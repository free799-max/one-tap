"""Docker Compose 编排核心服务。"""

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.models.docker import (
    DockerComposeProject,
    DockerComposeStack,
    DockerComposeVersion,
)
from app.models.orchestration import AppInstance
from app.services.compose.compose_discovery import ComposeDiscoveryService
from app.services.compose.log_utils import (
    detect_stage,
    is_progress_line,
    strip_ansi,
    summarize_compose_error,
)
from app.services.compose.permissions import extract_bind_sources, parse_puid_pgid
from app.services.docker.mirror_pull import _format_pull_block, pull_with_mirror
from app.services.manage_client import manage_base_urls
from app.services.system.registry_mirror_service import get_acceleration_map
from app.services.compose.yaml_utils import (
    inject_labels,
    rewrite_ports,
    validate_project_name,
    validate_yaml,
)
from app.services.system.port_allocator import (
    allocate_and_reserve,
    get_used_ports,
    release_ports,
)
from app.services.system import system_config_service
from app.services.system.host_path_aliases import host_path_aliases

logger = logging.getLogger(__name__)


class ComposeService:
    """Docker Compose 编排核心服务。

    负责 Compose 项目的文件落盘、CLI 调用和状态同步。
    """

    def __init__(self):
        """初始化 Compose 工作区目录。"""
        self.workspace = Path(settings.COMPOSE_WORKSPACE_DIR).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._locks: dict[int, asyncio.Lock] = {}
        self._docker_available: bool | None = None
        self._discovery = ComposeDiscoveryService()

    @property
    def docker_available(self) -> bool:
        """检查 docker 命令是否可用（成功结果缓存，失败时每次重新探测）。"""
        if self._docker_available is not True:
            self._docker_available = shutil.which("docker") is not None
        return self._docker_available

    def _project_dir(self, project_name: str) -> Path:
        """获取系统创建项目的目录路径。"""
        return self.workspace / project_name

    def _compose_file(self, project_name: str) -> Path:
        """获取系统创建项目的默认 docker-compose.yml 路径。"""
        return self._project_dir(project_name) / "docker-compose.yml"

    def _config_files(self, project: DockerComposeProject) -> list[Path]:
        """解析项目的 compose 文件路径列表。"""
        if project.config_files:
            try:
                paths = json.loads(project.config_files)
                if isinstance(paths, list) and paths:
                    return [Path(p) for p in paths]
            except json.JSONDecodeError:
                pass
        return [self._compose_file(project.project_name)]

    def _working_dir(self, project: DockerComposeProject) -> Path:
        """解析项目执行工作目录。"""
        if project.working_dir:
            return Path(project.working_dir)
        return self._project_dir(project.project_name)

    def project_availability(
        self, project: DockerComposeProject
    ) -> tuple[bool, str | None]:
        """检查项目是否具备 Compose 维护所需的文件与工作目录。"""
        missing_files = [
            path for path in self._config_files(project) if not path.is_file()
        ]
        if missing_files:
            paths = "、".join(
                host_path_aliases.to_host_path(path) for path in missing_files
            )
            return (
                False,
                f"Compose 文件在容器内不可见：{paths}；请挂载项目目录并配置存储映射",
            )

        working_dir = self._working_dir(project)
        if not working_dir.is_dir():
            path = host_path_aliases.to_host_path(working_dir)
            return (
                False,
                f"Compose 工作目录在容器内不可见：{path}；请挂载项目目录并配置存储映射",
            )
        return True, None

    def _compose_command(
        self, project: DockerComposeProject, *args: str
    ) -> tuple[list[str], str]:
        """构造 Compose 命令，文件路径与工作目录切换为宿主机路径视图。

        数据库始终保留容器内路径；只有 CLI 调用转换，保证旧项目升级后
        不需要迁移，关闭路径别名时也能完全沿用旧行为。
        """
        config_files = [
            Path(host_path_aliases.to_host_path(path))
            for path in self._config_files(project)
        ]
        working_dir = host_path_aliases.to_host_path(self._working_dir(project))

        cmd = ["docker", "compose"]
        for cf in config_files:
            cmd.extend(["-f", str(cf)])
        cmd.extend(["-p", project.project_name, *args])
        return cmd, working_dir

    def _lock(self, project_id: int) -> asyncio.Lock:
        """获取项目级操作锁。"""
        if project_id not in self._locks:
            self._locks[project_id] = asyncio.Lock()
        return self._locks[project_id]

    async def _auto_allocate_ports(self, db, content: str) -> tuple[str, list[int]]:
        """按系统设置的起始端口，将 compose 内容中的宿主端口顺序分配为空闲端口。

        分配的端口会被预留（防止并发部署重复分配），调用方在部署结束
        （成功或失败）后需调用 release_ports 释放。
        无端口映射或 YAML 无法解析时原样返回（空预留列表），不阻断创建。

        Returns:
            (重写后的 YAML 内容, 本次分配并预留的端口列表)
        """
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            return content, []
        if not isinstance(data, dict):
            return content, []
        services = data.get("services")
        if not isinstance(services, dict):
            return content, []
        has_ports = any(
            isinstance(svc, dict) and svc.get("ports") for svc in services.values()
        )
        if not has_ports:
            return content, []

        system_config = await system_config_service.get_or_create(db)
        port_start = system_config.service_port_start or 10000
        used = await asyncio.to_thread(get_used_ports)
        reserved: list[list[int]] = []

        def _allocator(u: set[int], count: int, start: int) -> list[int]:
            ports = allocate_and_reserve(u, count, start)
            reserved.append(ports)
            return ports

        new_content = rewrite_ports(content, used, port_start, allocator=_allocator)
        return new_content, (reserved[0] if reserved else [])

    async def _write_compose_file(
        self, project: DockerComposeProject, version: DockerComposeVersion
    ) -> Path:
        """将当前版本 YAML 写入项目工作目录。"""
        config_files = self._config_files(project)
        compose_file = config_files[0]
        working_dir = self._working_dir(project)
        working_dir.mkdir(parents=True, exist_ok=True)
        content = inject_labels(version.content, project.project_name)
        compose_file.write_text(content, encoding="utf-8")
        return compose_file

    async def _run(
        self,
        project: DockerComposeProject,
        *args: str,
        timeout: int = 120,
    ) -> dict:
        """执行 docker compose CLI 命令。"""
        if not self.docker_available:
            raise RuntimeError("未找到 docker 命令，请确认 Docker 已安装")

        cmd, working_dir = self._compose_command(project, *args)

        logger.info("执行 compose 命令: %s (cwd=%s)", " ".join(cmd), working_dir)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("docker compose 命令执行超时")

        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    async def _run_streaming(
        self,
        project: DockerComposeProject,
        *args: str,
        on_line: Callable[..., Any] | None = None,
        timeout: int = 300,
    ) -> dict:
        """流式执行 docker compose CLI 命令，实时回调每一行输出并收集结果。"""
        if not self.docker_available:
            raise RuntimeError("未找到 docker 命令，请确认 Docker 已安装")

        cmd, working_dir = self._compose_command(project, *args)

        logger.info("执行 compose 命令: %s (cwd=%s)", " ".join(cmd), working_dir)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def _read_stream(stream, name: str, collector: list[str]):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = strip_ansi(
                    line.decode("utf-8", errors="replace")
                ).rstrip("\n")
                if text:
                    collector.append(text)
                    if on_line:
                        try:
                            on_line(text, name)
                        except Exception:
                            logger.exception("compose 进度回调异常")

        stdout_task = asyncio.create_task(_read_stream(proc.stdout, "stdout", stdout_lines))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr, "stderr", stderr_lines))
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            proc.kill()
            await proc.wait()
            raise RuntimeError("docker compose 命令执行超时")
        finally:
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        return {
            "returncode": proc.returncode,
            "stdout": "\n".join(stdout_lines),
            "stderr": "\n".join(stderr_lines),
        }

    async def _compose_images(self, project: DockerComposeProject) -> list[str]:
        """解析 compose 配置中需要拉取的镜像（跳过本地 build 服务）。"""
        result = await self._run(project, "config", "--format", "json", timeout=60)
        if result["returncode"] != 0:
            logger.info("解析 compose 镜像失败：%s", result.get("stderr", "")[:200])
            return []
        try:
            data = json.loads(result["stdout"])
        except ValueError:
            return []
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict):
            return []
        images: list[str] = []
        for service in services.values():
            if not isinstance(service, dict) or service.get("build"):
                continue
            image = service.get("image")
            if isinstance(image, str) and image.strip():
                normalized = image.strip()
                if normalized not in images:
                    images.append(normalized)
        return images

    async def _prepull_images(
        self,
        project: DockerComposeProject,
        progress: Callable[..., Any] | None,
    ) -> None:
        """部署前按源站用加速域名预拉镜像，成功后改回原 tag。"""
        if not manage_base_urls():
            return
        images = await self._compose_images(project)
        if not images:
            return
        acceleration = await get_acceleration_map()
        if not acceleration:
            return
        for index, image in enumerate(images, start=1):
            pull_progress: Callable[[dict], None] | None = None
            if progress:
                # 拉取阶段占据 26%～90% 的进度区间，按真实下载字节推进
                span_start = 26 + int(64 * (index - 1) / len(images))
                span_end = 26 + int(64 * index / len(images))
                message = f"通过加速域名拉取镜像 {index}/{len(images)}"
                progress("pulling_images", span_start, message, image)

                def pull_progress(
                    state: dict,
                    _start: int = span_start,
                    _end: int = span_end,
                    _message: str = message,
                    _image: str = image,
                ) -> None:
                    percentage = int(state.get("percentage", 0) or 0)
                    progress(
                        "pulling_images",
                        _start + int((_end - _start) * percentage / 100),
                        _message,
                        _format_pull_block(_image, state),
                    )

            try:
                await pull_with_mirror(
                    image, acceleration, progress=pull_progress
                )
            except Exception:
                logger.warning(
                    "预拉取镜像失败，交给 compose 重试：%s", image, exc_info=True
                )

    @staticmethod
    def _has_ip_conflict(result: dict) -> bool:
        """判断 compose 输出是否为容器网络地址冲突。"""
        text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
        return "address already in use" in text

    async def _static_ip_targets(
        self, project: DockerComposeProject
    ) -> list[dict[str, str]]:
        """解析 Compose 中固定的容器 IPv4 地址。"""
        result = await self._run(project, "config", "--format", "json", timeout=60)
        if result["returncode"] != 0:
            return []
        try:
            data = json.loads(result["stdout"] or "{}")
        except ValueError:
            return []
        services = data.get("services")
        if not isinstance(services, dict):
            return []
        targets: list[dict[str, str]] = []
        for name, service in services.items():
            if not isinstance(service, dict):
                continue
            networks = service.get("networks")
            if not isinstance(networks, dict):
                continue
            container_name = (
                service.get("container_name")
                or f"{project.project_name}-{name}-1"
            )
            for network_name, network_config in networks.items():
                if not isinstance(network_config, dict):
                    continue
                ip = network_config.get("ipv4_address")
                if not ip:
                    continue
                targets.append(
                    {
                        "service": str(name),
                        "container_name": str(container_name),
                        "network": str(network_name),
                        "ip": str(ip),
                    }
                )
        return targets

    @staticmethod
    def _static_ip_holders_sync(
        targets: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """查询占用固定容器地址的容器信息。"""
        from app.services.docker import docker_manager

        client = docker_manager._client
        if client is None:
            return []
        holders: list[dict[str, str]] = []
        for target in targets:
            try:
                network = client.networks.get(target["network"])
            except Exception:
                continue
            for container_id, endpoint in (
                network.attrs.get("Containers") or {}
            ).items():
                address = str((endpoint or {}).get("IPv4Address") or "").split("/")[0]
                if address != target["ip"]:
                    continue
                try:
                    container = client.containers.get(container_id)
                except Exception:
                    continue
                labels = container.labels or {}
                holders.append(
                    {
                        "id": container_id,
                        "name": container.name,
                        "status": container.status,
                        "project": str(
                            labels.get("com.docker.compose.project") or ""
                        ),
                        "network": target["network"],
                        "ip": target["ip"],
                        "expected": target["container_name"],
                    }
                )
        return holders

    @staticmethod
    def _remove_container_sync(container_id: str) -> None:
        """强制删除容器。"""
        from app.services.docker import docker_manager

        client = docker_manager._client
        if client is None:
            return
        client.containers.get(container_id).remove(force=True)

    async def _heal_static_ip_conflicts(
        self, project: DockerComposeProject
    ) -> tuple[list[str], list[dict[str, str]]]:
        """清理本项目残留的固定地址占用，返回沿未解决的外部冲突。"""
        targets = await self._static_ip_targets(project)
        if not targets:
            return [], []
        holders = await asyncio.to_thread(self._static_ip_holders_sync, targets)
        removed: list[str] = []
        foreign: list[dict[str, str]] = []
        for holder in holders:
            if holder["project"] != project.project_name:
                foreign.append(holder)
                continue
            # 正常运行的目标容器交给 compose 自己重建。
            if holder["name"] == holder["expected"] and holder["status"] == "running":
                continue
            try:
                await asyncio.to_thread(self._remove_container_sync, holder["id"])
                removed.append(holder["name"])
                logger.info(
                    "清理静态地址残留容器 %s (%s)", holder["name"], holder["ip"]
                )
            except Exception as exc:
                logger.warning("清理残留容器 %s 失败：%s", holder["name"], exc)
                foreign.append(holder)
        return removed, foreign

    @staticmethod
    def _format_ip_conflicts(conflicts: list[dict[str, str]]) -> str:
        """构造固定地址冲突的错误信息，保留 Docker 原始关键字便于上层识别。"""
        detail = "；".join(
            f"{item['ip']} 已被容器 {item['name']}"
            + (f"（Compose 项目 {item['project']}）" if item["project"] else "")
            + " 占用"
            for item in conflicts
        )
        return (
            f"容器网络地址冲突（Address already in use）：{detail}。"
            "请先停止占用地址的容器，或修改本项目的固定 IP 后重试"
        )

    async def _run_up_with_progress(
        self,
        project: DockerComposeProject,
        progress_callback: Callable[..., Any] | None,
        timeout: int = 300,
        pull: bool = True,
    ) -> dict:
        """统一执行 docker compose up -d 并推送进度。

        若未提供 progress_callback，则退化为非流式执行。
        固定容器 IP 被占用时清理本项目残留容器并重试一次，避免
        Docker 的 ``Address already in use`` 直接导致部署失败。
        ``pull=False`` 用于已有项目的启动，只使用本机已有镜像。
        """
        def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
            if progress_callback:
                progress_callback(stage, percentage, message, detail)

        if pull:
            _progress("pulling_images", 26, "准备拉取镜像")
            await self._prepull_images(project, progress_callback)
            _progress("pulling_images", 90, "拉取镜像并启动服务")
            current_stage = "pulling_images"
        else:
            _progress("starting_services", 90, "启动服务中")
            current_stage = "starting_services"
        last_progress_at = 0.0
        up_args = ("up", "-d") if pull else ("up", "-d", "--pull", "never")

        def _on_line(line: str, name: str):
            detected = detect_stage(line)
            nonlocal current_stage, last_progress_at
            if detected:
                current_stage = detected
            if is_progress_line(line):
                # 分层下载进度刷新极快，节流转发，避免刷屏
                now = time.monotonic()
                if now - last_progress_at < 2:
                    return
                last_progress_at = now
            _progress(
                current_stage,
                90,
                "拉取镜像中" if current_stage == "pulling_images" else "启动服务中",
                line,
            )

        async def _up() -> dict:
            if progress_callback:
                return await self._run_streaming(
                    project, *up_args, on_line=_on_line, timeout=timeout
                )
            return await self._run(project, *up_args, timeout=timeout)

        # 开跑前先处理残留的静态地址占用，避免启动阶段才失败。
        _, foreign = await self._heal_static_ip_conflicts(project)
        if foreign:
            raise RuntimeError(self._format_ip_conflicts(foreign))

        result = await _up()
        if result["returncode"] != 0 and self._has_ip_conflict(result):
            removed, foreign = await self._heal_static_ip_conflicts(project)
            if foreign:
                raise RuntimeError(self._format_ip_conflicts(foreign))
            if removed:
                logger.info("清理残留容器后重试 compose up: %s", removed)
                _progress("starting_services", 91, "清理残留容器后重试")
                result = await _up()

        if result["returncode"] != 0:
            if self._has_ip_conflict(result):
                # 无法定位占用者时也保留关键字，方便上层按地址冲突处理。
                _, foreign = await self._heal_static_ip_conflicts(project)
                if foreign:
                    raise RuntimeError(self._format_ip_conflicts(foreign))
            error = summarize_compose_error(result.get("stderr", ""))
            raise RuntimeError(error or "docker compose up -d 执行失败")

        return result

    async def _ensure_host_directories(self, content: str) -> None:
        """解析 Compose YAML，为 bind mount 的宿主机路径自动创建目录。

        对本次新建的目录，若服务显式声明了 PUID/PGID，将目录属主调整为
        该运行用户，避免容器因权限不足无法写入而反复重启。已存在的目录
        不做任何改动。不检查镜像 Config.User——镜像不在本地时 inspect
        会触发静默拉取，导致部署长时间无进度。
        """
        try:
            data = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            return

        services = data.get("services") or {}
        if not isinstance(services, dict):
            return

        for svc in services.values():
            if not isinstance(svc, dict):
                continue
            new_paths: list[Path] = []
            for source in extract_bind_sources(svc):
                path = Path(source)
                is_new = not path.exists()
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError:
                    # 权限不足或路径非法时跳过，避免阻塞部署
                    logger.warning("自动创建宿主机目录失败: %s", source)
                    continue
                if is_new:
                    new_paths.append(path)
            if not new_paths:
                continue

            uid_gid = parse_puid_pgid(svc.get("environment"))
            if not uid_gid:
                continue
            uid, gid = uid_gid
            for path in new_paths:
                try:
                    os.chown(path, uid, gid)
                    logger.info(
                        "自动授权 %s → %d:%d（服务 %s 声明了 PUID/PGID）",
                        path, uid, gid, svc.get("image"),
                    )
                except OSError as e:
                    logger.warning("自动授权失败 %s → %d:%d: %s", path, uid, gid, e)

    async def create_project(
        self,
        db,
        project_name: str,
        content: str,
        user_id: int | None = None,
        description: str | None = None,
    ) -> DockerComposeProject:
        """创建 Compose 项目并写入初始版本（同步包装）。"""
        return await self.create_project_async(
            db,
            project_name=project_name,
            content=content,
            user_id=user_id,
            description=description,
        )

    async def create_project_async(
        self,
        db,
        project_name: str,
        content: str,
        user_id: int | None = None,
        description: str | None = None,
        progress_callback: Callable[..., Any] | None = None,
        allocate_ports: bool = True,
    ) -> DockerComposeProject:
        """创建 Compose 项目并写入初始版本（支持进度回调）。

        Args:
            allocate_ports: 是否对 YAML 宿主端口做顺序分配。应用商店部署
                路径已在 deploy 阶段分配并预留端口，传 False 避免二次分配。
        """
        validate_project_name(project_name)
        validate_yaml(content)

        # 服务端口顺序分配：从起始端口开始重写宿主端口，跳过已占用端口。
        # 分配的端口先预留，部署结束（成功或失败）后统一释放
        reserved_ports: list[int] = []
        if allocate_ports:
            content, reserved_ports = await self._auto_allocate_ports(db, content)

        try:
            return await self._create_project_inner(
                db,
                project_name=project_name,
                content=content,
                user_id=user_id,
                description=description,
                progress_callback=progress_callback,
            )
        finally:
            release_ports(reserved_ports)

    async def _create_project_inner(
        self,
        db,
        project_name: str,
        content: str,
        user_id: int | None,
        description: str | None,
        progress_callback: Callable[..., Any] | None,
    ) -> DockerComposeProject:
        """创建 Compose 项目的主体流程（端口分配与释放由外层负责）。"""

        def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
            if progress_callback:
                progress_callback(stage, percentage, message, detail)

        _progress("preparing", 5, "准备部署")

        # 自动创建 bind mount 所需的宿主机目录（新建目录按需授权给容器用户）
        await self._ensure_host_directories(content)

        _progress("creating_project", 15, "创建 Compose 项目")
        project_dir = self._project_dir(project_name)
        project_dir.mkdir(parents=True, exist_ok=True)
        compose_file = self._compose_file(project_name)

        project = DockerComposeProject(
            project_name=project_name,
            description=description,
            config_files=json.dumps([str(compose_file)], ensure_ascii=False),
            working_dir=str(project_dir),
        )
        db.add(project)
        await db.flush()

        version = DockerComposeVersion(
            project_id=project.id,
            version_number=1,
            content=content,
            comment=None,
            is_current=True,
            created_by_user_id=user_id,
        )
        db.add(version)
        await db.flush()

        _progress("writing_compose", 25, "写入 Compose 配置")
        await self._write_compose_file(project, version)

        # 首次创建需要拉取镜像，大镜像耗时较长，超时放宽到 30 分钟
        await self._run_up_with_progress(project, progress_callback, timeout=1800)

        _progress("syncing_status", 95, "同步服务状态")
        stack = await self.sync_stack_status(db, project, action="up")
        if stack.service_count > 0 and stack.running_count == 0:
            raise RuntimeError(
                "服务启动后未保持运行，请检查容器日志或 compose 配置"
            )

        await db.commit()
        await db.refresh(project)
        return project

    async def update_project(
        self,
        db,
        project: DockerComposeProject,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> DockerComposeProject:
        """更新项目元数据。"""
        if description is not None:
            project.description = description
        if is_active is not None:
            project.is_active = is_active
        await db.commit()
        await db.refresh(project)
        return project

    def _compose_volume_config(self, project: DockerComposeProject) -> dict[str, Any]:
        """从 Compose 文件提取宿主机挂载路径，供数据目录备份使用。"""
        volumes: list[dict[str, str]] = []
        for path in self._config_files(project):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            services = data.get("services") if isinstance(data, dict) else None
            if not isinstance(services, dict):
                continue
            for service in services.values():
                if not isinstance(service, dict):
                    continue
                for volume in service.get("volumes") or []:
                    if isinstance(volume, str):
                        host_path = volume.split(":", 1)[0]
                    elif isinstance(volume, dict):
                        host_path = volume.get("source") or volume.get("host_path")
                    else:
                        continue
                    if host_path:
                        volumes.append({"host_path": str(host_path)})
        return {"volumes": volumes}

    async def _backup_project_data(
        self, db, project: DockerComposeProject
    ) -> tuple[Path | None, list]:
        """把项目自身的数据目录移入应用数据目录下的 ``_backups``。"""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.services.orchestration.backup import (
            AppDataEntry,
            backup_app_data_dirs,
        )

        result = await db.execute(
            select(AppInstance)
            .options(selectinload(AppInstance.app))
            .where(AppInstance.project_id == project.id)
        )
        instances = result.scalars().all()
        if any(instance.orchestration_group_id for instance in instances):
            # 实例组内多个应用共用 <应用数据目录>/<实例组名>/ 目录，单独删除
            # 某个 Compose 项目时不能整体搬走；实例组删除流程会统一备份。
            logger.info(
                "项目 %s 属于编排实例组，跳过 Compose 级数据备份",
                project.project_name,
            )
            return None, []
        entries = [
            AppDataEntry(
                instance_name=instance.instance_name,
                app_name=instance.app.name if instance.app else None,
                project_name=project.project_name,
                config=dict(instance.config or {}),
            )
            for instance in instances
        ]
        if not entries:
            # 手动创建的 Compose 项目没有 AppInstance，从当前 Compose 文件
            # 的 bind mount 中提取宿主机路径。
            entries = [
                AppDataEntry(
                    instance_name=project.project_name,
                    app_name=None,
                    project_name=project.project_name,
                    config=self._compose_volume_config(project),
                )
            ]
        return await backup_app_data_dirs(
            db, entries, group_name=project.project_name
        )

    async def delete_project(
        self,
        db,
        project: DockerComposeProject,
        backup_data: bool = False,
    ) -> None:
        """删除项目。

        Args:
            backup_data: 删除容器后是否先把应用数据目录移入
                ``<应用数据目录>/_backups``。重新部署时会从空数据开始；
                内部替换项目（如 AdGuard 重新部署）应保持 False。
        """
        async with self._lock(project.id):
            try:
                await self._run(project, "down", "--remove-orphans", timeout=120)
            except Exception as e:
                logger.warning("删除项目时 down 失败（可能已不存在）: %s", e)

            # 兜底：无论 docker compose down 是否成功，都强制清理归属容器，
            # 避免外部发现的项目或 down 失败的项目反复“复活”。
            await self._remove_project_containers(project.project_name)

            if backup_data:
                backup_dir, failures = await self._backup_project_data(db, project)
                if failures:
                    names = "、".join(item.project_name for item in failures)
                    raise RuntimeError(
                        f"数据目录备份失败，已停止删除项目 {project.project_name}：{names}"
                    )
                if backup_dir is not None:
                    logger.info(
                        "项目 %s 的数据目录已备份到 %s",
                        project.project_name,
                        backup_dir,
                    )

            project_dir = self._project_dir(project.project_name)
            if project_dir == self._working_dir(project):
                if project_dir.exists():
                    shutil.rmtree(project_dir)

            # 先清理关联的应用实例记录（含备份级联删除）
            from sqlalchemy import delete

            await db.execute(
                delete(AppInstance).where(AppInstance.project_id == project.id)
            )

            await db.delete(project)
            await db.commit()
            self._locks.pop(project.id, None)

    async def _remove_project_containers(self, project_name: str) -> None:
        """按 Compose 项目标签强制停止并删除容器。"""
        from app.services.docker import docker_manager

        if not await docker_manager.run_sync(docker_manager.check_available):
            return

        try:
            filters = {"label": [f"com.docker.compose.project={project_name}"]}
            containers = await docker_manager.run_sync(
                docker_manager.list_containers, filters=filters
            )
            container_ids = [c.get("id") for c in containers if c.get("id")]
            if container_ids:
                logger.info(
                    "强制删除项目 %s 的容器: %s", project_name, container_ids
                )
                await docker_manager.run_sync(
                    docker_manager.batch_container_action, container_ids, "remove"
                )
        except Exception as e:
            logger.warning("强制删除项目 %s 容器失败: %s", project_name, e)

    async def add_version(
        self,
        db,
        project: DockerComposeProject,
        content: str,
        user_id: int | None = None,
    ) -> DockerComposeVersion:
        """为项目新增一个版本并设为当前版本。"""
        from sqlalchemy import select, update

        validate_yaml(content)

        result = await db.execute(
            select(DockerComposeVersion.version_number)
            .where(DockerComposeVersion.project_id == project.id)
            .order_by(DockerComposeVersion.version_number.desc())
        )
        max_version = result.scalar() or 0

        await db.execute(
            update(DockerComposeVersion)
            .where(DockerComposeVersion.project_id == project.id)
            .values(is_current=False)
        )

        version = DockerComposeVersion(
            project_id=project.id,
            version_number=max_version + 1,
            content=content,
            comment=None,
            is_current=True,
            created_by_user_id=user_id,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)

        await self._write_compose_file(project, version)
        return version

    async def edit_and_deploy(
        self,
        db,
        project: DockerComposeProject,
        content: str,
        user_id: int | None = None,
        comment: str | None = None,
        description: str | None = None,
    ) -> tuple[DockerComposeVersion, dict]:
        """编辑 Compose 项目：保存新版本并自动部署（同步包装）。"""
        return await self.edit_and_deploy_async(
            db,
            project,
            content=content,
            user_id=user_id,
            comment=comment,
            description=description,
        )

    async def edit_and_deploy_async(
        self,
        db,
        project: DockerComposeProject,
        content: str,
        user_id: int | None = None,
        comment: str | None = None,
        description: str | None = None,
        progress_callback: Callable[..., Any] | None = None,
    ) -> tuple[DockerComposeVersion, dict]:
        """编辑 Compose 项目：保存新版本并自动部署（支持进度回调）。"""
        from sqlalchemy import select, update

        def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
            if progress_callback:
                progress_callback(stage, percentage, message, detail)

        validate_yaml(content)

        async with self._lock(project.id):
            _progress("preparing", 5, "准备应用新配置")
            if description is not None:
                project.description = description

            result = await db.execute(
                select(DockerComposeVersion.version_number)
                .where(DockerComposeVersion.project_id == project.id)
                .order_by(DockerComposeVersion.version_number.desc())
            )
            max_version = result.scalar() or 0

            await db.execute(
                update(DockerComposeVersion)
                .where(DockerComposeVersion.project_id == project.id)
                .values(is_current=False)
            )

            version = DockerComposeVersion(
                project_id=project.id,
                version_number=max_version + 1,
                content=content,
                comment=comment or "编辑更新",
                is_current=True,
                created_by_user_id=user_id,
            )
            db.add(version)
            await db.commit()
            await db.refresh(version)

            _progress("writing_compose", 25, "写入 Compose 配置")
            await self._write_compose_file(project, version)

            # 编辑部署可能引入新的 bind mount：先以本进程身份创建目录，
            # 避免 Docker 以 root 自动创建，导致之后删除实例时数据目录搬不走
            await self._ensure_host_directories(content)

            await self._run_up_with_progress(project, progress_callback, timeout=300)

            _progress("syncing_status", 95, "同步服务状态")
            stack = await self.sync_stack_status(db, project, action="up")
            if stack.service_count > 0 and stack.running_count == 0:
                raise RuntimeError(
                    "服务启动后未保持运行，请检查容器日志或 compose 配置"
                )

            return version, {"returncode": 0, "stdout": "", "stderr": ""}

    async def rollback_version(
        self,
        db,
        project: DockerComposeProject,
        version: DockerComposeVersion,
    ) -> DockerComposeVersion:
        """切换到指定版本。"""
        from sqlalchemy import update

        async with self._lock(project.id):
            await db.execute(
                update(DockerComposeVersion)
                .where(DockerComposeVersion.project_id == project.id)
                .values(is_current=False)
            )
            version.is_current = True
            await db.commit()
            await db.refresh(version)

            await self._write_compose_file(project, version)
            result = await self._run(project, "up", "-d", timeout=300)
            if result["returncode"] != 0:
                raise RuntimeError(f"切换后启动失败: {result['stderr']}")

            await self.sync_stack_status(db, project, action="rollback")
            return version

    async def action(
        self,
        db,
        project: DockerComposeProject,
        action: str,
    ) -> dict:
        """对项目执行 up / down / restart 操作（同步包装）。"""
        return await self.action_async(db, project, action)

    async def action_async(
        self,
        db,
        project: DockerComposeProject,
        action: str,
        progress_callback: Callable[..., Any] | None = None,
    ) -> dict:
        """对项目执行 up / down / restart 操作（支持进度回调）。"""
        if action not in {"up", "down", "restart"}:
            raise ValueError(f"不支持的操作: {action}")

        def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
            if progress_callback:
                progress_callback(stage, percentage, message, detail)

        async with self._lock(project.id):
            _progress("preparing", 5, f"准备执行 {action}")

            if action == "up":
                await self._run_up_with_progress(
                    project, progress_callback, timeout=300, pull=False
                )
            elif action == "down":
                _progress("starting_services", 50, "停止服务中")
                result = await self._run(
                    project, "down", "--remove-orphans", timeout=120
                )
                if result["returncode"] != 0:
                    raise RuntimeError(result.get("stderr") or "compose down 失败")
            else:
                _progress("starting_services", 50, "重启服务中")
                result = await self._run(project, "restart", timeout=120)
                if result["returncode"] != 0:
                    raise RuntimeError(result.get("stderr") or "compose restart 失败")

            _progress("syncing_status", 95, "同步服务状态")
            stack = await self.sync_stack_status(db, project, action=action)
            if action == "up" and stack.service_count > 0 and stack.running_count == 0:
                raise RuntimeError(
                    "服务启动后未保持运行，请检查容器日志或 compose 配置"
                )
            return {"returncode": 0, "stdout": "", "stderr": ""}

    async def get_logs(
        self,
        project: DockerComposeProject,
        tail: int = 100,
        services: list[str] | None = None,
    ) -> str:
        """获取项目日志。"""
        args = ["logs", "--tail", str(tail)]
        if services:
            args.extend(services)
        result = await self._run(project, *args, timeout=60)
        if result["returncode"] != 0:
            raise RuntimeError(strip_ansi(result["stderr"]))
        return strip_ansi(result["stdout"])

    async def stream_logs(
        self,
        project: DockerComposeProject,
        tail: int = 100,
        services: list[str] | None = None,
        follow: bool = True,
    ):
        """流式获取项目日志。"""
        if not self.docker_available:
            raise RuntimeError("未找到 docker 命令，请确认 Docker 已安装")

        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("-f")
        if services:
            args.extend(services)
        cmd, working_dir = self._compose_command(project, *args)

        logger.info("执行 compose 日志流: %s (cwd=%s)", " ".join(cmd), working_dir)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        def _clean_log_line(raw: str) -> str:
            """清理日志行：移除 ANSI 转义序列并处理进度条的 \\r 覆盖。"""
            cleaned = strip_ansi(raw)
            # 先统一去掉行尾换行符，避免 \\r\\n 被当成行内覆盖
            cleaned = cleaned.rstrip("\r\n")
            # 进度条通常使用 \\r 回到行首覆盖旧内容，只保留最后一次覆盖后的文本
            if "\r" in cleaned:
                cleaned = cleaned.rsplit("\r", 1)[-1]
            return cleaned

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield _clean_log_line(line.decode("utf-8", errors="replace"))

            stderr_data = await proc.stderr.read()
            stderr = strip_ansi(
                stderr_data.decode("utf-8", errors="replace")
            ).strip()
            if stderr:
                logger.warning("compose 日志流 stderr: %s", stderr)
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def get_status(
        self, project: DockerComposeProject
    ) -> dict:
        """获取项目实时服务状态。"""
        result = await self._run(project, "ps", "--format", "json", timeout=30)
        if result["returncode"] != 0:
            raise RuntimeError(result["stderr"])

        try:
            services = []
            for line in (result["stdout"] or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    services.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            services = []

        return self._parse_ps(services)

    @staticmethod
    def _parse_ps(services: list) -> dict:
        """解析 docker compose ps --format json 输出。"""
        total = len(services)
        running = 0
        restarting = 0
        port_set: set[str] = set()
        for svc in services:
            state = svc.get("State", "")
            if state == "running":
                running += 1
            elif state == "restarting":
                restarting += 1
            publishers = svc.get("Publishers") or []
            for pub in publishers:
                url = pub.get("URL", "")
                target = pub.get("TargetPort", "")
                published = pub.get("PublishedPort", "")
                if url and target and published:
                    port_set.add(f"{url}:{published}->{target}")

        if total == 0:
            status = "stopped"
        elif running == total:
            status = "running"
        elif running > 0:
            status = "partial"
        elif restarting > 0:
            status = "restarting"
        else:
            status = "exited"

        return {
            "status": status,
            "service_count": total,
            "running_count": running,
            "ports": sorted(port_set),
        }

    @staticmethod
    def _utcnow() -> "datetime":
        """返回 UTC 当前时间（naive，与数据库 func.now() 写入的语义一致）。"""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    async def sync_stack_status(
        self,
        db,
        project: DockerComposeProject,
        action: str | None = None,
    ) -> DockerComposeStack:
        """同步 Stack 状态到数据库。"""
        from sqlalchemy import select

        status_info = await self.get_status(project)
        result = await db.execute(
            select(DockerComposeStack).where(
                DockerComposeStack.project_id == project.id
            )
        )
        stack = result.scalar_one_or_none()
        if not stack:
            stack = DockerComposeStack(project_id=project.id)
            db.add(stack)

        stack.status = status_info["status"]
        stack.service_count = status_info["service_count"]
        stack.running_count = status_info["running_count"]
        stack.ports = json.dumps(status_info["ports"], ensure_ascii=False)
        if action:
            stack.last_action = action
            stack.last_action_at = self._utcnow()
        stack.updated_at = self._utcnow()

        await db.commit()
        await db.refresh(stack)
        return stack

    async def discover_projects(self, db) -> list[DockerComposeProject]:
        """委托给自动发现服务扫描 Docker 容器并补全项目记录。"""
        return await self._discovery.discover_projects(db)


# 全局单例
compose_manager = ComposeService()
