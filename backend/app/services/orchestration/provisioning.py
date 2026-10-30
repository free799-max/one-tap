"""应用编排部署后账号自动配置（provisioning）能力。"""

import asyncio
import logging
import re
import time
from urllib.parse import urlparse

import docker

from app.core.background import create_background_task
from app.core.exceptions import APIException
from app.database import async_session
from app.models.orchestration import AppInstance
from app.services.apps import ProvisionResult, get_client
from app.services.docker import docker_manager
from app.services.orchestration.deploy_task_service import deploy_task_manager

logger = logging.getLogger(__name__)

# 清理容器日志里的 ANSI 颜色/光标控制序列，避免进度消息出现乱码
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# music-dl 下载目录候选（容器内路径）：优先写入 Music Tag Web 可见的
# 下载目录，其次为音乐自动化共享根目录下的 downloads。
_MUSIC_DOWNLOAD_DIR_CANDIDATES = (
    "/app/media/downloads",
    "/mnt/music/downloads",
    "/app/media/music",
    "/mnt/music/library",
)


def _apply_basic_auth_type(entry: dict) -> None:
    """自动写入账号密码时补齐认证方式。

    只补空缺或「无认证」，不覆盖用户显式选择的 API Key：
    自动配置成功不代表要改掉用户设定的认证方式。
    """
    if entry.get("auth_type") in (None, "", "none"):
        entry["auth_type"] = "basic"


