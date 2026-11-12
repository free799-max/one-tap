"""OneTap 托管的 Lucky 部署、状态与删除。"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tarfile
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
from app.services.integrations.lucky import LuckyClient, LuckyError
from app.services.system.config_service import system_config_service
from app.services.system.port_allocator import (
    allocate_free_ports,
    get_reserved_ports,
    get_used_ports,
    release_ports,
    reserve_ports,
)
from app.services.system.storage import StoragePathResolver
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)

LUCKY_PROJECT = "onetap-lucky"
LUCKY_CONTAINER = "onetap-lucky"
LUCKY_SERVICE = "lucky"
LUCKY_IMAGE = "gdy666/lucky:v3_lucky"
LUCKY_KIND = "network"
LUCKY_LEGACY_KIND = "lucky"
# Lucky 首次启动的默认管理员账号与端口
LUCKY_INITIAL_ACCOUNT = "666"
LUCKY_INITIAL_PASSWORD = "666"
DEFAULT_ADMIN_PORT = 16601

Progress = Callable[[str, int, str, str | None], None]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tree_readable(root: Path) -> bool:
    """目录下所有文件都能被当前进程读取时返回 True。"""
    try:
        for item in root.rglob("*"):
            if item.is_file() and not os.access(item, os.R_OK):
                return False
    except OSError:
        return False
    return True


def _copy_tree(source: Path, target: Path) -> None:
    for item in source.iterdir():
        dst = target / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)


def _deploy_options(
    host_base: str, mount_base: str, app_config: dict | None
) -> tuple[Path, Path, list[str], bool]:
    """从部署弹窗的配置里解析 Lucky 的配置目录、环境变量与特权模式。"""
    conf_host = Path(host_base) / LUCKY_PROJECT / "conf"
    conf_mount = Path(mount_base) / LUCKY_PROJECT / "conf"
    env = ["TZ=Asia/Shanghai"]
    privileged = False
    if not isinstance(app_config, dict):
        return conf_host, conf_mount, env, privileged

    resolver = StoragePathResolver(app_data=(host_base, mount_base))
    volumes = app_config.get("volumes")
    if isinstance(volumes, list):
        for item in volumes:
            if not isinstance(item, dict):
                continue
            if _text(item.get("container_path")) != "/app/conf":
                continue
            raw = _text(item.get("host_path"))
            if not raw:
                break
            if not raw.startswith("/"):
                raw = f"{LUCKY_PROJECT}/{raw.strip('/')}"
            conf_host = Path(resolver.to_host_path(raw))
            conf_mount = Path(resolver.to_container_path(raw))
            break

    env_rows = app_config.get("lucky_env")
    if isinstance(env_rows, list):
        parsed = [
            f"{_text(row.get('key'))}={_text(row.get('value'))}"
            for row in env_rows
            if isinstance(row, dict) and _text(row.get("key"))
        ]
        if parsed:
            env = parsed
    privileged = bool(app_config.get("privileged"))
    return conf_host, conf_mount, env, privileged


class LuckyDeploymentService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def _record(self, db: AsyncSession) -> IntegrationConfig:
        result = await db.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.kind.in_([LUCKY_KIND, LUCKY_LEGACY_KIND])
            )
        )
        records = result.scalars().all()
        record = next(
            (item for item in records if item.kind == LUCKY_KIND),
            records[0] if records else None,
        )
        if record is None:
            record = IntegrationConfig(kind=LUCKY_KIND, config={}, status={})
            db.add(record)
            await db.flush()
        return record

    @staticmethod
    def _lucky_block(config: dict | None) -> dict[str, Any]:
        return dict((config or {}).get("lucky") or {})

    async def _container(self) -> dict[str, Any] | None:
        try:
            return await docker_manager.run_sync(
                docker_manager.get_container, LUCKY_CONTAINER
            )
        except Exception:
            logger.warning("读取 Lucky 容器状态失败", exc_info=True)
            return None

    async def _host(self) -> str | None:
        client = docker_manager._client
        if client is None:
            return None
        try:
            return await asyncio.to_thread(detect_host_address, client)
        except Exception:
            logger.warning("检测 Lucky 宿主机地址失败", exc_info=True)
            return None

    async def _list_external(self) -> list[dict[str, Any]]:
        """找出不是 OneTap 托管的 Lucky 容器（按镜像识别）。"""
        try:
            containers = await docker_manager.run_sync(
                docker_manager.list_containers
            )
        except Exception:
            logger.warning("扫描 Lucky 容器失败", exc_info=True)
            return []
        return [
            item
            for item in containers
            if _text(item.get("image", "")).startswith("gdy666/lucky")
            and _text(item.get("name")) != LUCKY_CONTAINER
        ]

    async def runtime_status(self, config: dict[str, Any]) -> dict[str, Any]:
        managed = bool(config.get("managed"))
        container = await self._container() if managed else None
        running = bool(container and container.get("status") == "running")
        external = (await self._list_external()) if not managed else []
        try:
            port = int(config.get("web_port")) if config.get("web_port") else None
        except (TypeError, ValueError):
            port = None
        return {
            "managed": managed,
            "running": running,
            "external": len(external) == 1,
            "external_name": external[0].get("name") if len(external) == 1 else None,
            "web_port": port,
            "web_url": _text(config.get("url")) or None,
        }

    async def start_deploy_managed(
        self,
        db: AsyncSession,
        *,
        username: str | None = None,
        password: str | None = None,
        web_port: int | None = None,
        config: dict | None = None,
    ) -> str:
        from app.core.background import create_background_task
        from app.services.orchestration.deploy_task_service import (
            deploy_task_manager,
        )

        record = await self._record(db)
        lucky = self._lucky_block(record.config)
        account = _text(username) or _text(lucky.get("username"))
        secret = _text(password) or _text(lucky.get("password"))
        system_config = await system_config_service.get_or_create(db)
        if not account:
            account = _text(system_config.default_admin_username) or "admin"
            if "@" in account:
                account = account.split("@", 1)[0]
        if not secret:
            secret = _text(system_config.default_admin_password)
        if not secret:
            from app.services.orchestration.credentials import generate_password

            secret = generate_password()

        for task_id in deploy_task_manager.list_active_task_ids():
            task = deploy_task_manager.get_task(task_id) or {}
            if (task.get("meta") or {}).get("operation") == "lucky_deploy":
                return task_id

        task_id = deploy_task_manager.create_task(
            "lucky_deploy",
            meta={
                "app_name": "Lucky",
                "instance_name": LUCKY_PROJECT,
                "description": "部署并接入 OneTap 访问入口",
                "operation": "lucky_deploy",
            },
            enforce_limit=False,
        )
        create_background_task(
            self._run_deploy_managed_task(
                task_id, account, secret, web_port=web_port, app_config=config
            )
        )
        return task_id

    async def _run_deploy_managed_task(
        self,
        task_id: str,
        username: str,
        password: str,
        *,
        web_port: int | None,
        app_config: dict | None = None,
    ) -> None:
        from app.database import async_session
        from app.services.orchestration.deploy_task_service import (
            deploy_task_manager,
        )

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
                    username=username,
                    password=password,
                    web_port=web_port,
                    app_config=app_config,
                    progress=progress,
                )
                deploy_task_manager.complete_task(
                    task_id, message="Lucky 已部署并接入网络方案"
                )
            except Exception as exc:
                await db.rollback()
                logger.exception("Lucky 部署任务失败: %s", task_id)
                deploy_task_manager.fail_task(task_id, str(exc))

    async def deploy_managed(
        self,
        db: AsyncSession,
        *,
        username: str,
        password: str,
        web_port: int | None = None,
        app_config: dict | None = None,
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await self._deploy_managed_locked(
                db,
                username=username,
                password=password,
                web_port=web_port,
                app_config=app_config,
                progress=progress,
            )

    async def _deploy_managed_locked(
        self,
        db: AsyncSession,
        *,
        username: str,
        password: str,
        web_port: int | None,
        progress: Progress | None,
        app_config: dict | None = None,
    ) -> dict[str, Any]:
        def report(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            if progress is not None:
                progress(stage, percentage, message, detail)

        report("preparing", 3, "准备部署 Lucky")
        pair = await storage_directory_service.get_app_data_pair(db)
        if not pair:
            raise APIException(
                "部署 Lucky 需要先配置应用数据目录（系统设置 → 存储配置）",
                400,
            )
        host_base = pair[0].rstrip("/")
        mount_base = pair[1].rstrip("/")
        record = await self._record(db)
        config = dict(record.config or {})
        lucky = self._lucky_block(config)
        was_managed = bool(lucky.get("managed"))
        saved_username = _text(lucky.get("username"))
        saved_password = _text(lucky.get("password"))
        conf_path, conf_mount_path, env, privileged = _deploy_options(
            host_base, mount_base, app_config
        )

        if not was_managed:
            existing = (
                await db.execute(
                    select(DockerComposeProject).where(
                        DockerComposeProject.project_name == LUCKY_PROJECT
                    )
                )
            ).scalar_one_or_none()
            if existing is not None or await self._container() is not None:
                raise APIException(
                    "已存在同名 Lucky 项目或容器，为避免误删已停止部署",
                    400,
                )

        report("preparing", 6, "检查现有 Lucky 数据")
        migrated = await self._prepare_conf(db, conf_mount_path, conf_path)

        selected_port = await self._choose_admin_port(db, lucky, web_port)
        reserved: list[int] = [selected_port]
        reserve_ports(reserved)
        try:
            # 记录本次端口，失败后重试可以沿用同一端口
            lucky["web_port"] = selected_port
            config["lucky"] = lucky
            record.config = config
            await db.commit()

            report("preparing", 8, "清理旧容器")
            await self._remove_project(db, backup_data=False)

            host = await self._host() or "127.0.0.1"
            web_url = f"http://{host}:{selected_port}"
            probe_port = await self._choose_probe_port(selected_port)
            reserved.append(probe_port)
            reserve_ports([probe_port])

            report("creating_project", 15, "创建 Lucky 初始化容器")
            from app.services.compose import compose_manager

            await compose_manager.create_project_async(
                db,
                project_name=LUCKY_PROJECT,
                content=self._compose_content(
                    conf_path=str(conf_path),
                    ports=[
                        f"{selected_port}:{selected_port}",
                        f"{probe_port}:{DEFAULT_ADMIN_PORT}"
                    ],
                    env=env,
                    privileged=privileged,
                ),
                description="OneTap 访问入口专用 Lucky（初始化）",
                allocate_ports=False,
                progress_callback=progress,
            )

            report("initializing", 45, "等待 Lucky 就绪")
            reachable_url = await self._wait_until_ready(
                [
                    f"http://{host}:{selected_port}",
                    f"http://{host}:{probe_port}",
                ]
            )

            report("initializing", 58, "设置 Lucky 管理账号")
            used_account, used_password = await self._configure_admin(
                reachable_url,
                username=username,
                password=password,
                listen_port=selected_port,
                candidates=self._credential_candidates(
                    was_managed,
                    migrated,
                    saved_username,
                    saved_password,
                    username,
                    password,
                ),
            )
            logger.info("Lucky 管理员账号已由 %s 更新为 %s", used_account, username)

            report("applying", 72, "切换 Lucky 到 host 网络")
            await self._remove_project(db, backup_data=False)

            report("applying", 78, "启动 Lucky")
            await compose_manager.create_project_async(
                db,
                project_name=LUCKY_PROJECT,
                content=self._compose_content(
                    conf_path=str(conf_path),
                    ports=None,
                    env=env,
                    privileged=privileged,
                ),
                description="OneTap 访问入口专用 Lucky",
                allocate_ports=False,
                progress_callback=progress,
            )

            report("verifying", 90, "验证 Lucky 连接")
            await self._wait_until_ready([web_url])
            async with LuckyClient(web_url, username, password) as client:
                version = await client.test()
        except Exception:
            try:
                await self._remove_project(db, backup_data=False)
            except Exception:
                logger.warning("清理失败的 Lucky 项目出错", exc_info=True)
            raise
        finally:
            release_ports(reserved)

        record = await self._record(db)
        config = _ensure_lucky_config(record.config)
        lucky = dict(config["lucky"])
        lucky.update(
            {
                "url": web_url,
                "username": username,
                "password": password,
                "managed": True,
                "web_port": selected_port,
            }
        )
        lucky.setdefault("listen_port", 443)
        config["lucky"] = lucky
        config["provider"] = "lucky"
        record.config = config
        record.enabled = True
        record.status = {
            **(record.status or {}),
            "ok": True,
            "message": "自动部署并连接成功",
            "version": version,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.commit()
        return {
            "deployed": True,
            "url": web_url,
            "version": version,
            "message": "Lucky 已部署并接入网络方案",
        }

    async def _choose_admin_port(
        self,
        db: AsyncSession,
        lucky: dict[str, Any],
        requested: int | None,
    ) -> int:
        used = get_used_ports(exclude_containers={LUCKY_CONTAINER}) | get_reserved_ports()
        if requested is not None:
            if requested in used:
                raise APIException(f"端口 {requested} 已被占用", 400)
            return requested
        if lucky.get("managed") and lucky.get("web_port"):
            try:
                return int(lucky["web_port"])
            except (TypeError, ValueError):
                pass
        saved = lucky.get("web_port")
        if saved:
            try:
                saved_port = int(saved)
            except (TypeError, ValueError):
                saved_port = 0
            if saved_port and saved_port not in used:
                return saved_port
        if DEFAULT_ADMIN_PORT not in used:
            return DEFAULT_ADMIN_PORT
        system_config = await system_config_service.get_or_create(db)
        return allocate_free_ports(used, 1, system_config.service_port_start)[0]

    async def _choose_probe_port(self, selected: int) -> int:
        used = get_used_ports(exclude_containers={LUCKY_CONTAINER}) | {selected}
        return allocate_free_ports(used, 1, DEFAULT_ADMIN_PORT + 1)[0]

    @staticmethod
    def _credential_candidates(
        was_managed: bool,
        migrated: bool,
        saved_username: str,
        saved_password: str,
        username: str,
        password: str,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        if was_managed and saved_username and saved_password:
            candidates.append((saved_username, saved_password))
        if not was_managed and not migrated:
            candidates.append(
                (LUCKY_INITIAL_ACCOUNT, LUCKY_INITIAL_PASSWORD)
            )
        if saved_username and saved_password:
            candidates.append((saved_username, saved_password))
        if username and password:
            candidates.append((username, password))
        candidates.append((LUCKY_INITIAL_ACCOUNT, LUCKY_INITIAL_PASSWORD))
        seen: set[tuple[str, str]] = set()
        return [item for item in candidates if not (item in seen or seen.add(item))]

    async def _prepare_conf(
        self, db: AsyncSession, conf_mount_path: Path, conf_host_path: Path
    ) -> bool:
        """部署前准备配置目录，沿用现成数据。

        与 AdGuard Home 的迁移逻辑一致：优先接管正在运行的外部 Lucky
        （复制容器内配置后删掉旧容器），其次从最近一次删除备份恢复，
        避免「部署出来一个空白实例」。
        """
        if (conf_mount_path / "lucky_base.lkcf").exists():
            return False

        external = await self._list_external()
        if len(external) > 1:
            raise APIException(
                "检测到多个 Lucky 容器，请先在 Compose 页面删除多余的实例",
                400,
            )
        if len(external) == 1:
            name = _text(external[0].get("name"))
            try:
                await self._copy_container_conf(name, conf_mount_path)
            except Exception:
                await self._start_container(name)
                raise
            await self._remove_external(db, external[0])
            logger.info("已将外部 Lucky 容器 %s 的配置迁移到托管实例", name)
            return True

        return await self._restore_latest_backup(
            db, conf_mount_path, conf_host_path
        )

    async def _restore_latest_backup(
        self, db: AsyncSession, conf_mount_path: Path, conf_host_path: Path
    ) -> bool:
        """从 _backups 里最近一次 Lucky 备份恢复配置。"""
        pair = await storage_directory_service.get_app_data_pair(db)
        if not pair:
            return False
        backups_root = Path(pair[1]) / "_backups"
        if not backups_root.exists():
            return False
        candidates = [
            (
                1 if path.parent.name == "conf" else 0,
                path.stat().st_mtime,
                path.parent,
            )
            for path in backups_root.rglob("lucky_base.lkcf")
            if path.parent.name in ("conf", "config")
        ]
        if not candidates:
            return False
        _, _, source = max(candidates, key=lambda item: (item[0], item[1]))
        host_target = conf_host_path
        host_source = (
            Path(str(pair[0]).rstrip("/"))
            / "_backups"
            / source.relative_to(backups_root)
        )
        await self._copy_conf_tree(source, conf_mount_path, host_source, host_target)
        logger.info("已从备份 %s 恢复 Lucky 配置", source)
        return True

    async def _copy_conf_tree(
        self, source: Path, target: Path, host_source: Path, host_target: Path
    ) -> None:
        """复制 Lucky 配置目录，必要时借助容器以 root 权限读取。

        Lucky 容器以 root 运行，配置目录里的 ``lucky_base.lkcf`` 等文件
        常常是 root 属主且 0600 权限，后端进程（普通用户）无法直接读取。
        这时改用一次性容器（镜像与 Lucky 相同）复制，并把结果 chown 给
        当前进程，避免后续备份再次遇到权限问题。
        """
        target.mkdir(parents=True, exist_ok=True)
        if _tree_readable(source):
            await asyncio.to_thread(_copy_tree, source, target)
            return

        client = docker_manager._client
        if client is None:
            raise APIException("Docker 不可用，无法读取 Lucky 备份配置", 502)
        uid, gid = os.getuid(), os.getgid()
        script = f"cp -a /src/. /dst/ && chown -R {uid}:{gid} /dst"

        def _run() -> None:
            client.containers.run(
                LUCKY_IMAGE,
                command=["-c", script],
                entrypoint="sh",
                volumes={
                    str(host_source): {"bind": "/src", "mode": "ro"},
                    str(host_target): {"bind": "/dst", "mode": "rw"},
                },
                remove=True,
            )

        try:
            await docker_manager.run_sync(_run)
        except Exception as exc:
            raise APIException(f"恢复 Lucky 备份配置失败：{exc}", 500) from exc

    async def _configure_admin(
        self,
        base_url: str,
        *,
        username: str,
        password: str,
        listen_port: int,
        candidates: list[tuple[str, str]],
    ) -> tuple[str, str]:
        last_error: Exception | None = None
        for account, secret in candidates:
            try:
                async with LuckyClient(base_url, account, secret) as client:
                    await client.login()
                    await client.update_admin(
                        account=username,
                        password=password,
                        old_password=secret,
                        listen_port=listen_port,
                        disable_tls=True,
                    )
                    return account, secret
            except (LuckyError, APIException) as exc:
                last_error = exc
        raise APIException(
            f"无法登录 Lucky 初始化容器：{last_error}", 502
        )

    @staticmethod
    def _compose_content(
        *,
        conf_path: str,
        ports: list[str] | None,
        env: list[str] | None = None,
        privileged: bool = False,
    ) -> str:
        service: dict[str, Any] = {
            "image": LUCKY_IMAGE,
            "container_name": LUCKY_CONTAINER,
            "environment": env or ["TZ=Asia/Shanghai"],
            "volumes": [f"{conf_path}:/app/conf"],
        }
        if privileged:
            service["privileged"] = True
        if ports:
            service["ports"] = ports
            service["restart"] = "no"
        else:
            service["network_mode"] = "host"
            service["restart"] = "unless-stopped"
        return yaml.safe_dump(
            {"services": {LUCKY_SERVICE: service}},
            sort_keys=False,
            allow_unicode=True,
        )

    async def _wait_until_ready(
        self, base_urls: list[str], timeout: float = 150.0
    ) -> str:
        deadline = time.monotonic() + timeout
        last_error = ""
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=True, trust_env=False
        ) as client:
            while time.monotonic() < deadline:
                for base_url in base_urls:
                    try:
                        response = await client.get(f"{base_url}/version")
                        if response.status_code < 400:
                            return base_url
                        last_error = f"HTTP {response.status_code}"
                    except httpx.HTTPError as exc:
                        last_error = str(exc)
                await asyncio.sleep(2)
        raise APIException(f"Lucky 启动后未就绪：{last_error}", 502)

    async def _remove_project(self, db: AsyncSession, *, backup_data: bool) -> None:
        from app.services.compose import compose_manager

        project = (
            await db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == LUCKY_PROJECT
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
                    lambda: client.containers.get(LUCKY_CONTAINER).remove(
                        force=True
                    )
                )
            except Exception as exc:
                raise APIException(f"清理遗留 Lucky 容器失败：{exc}", 502) from exc

        if backup_data:
            from app.services.orchestration.backup import (
                AppDataEntry,
                backup_app_data_dirs,
            )

            await backup_app_data_dirs(
                db,
                [
                    AppDataEntry(
                        instance_name=LUCKY_PROJECT,
                        app_name=LUCKY_SERVICE,
                        project_name=LUCKY_PROJECT,
                    )
                ],
                group_name=LUCKY_PROJECT,
            )

    async def _start_container(self, name: str) -> None:
        client = docker_manager._client
        if client is None or not name:
            return
        try:
            await docker_manager.run_sync(
                lambda: client.containers.get(name).start()
            )
        except Exception:
            logger.warning("恢复 Lucky 容器运行失败", exc_info=True)

    async def _extract_container_conf(
        self, container_name: str, dest: Path
    ) -> Path:
        """停容器并把 /app/conf（v3）或 /goodluck（v2）解包到 dest。"""
        client = docker_manager._client
        if client is None or not container_name:
            raise APIException("Docker 不可用，无法读取 Lucky 配置", 502)
        dest.mkdir(parents=True, exist_ok=True)

        def _stop() -> None:
            client.containers.get(container_name).stop(timeout=10)

        def _extract() -> Path | None:
            container = client.containers.get(container_name)
            for path in ("/app/conf", "/goodluck"):
                try:
                    stream, _stat = container.get_archive(path)
                    raw = b"".join(stream)
                    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                        archive.extractall(dest, filter="data")
                    break
                except Exception:
                    continue
            else:
                return None
            for found in dest.rglob("lucky_base.lkcf"):
                return found.parent
            return None

        try:
            await docker_manager.run_sync(_stop)
        except Exception:
            logger.warning("停止 Lucky 容器失败，继续尝试读取配置", exc_info=True)
        source = await docker_manager.run_sync(_extract)
        if source is None:
            await self._start_container(container_name)
            raise APIException("无法读取 Lucky 容器内配置", 502)
        return source

    async def _copy_container_conf(
        self, container_name: str, target: Path
    ) -> None:
        """把容器内配置迁移到托管数据目录。"""
        staging = target.parent / f".{target.name}.migrate"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            source = await self._extract_container_conf(container_name, staging)
            target.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                dst = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def _backup_container_conf(
        self, db: AsyncSession, container_name: str
    ) -> None:
        """把容器内配置复制到备份目录，删除容器前调用。"""
        if not container_name:
            return
        pair = await storage_directory_service.get_app_data_pair(db)
        if not pair:
            raise APIException(
                "删除 Lucky 前需要先配置应用数据目录，用于备份容器内配置",
                400,
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = Path(pair[1]) / "_backups" / f"{stamp}-{container_name}"
        await self._extract_container_conf(container_name, dest)

    async def _remove_external(
        self, db: AsyncSession, container: dict[str, Any]
    ) -> None:
        name = _text(container.get("name"))
        project_name = _text(
            (container.get("labels") or {}).get("com.docker.compose.project")
        )
        if project_name:
            project = (
                await db.execute(
                    select(DockerComposeProject).where(
                        DockerComposeProject.project_name == project_name
                    )
                )
            ).scalar_one_or_none()
            if project is not None:
                from app.services.compose import compose_manager

                await compose_manager.delete_project(
                    db, project, backup_data=True
                )
                return

        client = docker_manager._client
        if client is None or not name:
            raise APIException("Docker 不可用，无法删除 Lucky 容器", 502)
        try:
            await docker_manager.run_sync(
                lambda: client.containers.get(name).remove(force=True)
            )
        except Exception as exc:
            raise APIException(f"删除 Lucky 容器失败：{exc}", 502) from exc

    async def delete_instance(self, db: AsyncSession) -> dict[str, Any]:
        """删除 OneTap 托管的 Lucky，或识别到的外部 Lucky 容器。"""
        async with self._lock:
            record = await self._record(db)
            config = _ensure_lucky_config(record.config)
            lucky = dict(config["lucky"])
            if lucky.get("managed"):
                await self._remove_project(db, backup_data=True)
                await db.execute(
                    delete(AppInstance).where(
                        AppInstance.instance_name == LUCKY_PROJECT
                    )
                )
            else:
                external = await self._list_external()
                if not external:
                    raise APIException(
                        "未找到可删除的 Lucky 容器，请在 Compose 页面确认",
                        400,
                    )
                if len(external) > 1:
                    raise APIException(
                        "检测到多个 Lucky 容器，请在 Compose 页面逐个删除",
                        400,
                    )
                container = external[0]
                name = _text(container.get("name"))
                await self._backup_container_conf(db, name)
                try:
                    await self._remove_external(db, container)
                except Exception:
                    await self._start_container(name)
                    raise

            for key in ("managed", "url", "web_port"):
                lucky.pop(key, None)
            config["lucky"] = lucky
            record.config = config
            record.enabled = False
            record.status = {}
            await db.commit()
            return {
                "removed": True,
                "message": "Lucky 已删除，配置数据已备份到应用数据目录的 _backups 下",
            }


def _ensure_lucky_config(raw: dict | None) -> dict[str, Any]:
    """补齐网络集成配置里的 Lucky 结构。"""
    config = dict(raw or {})
    config.setdefault("lucky", {})
    return config


lucky_deployment_service = LuckyDeploymentService()
