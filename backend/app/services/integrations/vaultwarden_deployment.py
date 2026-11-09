"""OneTap 托管的 Vaultwarden 部署、状态与删除。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.docker.compose import DockerComposeProject
from app.models.integration import IntegrationConfig
from app.models.orchestration import AppInstance
from app.services.docker import docker_manager
from app.services.docker.host_network import detect_host_address
from app.services.integrations.vaultwarden import VaultwardenClient
from app.services.system.config_service import (
    resolve_username_for_app,
    system_config_service,
)
from app.services.system.port_allocator import (
    allocate_free_ports,
    get_used_ports,
    release_ports,
    reserve_ports,
)
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)

VAULTWARDEN_PROJECT = "onetap-vaultwarden"
VAULTWARDEN_CONTAINER = "onetap-vaultwarden"
VAULTWARDEN_SERVICE = "vaultwarden"
VAULTWARDEN_IMAGE = "vaultwarden/server:1.37.4"
VAULTWARDEN_KIND = "vaultwarden"

Progress = Callable[[str, int, str, str | None], None]


def _text(value: Any) -> str:
    return str(value or "").strip()


class VaultwardenDeploymentService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def _record(self, db: AsyncSession) -> IntegrationConfig:
        record = (
            await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.kind == VAULTWARDEN_KIND
                )
            )
        ).scalar_one_or_none()
        if record is None:
            record = IntegrationConfig(kind=VAULTWARDEN_KIND, config={}, status={})
            db.add(record)
            await db.flush()
        return record

    async def _container(self) -> dict[str, Any] | None:
        try:
            return await docker_manager.run_sync(
                docker_manager.get_container, VAULTWARDEN_CONTAINER
            )
        except Exception:
            logger.warning("读取 Vaultwarden 容器状态失败", exc_info=True)
            return None

    async def _host(self) -> str | None:
        client = docker_manager._client
        if client is None:
            return None
        try:
            return await asyncio.to_thread(detect_host_address, client)
        except Exception:
            logger.warning("检测 Vaultwarden 宿主机地址失败", exc_info=True)
            return None

    async def runtime_status(self, config: dict[str, Any]) -> dict[str, Any]:
        managed = bool(config.get("managed"))
        container = await self._container() if managed else None
        running = bool(container and container.get("status") == "running")
        host = await self._host() if managed else None
        port = config.get("web_port")
        try:
            port = int(port) if port is not None else None
        except (TypeError, ValueError):
            port = None
        return {
            "managed": managed,
            "running": running,
            "web_port": port,
            "web_url": f"http://{host}:{port}" if host and port else None,
        }

    async def start_deploy_managed(
        self,
        db: AsyncSession,
        *,
        email: str | None = None,
        password: str | None = None,
        web_port: int | None = None,
    ) -> str:
        from app.core.background import create_background_task
        from app.services.orchestration.deploy_task_service import deploy_task_manager

        record = await self._record(db)
        config = record.config or {}
        mail = _text(email) or _text(config.get("email"))
        secret = _text(password) or _text(config.get("password"))
        if not mail or not secret:
            system_config = await system_config_service.get_or_create(db)
            if not mail:
                unified_email = resolve_username_for_app(
                    VAULTWARDEN_KIND,
                    system_config.default_admin_username,
                    system_config.default_email_domain,
                )
                if "@" in unified_email:
                    mail = unified_email
            if not secret:
                secret = _text(system_config.default_admin_password)
        if not mail or "@" not in mail or not secret:
            raise APIException(
                "统一账号未完整配置邮箱后缀或密码，请填写 Vaultwarden 邮箱和主密码",
                400,
            )

        for task_id in deploy_task_manager.list_active_task_ids():
            task = deploy_task_manager.get_task(task_id) or {}
            if (task.get("meta") or {}).get("operation") == "vaultwarden_deploy":
                return task_id

        task_id = deploy_task_manager.create_task(
            "vaultwarden_deploy",
            meta={
                "app_name": "Vaultwarden",
                "instance_name": VAULTWARDEN_PROJECT,
                "description": "部署并接入 OneTap 认证专用密码库",
                "operation": "vaultwarden_deploy",
            },
            enforce_limit=False,
        )
        create_background_task(
            self._run_deploy_managed_task(
                task_id, mail, secret, web_port=web_port
            )
        )
        return task_id

    async def _run_deploy_managed_task(
        self,
        task_id: str,
        email: str,
        password: str,
        *,
        web_port: int | None,
    ) -> None:
        from app.database import async_session
        from app.services.orchestration.deploy_task_service import deploy_task_manager

        def progress(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            deploy_task_manager.update_progress(
                task_id, stage, percentage, message, detail
            )

        async with async_session() as db:
            try:
                await self.deploy_managed(
                    db,
                    email=email,
                    password=password,
                    web_port=web_port,
                    progress=progress,
                )
                deploy_task_manager.complete_task(
                    task_id, message="Vaultwarden 已部署并完成认证接入"
                )
            except Exception as exc:
                await db.rollback()
                logger.exception("Vaultwarden 部署任务失败: %s", task_id)
                deploy_task_manager.fail_task(task_id, str(exc))

    async def deploy_managed(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        web_port: int | None = None,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await self._deploy_managed_locked(
                db,
                email=email,
                password=password,
                web_port=web_port,
                progress=progress,
            )

    async def _deploy_managed_locked(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        web_port: int | None,
        progress: Progress | None,
    ) -> dict[str, Any]:
        def report(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            if progress is not None:
                progress(stage, percentage, message, detail)

        report("preparing", 3, "准备部署 Vaultwarden")
        pair = await storage_directory_service.get_app_data_pair(db)
        if not pair:
            raise APIException(
                "部署 Vaultwarden 需要先配置应用数据目录（系统设置 → 存储配置）",
                400,
            )
        host_base = pair[0].rstrip("/")
        record = await self._record(db)
        config = dict(record.config or {})
        was_managed = bool(config.get("managed"))
        selected_port = await self._choose_web_port(db, config, web_port)
        host = await self._host() or "127.0.0.1"
        web_url = f"http://{host}:{selected_port}"

        if not was_managed:
            existing = (
                await db.execute(
                    select(DockerComposeProject).where(
                        DockerComposeProject.project_name == VAULTWARDEN_PROJECT
                    )
                )
            ).scalar_one_or_none()
            if existing is not None or await self._container() is not None:
                raise APIException(
                    "已存在同名 Vaultwarden 项目或容器，为避免误删已停止部署", 400
                )

        reserve_ports([selected_port])
        project = None
        try:
            report("creating_project", 10, "创建 Vaultwarden 项目")
            await self._remove_project(db, backup_data=False)
            from app.services.compose import compose_manager

            project = await compose_manager.create_project_async(
                db,
                project_name=VAULTWARDEN_PROJECT,
                content=self._compose_content(
                    host_base=host_base,
                    web_url=web_url,
                    web_port=selected_port,
                    signups_allowed=not was_managed,
                ),
                description="OneTap 认证集成专用 Vaultwarden",
                allocate_ports=False,
                progress_callback=progress,
            )
        finally:
            release_ports([selected_port])

        report("initializing", 80, "等待 Vaultwarden 就绪")
        await self._wait_until_ready(web_url)
        async with VaultwardenClient(web_url) as client:
            if was_managed:
                session = await client.login(email, password)
            else:
                report("initializing", 86, "创建 Vaultwarden 账号")
                try:
                    await client.register(email, password)
                except APIException:
                    # 上次部署若在注册后中断，重试时直接验证已有账号。
                    session = await client.login(email, password)
                else:
                    session = await client.login(email, password)

        if not was_managed and project is not None:
            report("initializing", 92, "关闭公开注册")
            from app.services.compose import compose_manager

            await compose_manager.edit_and_deploy_async(
                db,
                project,
                content=self._compose_content(
                    host_base=host_base,
                    web_url=web_url,
                    web_port=selected_port,
                    signups_allowed=False,
                ),
                comment="完成账号初始化并关闭公开注册",
                progress_callback=progress,
            )

        record = await self._record(db)
        updated = dict(record.config or {})
        updated.update(
            {
                "managed": True,
                "url": web_url,
                "web_port": selected_port,
                "email": email,
                "password": password,
            }
        )
        record.config = updated
        record.enabled = True
        record.status = {
            **(record.status or {}),
            "ok": True,
            "message": "自动部署并连接成功",
            "version": session.version,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.commit()
        return {
            "deployed": True,
            "url": web_url,
            "version": session.version,
            "message": "Vaultwarden 已部署并完成认证接入",
        }

    async def _choose_web_port(
        self,
        db: AsyncSession,
        config: dict[str, Any],
        requested: int | None,
    ) -> int:
        used = get_used_ports(exclude_containers={VAULTWARDEN_CONTAINER})
        if requested is not None:
            if requested in used:
                raise APIException(f"端口 {requested} 已被占用", 400)
            return requested
        saved = config.get("web_port")
        if config.get("managed") and saved:
            saved_port = int(saved)
            if saved_port not in used:
                return saved_port
        system_config = await system_config_service.get_or_create(db)
        return allocate_free_ports(used, 1, system_config.service_port_start)[0]

    @staticmethod
    def _compose_content(
        *,
        host_base: str,
        web_url: str,
        web_port: int,
        signups_allowed: bool,
    ) -> str:
        data_path = Path(host_base) / VAULTWARDEN_PROJECT / "vaultwarden" / "data"
        content = {
            "services": {
                VAULTWARDEN_SERVICE: {
                    "image": VAULTWARDEN_IMAGE,
                    "container_name": VAULTWARDEN_CONTAINER,
                    "restart": "unless-stopped",
                    "environment": {
                        "DOMAIN": web_url,
                        "SIGNUPS_ALLOWED": "true" if signups_allowed else "false",
                        "TZ": "Asia/Shanghai",
                    },
                    "ports": [f"{web_port}:80"],
                    "volumes": [f"{data_path}:/data"],
                }
            }
        }
        return yaml.safe_dump(content, sort_keys=False, allow_unicode=True)

    async def _wait_until_ready(self, base_url: str, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=True, trust_env=False
        ) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"{base_url}/api/config")
                    if response.status_code < 400:
                        return
                    last_error = f"HTTP {response.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                await asyncio.sleep(2)
        raise APIException(f"Vaultwarden 启动后未就绪：{last_error}", 502)

    async def _remove_project(self, db: AsyncSession, *, backup_data: bool) -> None:
        from app.services.compose import compose_manager

        project = (
            await db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == VAULTWARDEN_PROJECT
                )
            )
        ).scalar_one_or_none()
        if project is not None:
            await compose_manager.delete_project(
                db, project, backup_data=backup_data
            )
            return

        container = await self._container()
        client = docker_manager._client
        if container is not None and client is not None:
            try:
                await docker_manager.run_sync(
                    lambda: client.containers.get(VAULTWARDEN_CONTAINER).remove(
                        force=True
                    )
                )
            except Exception as exc:
                raise APIException(f"清理遗留 Vaultwarden 容器失败：{exc}", 502) from exc

        if backup_data:
            from app.services.orchestration.backup import (
                AppDataEntry,
                backup_app_data_dirs,
            )

            await backup_app_data_dirs(
                db,
                [
                    AppDataEntry(
                        instance_name=VAULTWARDEN_PROJECT,
                        app_name=VAULTWARDEN_SERVICE,
                        project_name=VAULTWARDEN_PROJECT,
                    )
                ],
                group_name=VAULTWARDEN_PROJECT,
            )

    async def delete_managed(self, db: AsyncSession) -> dict[str, Any]:
        async with self._lock:
            record = await self._record(db)
            config = dict(record.config or {})
            if not config.get("managed"):
                raise APIException("当前不是 OneTap 托管的 Vaultwarden，不能自动删除", 400)
            await self._remove_project(db, backup_data=True)
            await db.execute(
                delete(AppInstance).where(
                    AppInstance.instance_name == VAULTWARDEN_PROJECT
                )
            )
            for key in ("managed", "url", "web_port", "vaultwarden_items"):
                config.pop(key, None)
            record.config = config
            record.enabled = False
            record.status = {}
            await db.commit()
            return {
                "removed": True,
                "message": "Vaultwarden 已删除，数据已备份到应用数据目录的 _backups 下",
            }


vaultwarden_deployment_service = VaultwardenDeploymentService()