class ProvisionMixin:
    """部署任务完成后把统一账号配置进真实服务的能力。"""

    # 首次配置失败后的自动重试等待（秒）。仅当容器仍在运行时重试，
    # 用于覆盖首次启动极慢（如 MoviePilot 下载浏览器内核）的场景。
    PROVISION_RETRY_DELAYS = (60, 180, 300)
    # 长时间等待服务就绪时刷新进度的间隔（秒），避免跟踪任务被判超时
    PROVISION_HEARTBEAT_SECONDS = 15.0

    def _schedule_provisioning(
        self,
        group_id: int,
        instance_name: str,
        selected_apps: list[str],
        task_ids: list[str],
        pending_credentials: dict[str, dict],
    ) -> str | None:
        """为部署后的账号自动配置启动后台协程。

        每个待配置应用等待其部署任务结束后，由应用客户端把统一
        账号密码配置进真实服务并复验，结果写入 group.app_configs。

        Returns:
            进度跟踪任务 ID；没有待配置应用时返回 None
        """
        # 延迟导入：避免 app_store -> orchestration -> provisioning 的循环依赖
        from app.services.app_store.app_service import _slugify

        items = []
        for app_name, task_id in zip(selected_apps, task_ids, strict=True):
            creds = pending_credentials.get(app_name)
            if not creds:
                continue
            items.append(
                {
                    "app_name": app_name,
                    "task_id": task_id,
                    "project_name": _slugify(f"{instance_name}-{app_name}"),
                    "credentials": creds,
                }
            )
        if not items:
            return None

        # 部署后的配置与装配耗时较长，单独建一个跟踪任务让前端继续
        # 展示进度，避免用户误以为部署完成后没有自动配置
        progress_task_id = deploy_task_manager.create_task(
            "orchestration_provision",
            meta={
                "instance_name": instance_name,
                "orchestration_group_id": group_id,
                "description": "自动配置账号与应用集成",
                "operation": "provision",
            },
            enforce_limit=False,
        )
        create_background_task(
            self._provision_group(group_id, items, progress_task_id)
        )
        return progress_task_id

    async def _wait_task_terminal(self, task_id: str, timeout: float = 1800.0) -> str:
        """轮询部署任务直到终态，返回 completed / failed。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            task = deploy_task_manager.get_task(task_id)
            if task is None or task.get("status") != "deploying":
                return (task or {}).get("status") or "failed"
            await asyncio.sleep(2)
        return "failed"

    async def _provision_group(
        self,
        group_id: int,
        items: list[dict],
        progress_task_id: str | None = None,
    ) -> None:
        """逐个应用执行账号自动配置并落库，失败只记录不影响部署结果。

        同时把等待部署、配置账号、自动装配的阶段推送到进度任务，
        前端在部署进度弹窗中持续展示，直到配置与装配全部结束。
        首次配置失败的容器若仍在运行，会按 ``PROVISION_RETRY_DELAYS``
        自动重试，避免首次启动过慢导致永久停在上次失败状态。
        """
        total = len(items)
        deploy_failed: list[str] = []
        pending = list(items)

        def report(stage: str, percentage: int, message: str) -> None:
            if progress_task_id:
                deploy_task_manager.update_progress(
                    progress_task_id, stage, percentage, message
                )

        try:
            report("waiting_services", 5, f"等待应用部署完成（0/{total}）")
            # 先落库凭据：即使配置失败（或后端重启中断协程），
            # 用户点「重新装配」时仍能用同一套账号密码重试
            await self._persist_pending_credentials(group_id, items)

            for attempt in range(len(self.PROVISION_RETRY_DELAYS) + 1):
                if attempt > 0:
                    if not pending:
                        break
                    running = [
                        item
                        for item in pending
                        if await asyncio.to_thread(
                            self._project_running, item["project_name"]
                        )
                    ]
                    if not running:
                        break
                    pending = running
                    delay = self.PROVISION_RETRY_DELAYS[attempt - 1]
                    report(
                        "provisioning",
                        85,
                        f"{delay // 60} 分钟后重试 "
                        f"{len(pending)} 个未配置应用",
                    )
                    await asyncio.sleep(delay)

                round_failed: list[dict] = []
                for index, item in enumerate(pending, start=1):
                    app_name = item["app_name"]
                    creds = item["credentials"]
                    base_message = (
                        f"自动配置账号：{app_name}（{index}/{len(pending)}）"
                    )
                    try:
                        if attempt == 0:
                            status = await self._wait_task_terminal(
                                item["task_id"]
                            )
                            if status != "completed":
                                deploy_failed.append(app_name)
                                report(
                                    "provisioning",
                                    10
                                    + int(75 * index / max(len(pending), 1)),
                                    f"跳过 {app_name}：部署未成功",
                                )
                                await self._write_provision_result(
                                    group_id, app_name, creds, None,
                                    "部署未成功，跳过账号自动配置",
                                )
                                continue

                        report(
                            "provisioning",
                            10 + int(75 * (index - 1) / max(len(pending), 1)),
                            base_message,
                        )
                        async with async_session() as db:
                            group = await self.get_group(db, group_id)
                            entry = (group.app_configs or {}).get(app_name) or {}
                            url = entry.get("url")

                        def heartbeat(
                            message: str,
                            *,
                            _percentage=10
                            + int(75 * (index - 1) / max(len(pending), 1)),
                        ) -> None:
                            report("provisioning", _percentage, message)

                        result = await self._provision_app(
                            group_id,
                            app_name,
                            creds,
                            url,
                            item["project_name"],
                            progress=heartbeat,
                        )
                        if result is not None and not result.ok:
                            round_failed.append(item)
                    except Exception:
                        round_failed.append(item)
                        logger.exception(
                            "应用 %s 账号自动配置调度失败", app_name
                        )
                    report(
                        "provisioning",
                        10 + int(75 * index / max(len(pending), 1)),
                        f"自动配置账号完成（{index}/{len(pending)}）",
                    )
                pending = round_failed

            failed = deploy_failed + [item["app_name"] for item in pending]

            # 所有应用账号配置完毕后，按模板 integrations 声明把来源应用
            # （下载器/媒体服务器）自动装配进目标应用（如 MoviePilot）。
            # 注：无任何待配置应用时 _schedule_provisioning 不会调度本协程，
            # 该场景下自动装配也不触发（没有已配置应用可装配），属预期行为。
            report("wiring", 90, "装配应用集成（下载器/媒体服务器）")
            try:
                await self._wire_group(group_id)
            except Exception:
                logger.exception("实例组 %s 自动装配调度失败", group_id)

            if progress_task_id:
                if failed:
                    deploy_task_manager.complete_task(
                        progress_task_id,
                        message=(
                            f"自动配置完成，{len(failed)} 个应用未配置成功，"
                            "可在应用详情中重新装配"
                        ),
                    )
                else:
                    deploy_task_manager.complete_task(
                        progress_task_id,
                        message="账号与集成已自动配置完成",
                    )
        except Exception as exc:
            logger.exception("实例组 %s 自动配置任务异常", group_id)
            if progress_task_id:
                deploy_task_manager.fail_task(progress_task_id, str(exc))

    async def _persist_pending_credentials(
        self,
        group_id: int,
        items: list[dict],
    ) -> None:
        """把待配置凭据提前写入 app_configs，保证失败后仍可复用重试。

        已存在的用户名（如 Komga 使用邮箱）和密码保持不变：目标密码要等
        自动配置成功后才生效，提前覆盖会让「旧密码登录后改密」的重试链路
        丢失当前有效密码。
        """
        async with async_session() as db:
            group = await self.get_group(db, group_id)
            configs = dict(group.app_configs or {})
            for item in items:
                app_name = item["app_name"]
                creds = item["credentials"]
                entry = dict(configs.get(app_name) or {})
                if not entry.get("username"):
                    entry["username"] = creds["username"]
                _apply_basic_auth_type(entry)
                if not entry.get("password"):
                    entry["password"] = creds["password"]
                if creds.get("api_key"):
                    entry["api_key"] = creds["api_key"]
                # 应用刚部署/重建，需要重新验证配置；清掉上一轮的结果，
                # 避免等待期间误显示「已配置」或被手动重跑跳过
                entry.pop("provisioned", None)
                entry.pop("provision_error", None)
                configs[app_name] = entry
            group.app_configs = configs
            await db.commit()

    async def _resolve_music_download_dir(self, group_id: int) -> str | None:
        """从音乐自动化共享目录中选出 music-dl 的下载目录。

        music-dl 的 ``downloadDir`` 必须落在共享卷内，下载完成的歌曲才能
        被 Music Tag Web / Navidrome 看到。优先使用显式挂载到容器的
        downloads 目录；仅挂载共享根目录时使用其 downloads 子目录。
        """
        async with async_session() as db:
            group = await self.get_group(db, group_id)
            shared_config = group.shared_config or {}

        mounted = {
            str(volume.get("container_path") or "").rstrip("/")
            for volume in shared_config.get("volumes") or []
            if isinstance(volume, dict)
        }
        for path in _MUSIC_DOWNLOAD_DIR_CANDIDATES:
            if path in mounted:
                return path
        if "/mnt/music" in mounted:
            return "/mnt/music/downloads"
        return None

    async def _provision_app(
        self,
        group_id: int,
        app_name: str,
        creds: dict,
        url: str,
        project_name: str,
        progress=None,
    ) -> ProvisionResult:
        """对单个应用执行账号自动配置并落库。

        Args:
            progress: 可选回调，长时间等待服务就绪时用于刷新进度消息
        """
        client = self._resolve_provision_client(app_name)
        if client is None or not url:
            message = "该应用不支持自动配置"
            await self._write_provision_result(
                group_id, app_name, creds, None, message
            )
            return ProvisionResult(ok=False, message=message)

        logs_reader = self._make_logs_reader(project_name)
        host_header_port = None
        if app_name == "qbittorrent":
            # Host 头端口与 WebUI 监听端口不一致时 qb 5.x 会 401，
            # 需要容器内端口伪装 Host 头完成配置
            url_port = urlparse(url).port
            if url_port:
                try:
                    host_header_port = await asyncio.to_thread(
                        self._get_container_port, project_name, url_port
                    )
                except Exception:
                    logger.warning(
                        "获取 %s 容器端口失败", project_name, exc_info=True
                    )

        download_dir = None
        if app_name == "music-dl":
            download_dir = await self._resolve_music_download_dir(group_id)

        command_runner = None
        if app_name == "music-tag-web":
            # 免费版用户管理接口返回 403，改密需在容器内执行 Django 命令
            command_runner = self._make_command_runner(project_name)

        provision_kwargs = {
            "url": url,
            "username": creds["username"],
            "password": creds["password"],
            "api_key": creds.get("api_key"),
            "logs_reader": logs_reader,
            "previous_password": creds.get("previous_password"),
            "host_header_port": host_header_port,
        }
        if download_dir:
            provision_kwargs["download_dir"] = download_dir
        if command_runner:
            provision_kwargs["command_runner"] = command_runner
        try:
            # provision 内部会轮询等待服务就绪，耗时可达数十分钟，
            # 因此不持有数据库会话执行；等待期间按心跳刷新进度
            result = await self._await_provision(
                client.provision(**provision_kwargs),
                app_name=app_name,
                logs_reader=logs_reader,
                status_getter=lambda: getattr(client, "last_status", None),
                progress=progress,
            )
        except Exception as exc:
            logger.exception("应用 %s 账号自动配置异常", app_name)
            result = ProvisionResult(ok=False, message=f"自动配置异常: {exc}")
        await self._write_provision_result(
            group_id, app_name, creds, result, result.message
        )
        return result

    async def _await_provision(
        self,
        coro,
        app_name: str,
        logs_reader,
        progress=None,
        status_getter=None,
    ) -> ProvisionResult:
        """等待 provision 完成，期间定时刷新进度并附带等待原因。"""
        task = asyncio.create_task(coro)
        started = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait(
                    {task}, timeout=self.PROVISION_HEARTBEAT_SECONDS
                )
                if task in done:
                    return task.result()
                if progress is None:
                    continue
                waited = int(time.monotonic() - started)
                message = (
                    f"等待 {app_name} 服务就绪"
                    f"（已等待 {waited // 60} 分 {waited % 60} 秒）"
                )
                # 优先展示客户端实际等待原因（HTTP 状态码/连接失败等），
                # 容器日志尾行作为兜底（如镜像拉取阶段）
                note = ""
                note_prefix = ""
                if status_getter is not None:
                    try:
                        note = (status_getter() or "").strip()
                    except Exception:
                        note = ""
                if not note:
                    note = await self._last_log_line(logs_reader)
                    # 标注来源，避免把应用自身日志误当成平台操作
                    if note:
                        note_prefix = "应用日志："
                if note:
                    message = f"{message}：{note_prefix}{note[:160]}"
                progress(message)
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _last_log_line(self, logs_reader) -> str:
        """读取容器日志最后一行，失败时返回空串。"""
        if logs_reader is None:
            return ""
        try:
            logs = await logs_reader()
        except Exception:
            return ""
        for line in reversed((logs or "").splitlines()):
            cleaned = _ANSI_ESCAPE.sub("", line).strip()
            if cleaned:
                return cleaned[:120]
        return ""

    async def _reprovision_and_wire(
        self,
        group_id: int,
        progress_task_id: str | None = None,
    ) -> None:
        """手动重跑：先补做未完成的账号自动配置，再执行自动装配。"""

        def report(stage: str, percentage: int, message: str) -> None:
            if progress_task_id:
                deploy_task_manager.update_progress(
                    progress_task_id, stage, percentage, message
                )

        try:
            report("provisioning", 5, "补做未完成的账号自动配置")
            try:
                await self._reprovision_pending(
                    group_id,
                    progress=lambda message: report(
                        "provisioning", 50, message
                    ),
                )
            except Exception:
                logger.exception("实例组 %s 补做账号自动配置失败", group_id)

            report("wiring", 85, "装配应用集成（下载器/媒体服务器）")
            try:
                await self._wire_group(group_id)
            except Exception:
                logger.exception("实例组 %s 自动装配调度失败", group_id)

            if progress_task_id:
                remaining = await self._count_unprovisioned(group_id)
                if remaining:
                    deploy_task_manager.complete_task(
                        progress_task_id,
                        message=(
                            f"重新装配完成，仍有 {remaining} 个应用未配置成功"
                        ),
                    )
                else:
                    deploy_task_manager.complete_task(
                        progress_task_id,
                        message="账号与集成已重新装配完成",
                    )
        except Exception as exc:
            logger.exception("实例组 %s 重新装配任务异常", group_id)
            if progress_task_id:
                deploy_task_manager.fail_task(progress_task_id, str(exc))

    async def _count_unprovisioned(self, group_id: int) -> int:
        """统计组内仍标记为配置失败的应用数量。"""
        async with async_session() as db:
            group = await self.get_group(db, group_id)
            return sum(
                1
                for entry in (group.app_configs or {}).values()
                if isinstance(entry, dict) and entry.get("provision_error")
            )

    async def _reprovision_pending(self, group_id: int, progress=None) -> None:
        """为运行时装配类应用补做账号自动配置。

        后端进程重启会中断部署后的后台配置协程，重跑时按实例组里
        尚未配置成功的应用重新执行，已配置成功的应用跳过。
        """
        # 延迟导入：避免 app_store -> orchestration -> provisioning 的循环依赖
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.models.orchestration import AppInstance
        from app.services.app_store.app_service import _slugify
        from app.services.orchestration.credentials import (
            _ENV_CREDENTIAL_SPECS,
            _get_env,
            generate_password,
        )
        from app.services.system import system_config_service
        from app.services.system.config_service import resolve_username_for_app

        async with async_session() as db:
            group = await self.get_group(db, group_id)
            instance_name = group.instance_name
            app_configs = dict(group.app_configs or {})
            sys_config = await system_config_service.get_or_create(db)
            default_username = (sys_config.default_admin_username or "").strip()
            email_domain = (sys_config.default_email_domain or "").strip()
            unified_password = (sys_config.default_admin_password or "").strip()
            stored_configs = {
                inst.app.name: inst.config or {}
                for inst in (
                    await db.execute(
                        select(AppInstance)
                        .where(AppInstance.orchestration_group_id == group_id)
                        .options(selectinload(AppInstance.app))
                    )
                ).scalars()
                if inst.app
            }

        for app_name, entry in app_configs.items():
            if not isinstance(entry, dict) or entry.get("provisioned"):
                continue
            url = entry.get("url")
            if not url or self._resolve_provision_client(app_name) is None:
                continue
            stored_password = str(entry.get("password") or "")
            password = unified_password or stored_password
            if not password:
                # 旧实例可能只有访问地址（如后加入自动配置的应用），
                # 补配置时按部署流程生成密码并落库
                password = generate_password()
            creds = {
                "username": resolve_username_for_app(
                    app_name,
                    entry.get("username") or default_username or "admin",
                    email_domain,
                ),
                "password": password,
            }
            if stored_password and stored_password != password:
                creds["previous_password"] = stored_password
            # env 注入类应用（MoviePilot/Reader）的密钥只存在于部署配置里，
            # 补配置时沿用已有值，保证后续装配能拿到 API Key
            spec = _ENV_CREDENTIAL_SPECS.get(app_name)
            api_key = entry.get("api_key")
            api_token_key = spec.get("api_token_key") if spec else None
            if not api_key and spec and api_token_key:
                api_key = _get_env(
                    stored_configs.get(app_name) or {},
                    str(spec["env_key"]),
                    str(api_token_key),
                )
            if api_key:
                creds["api_key"] = api_key
            await self._provision_app(
                group_id,
                app_name,
                creds,
                url,
                _slugify(f"{instance_name}-{app_name}"),
                progress=progress,
            )

    async def _write_provision_result(
        self,
        group_id: int,
        app_name: str,
        creds: dict,
        result: ProvisionResult | None,
        message: str | None,
    ) -> None:
        """把自动配置结果写入 group.app_configs[app_name] 并提交。"""
        async with async_session() as db:
            group = await self.get_group(db, group_id)
            app_configs = dict(group.app_configs or {})
            entry = dict(app_configs.get(app_name) or {})
            if result is not None and result.ok:
                entry.pop("provision_error", None)
                _apply_basic_auth_type(entry)
                # 部分应用会改写登录名（如 Komga 使用邮箱），以客户端返回为准
                entry["username"] = result.username or creds["username"]
                entry["password"] = creds["password"]
                if result.api_key:
                    entry["api_key"] = result.api_key
                if result.download_dir:
                    entry["download_dir"] = result.download_dir
                entry["provisioned"] = True
            else:
                entry.pop("provisioned", None)
                entry["provision_error"] = message or "自动配置失败"
                # 失败时保留原有凭据：应用当前仍是旧密码，覆盖成目标密码会
                # 让下次重试失去「用旧密码登录后改密」所需的有效密码
                _apply_basic_auth_type(entry)
                if not entry.get("username"):
                    entry["username"] = creds.get("username")
                if not entry.get("password") and creds.get("password"):
                    entry["password"] = creds["password"]
                if creds.get("api_key"):
                    entry["api_key"] = creds["api_key"]
            app_configs[app_name] = entry
            group.app_configs = app_configs
            await db.commit()

    def _resolve_app_client(self, app_name: str):
        """按应用名获取客户端实例，emby 复用 jellyfin 客户端。"""
        try:
            client_cls = get_client(app_name)
        except APIException:
            if app_name != "emby":
                return None
            try:
                client_cls = get_client("jellyfin")
            except APIException:
                return None
        return client_cls()

    def _resolve_provision_client(self, app_name: str):
        """按应用名获取支持自动配置的客户端实例。"""
        client = self._resolve_app_client(app_name)
        return client if client is not None and client.supports_provision else None

    @staticmethod
    def _get_project_container(project_name: str):
        """按 Compose 项目名定位主容器（容器名与项目名一致，否则按标签兜底）。"""
        client = docker_manager._client
        if client is None:
            raise RuntimeError("Docker 不可用")
        try:
            return client.containers.get(project_name)
        except docker.errors.NotFound:
            containers = client.containers.list(
                all=True,
                filters={"label": f"com.docker.compose.project={project_name}"},
            )
            if not containers:
                raise
            return containers[0]

    def _make_logs_reader(self, project_name: str):
        """构造读取 Compose 项目主容器日志的异步回调。"""

        async def read() -> str:
            def _read() -> str:
                container = self._get_project_container(project_name)
                return container.logs(tail=1000).decode("utf-8", errors="replace")

            return await asyncio.to_thread(_read)

        return read

    def _make_command_runner(self, project_name: str):
        """构造在 Compose 项目主容器内执行命令的异步回调。"""

        async def run(
            cmd: list[str],
            env: dict[str, str] | None = None,
            workdir: str | None = None,
        ) -> tuple[int, str]:
            def _run() -> tuple[int, str]:
                container = self._get_project_container(project_name)
                result = container.exec_run(
                    cmd,
                    environment=env or {},
                    workdir=workdir,
                )
                output = (result.output or b"").decode(
                    "utf-8", errors="replace"
                )
                return result.exit_code, output

            return await asyncio.to_thread(_run)

        return run

    def _project_running(self, project_name: str) -> bool:
        """判断 Compose 项目主容器是否仍在运行（容器已退出则不重试）。"""
        try:
            container = self._get_project_container(project_name)
            container.reload()
            return container.status == "running"
        except Exception:
            return False

    def _get_container_port(self, project_name: str, host_port: int) -> int | None:
        """查找映射到指定宿主机端口的容器内端口（Host 头伪装用）。"""

        def _lookup() -> int | None:
            container = self._get_project_container(project_name)
            ports = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
            for container_port, bindings in ports.items():
                for binding in bindings or []:
                    if binding.get("HostPort") == str(host_port):
                        return int(container_port.split("/")[0])
            return None

        return _lookup()

    def _build_suggested_url(
        self,
        network_ip: str | None,
        host_port: int | None,
        container_port: str | None,
    ) -> str | None:
        """根据网络信息构造建议访问地址。

        当容器映射了宿主机端口时，优先使用宿主机内网 IP + 宿主机端口；
        否则回退到容器内网 IP + 容器端口。
        """
        if host_port:
            return f"http://{self._get_host_ip()}:{host_port}"
        if network_ip and container_port:
            return f"http://{network_ip}:{container_port}"
        return None

    def _refresh_instance_status(self, instance: AppInstance) -> None:
        """根据 Docker 容器实际运行状态刷新 AppInstance.status。

        优先读取 instance.config 中的 container_id；未找到时尝试用
        container_name 兜底。Docker 不可用时保持原状态不变。
        """
        config = instance.config or {}
        container_id = config.get("container_id")
        if not container_id:
            container_name = config.get("container_name")
            if not container_name:
                return
            container_id = container_name

        client = docker_manager._client
        if client is None:
            return
        try:
            container = client.containers.get(container_id)
            container.reload()
        except docker.errors.NotFound:
            instance.status = "stopped"
            return
        except docker.errors.DockerException:
            return

        state = container.attrs.get("State", {}) or {}
        status = state.get("Status", container.status)
        if status == "running":
            instance.status = "running"
        elif status in {"exited", "dead"}:
            instance.status = "stopped"
        elif status == "paused":
            instance.status = "stopped"
        else:
            instance.status = status
