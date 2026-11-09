"""宿主机路径别名服务。

OneTap 容器通过 docker.sock 管理宿主机 Compose 项目时，Compose 文件与相对
挂载路径必须以宿主机视角传给 Docker，否则容器标签和 bind source 会指向
宿主机上不存在的容器路径。

本服务复用系统设置里的「宿主机路径 ↔ 容器内路径」映射，并自动补充
DATA_ROOT 的宿主机挂载源，为 host_path != mount_path 的映射在容器内建立
``host_path -> mount_path`` 软链。这样同一个目录在两侧都能用宿主机路径访问，
Compose 调用再通过 to_host_path 切到宿主机视图。

不修改数据库中的项目路径：旧版本创建的项目升级后无需迁移，关闭
HOST_PATH_ALIAS_ENABLED 即可完整回到旧行为。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# 这些系统目录下不允许创建别名，避免异常配置遮蔽容器系统文件。
_FORBIDDEN_HOST_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/usr",
    "/boot",
    "/run",
    "/var/run",
)


@dataclass(frozen=True)
class HostPathMapping:
    """一条宿主机路径与容器内路径的对应关系。"""

    host_path: str
    container_path: str
    source: str
    """映射来源：data-root 为 OneTap 数据目录，storage 为存储目录设置。"""


def _normalize_path(path: str | Path | None) -> str:
    """只做文本规范化，不调用 resolve，避免把软链别名解析回目标路径。"""
    value = str(path or "").strip()
    return os.path.normpath(value) if value else ""


def _is_under(path: str, base: str) -> bool:
    """按路径段判断 path 是否等于 base 或位于 base 之下。"""
    if not path or not base:
        return False
    if base == "/":
        return path.startswith("/")
    return path == base or path.startswith(base + "/")


def _join(base: str, suffix: str) -> str:
    suffix = suffix.lstrip("/")
    if not suffix:
        return base
    return os.path.join(base, suffix)


class HostPathAliasService:
    """维护宿主机路径别名，并提供容器路径到宿主机路径的转换。"""

    def __init__(self):
        self._mappings: tuple[HostPathMapping, ...] = ()
        self._lock = asyncio.Lock()
        self._data_root_host: str | None = None
        self._data_root_detection_done = False

    @property
    def mappings(self) -> tuple[HostPathMapping, ...]:
        """当前已生效（容器内可通过宿主机路径访问）的映射。"""
        return self._mappings

    def reset(self) -> None:
        """清空运行时状态，供测试与显式重初始化使用。"""
        self._mappings = ()
        self._data_root_host = None
        self._data_root_detection_done = False

    def to_host_path(self, path: str | Path | None) -> str:
        """将容器内路径转换为宿主机路径；未命中映射时原样返回。

        只替换已生效映射的 container_path 前缀，并按最长前缀优先匹配。
        """
        value = _normalize_path(path)
        if not value or not value.startswith("/"):
            return str(path or "")
        ordered = sorted(
            self._mappings, key=lambda item: len(item.container_path), reverse=True
        )
        for mapping in ordered:
            if _is_under(value, mapping.container_path):
                suffix = value[len(mapping.container_path) :]
                return _join(mapping.host_path, suffix)
        return value

    async def initialize(self) -> None:
        """应用启动时建立别名，并对旧版本遗留的错误挂载给出告警。"""
        await self.refresh()
        await self._warn_legacy_bind_mounts()

    async def refresh(self, db: AsyncSession | None = None) -> None:
        """重新读取映射并补齐软链；存储目录变更后也应调用。

        单条映射失败只会跳过该条，不影响其他映射和系统启动。
        """
        if not settings.HOST_PATH_ALIAS_ENABLED:
            self._mappings = ()
            return
        async with self._lock:
            if db is not None:
                mappings = await self._collect_mappings(db)
            else:
                from app.database import async_session

                async with async_session() as session:
                    mappings = await self._collect_mappings(session)
            active = await asyncio.to_thread(self._ensure_aliases_sync, mappings)
            self._mappings = tuple(
                sorted(active, key=lambda item: len(item.container_path), reverse=True)
            )

    async def _collect_mappings(self, db: AsyncSession) -> list[HostPathMapping]:
        """汇总启用的存储目录映射与 DATA_ROOT 的宿主机挂载源。"""
        from app.models.storage_directory import StorageDirectory

        result = await db.execute(
            select(StorageDirectory).where(StorageDirectory.enabled.is_(True))
        )
        mappings: list[HostPathMapping] = []
        for directory in result.scalars().all():
            host_path = _normalize_path(directory.host_path)
            container_path = _normalize_path(directory.mount_path)
            if host_path and container_path and host_path != container_path:
                mappings.append(
                    HostPathMapping(host_path, container_path, "storage")
                )

        container_data_root = _normalize_path(Path(settings.DATA_ROOT).resolve())
        host_data_root = await self._get_data_root_host(container_data_root)
        if host_data_root and host_data_root != container_data_root:
            mappings.append(
                HostPathMapping(host_data_root, container_data_root, "data-root")
            )
        return mappings

    async def _get_data_root_host(self, container_data_root: str) -> str | None:
        """优先使用显式配置，否则通过自身容器的挂载信息自动探测。"""
        if settings.HOST_DATA_ROOT:
            return _normalize_path(settings.HOST_DATA_ROOT)
        if self._data_root_detection_done:
            return self._data_root_host
        host, done = await asyncio.to_thread(
            self._detect_data_root_host_sync, container_data_root
        )
        if done:
            self._data_root_host = host
            self._data_root_detection_done = True
        return host

    def _detect_data_root_host_sync(
        self, container_data_root: str
    ) -> tuple[str | None, bool]:
        """读取自身容器的 bind 挂载源，返回 (宿主机路径, 探测是否已有定论)。"""
        from app.services.docker import docker_manager

        client = docker_manager._client
        if client is None:
            return None, False
        try:
            container = client.containers.get(socket.gethostname())
        except Exception as exc:
            # 直接运行在宿主机、或自定义 hostname 无法反查容器时，不做映射。
            if exc.__class__.__name__ == "NotFound":
                return None, True
            logger.debug("探测自身容器挂载失败：%s", exc)
            return None, False

        for mount in container.attrs.get("Mounts", []) or []:
            if mount.get("Type") != "bind":
                continue
            if _normalize_path(mount.get("Destination")) != container_data_root:
                continue
            source = _normalize_path(mount.get("Source"))
            if source.startswith("/"):
                return source, True
        return None, True

    def _ensure_aliases_sync(
        self, mappings: list[HostPathMapping]
    ) -> list[HostPathMapping]:
        """为每条安全映射建立软链，返回容器内已可通过宿主机路径访问的映射。"""
        active: list[HostPathMapping] = []
        for mapping in mappings:
            if not self._mapping_safe(mapping):
                continue
            if self._alias_ready(mapping):
                active.append(mapping)
        return active

    def _mapping_safe(self, mapping: HostPathMapping) -> bool:
        """拒绝系统目录、相互嵌套和容器内不可见的映射。"""
        host_path = mapping.host_path
        container_path = mapping.container_path
        if not host_path.startswith("/") or not container_path.startswith("/"):
            return False
        if host_path == "/" or container_path == "/":
            return False
        if any(_is_under(host_path, prefix) for prefix in _FORBIDDEN_HOST_PREFIXES):
            logger.warning("宿主机路径别名命中系统目录，已跳过：%s", host_path)
            return False
        if _is_under(host_path, container_path) or _is_under(
            container_path, host_path
        ):
            logger.warning(
                "宿主机路径与容器内路径存在嵌套，无法建立别名：%s ↔ %s",
                host_path,
                container_path,
            )
            return False
        if not os.path.isdir(container_path):
            logger.debug("容器内路径不可见，跳过别名：%s", container_path)
            return False
        return True

    def _alias_ready(self, mapping: HostPathMapping) -> bool:
        """确保 host_path 是指向 container_path 的可用别名，不覆盖已有目录。"""
        host_path = mapping.host_path
        container_path = mapping.container_path
        if os.path.lexists(host_path):
            try:
                if os.path.isdir(host_path) and os.path.samefile(
                    host_path, container_path
                ):
                    return True
            except OSError:
                pass
            logger.warning(
                "宿主机路径在容器内已被其他文件占用，跳过别名：%s", host_path
            )
            return False

        try:
            os.makedirs(os.path.dirname(host_path), exist_ok=True)
            os.symlink(container_path, host_path, target_is_directory=True)
        except OSError as exc:
            logger.warning(
                "创建宿主机路径别名失败 %s -> %s：%s",
                host_path,
                container_path,
                exc,
            )
            return False
        try:
            ready = os.path.isdir(host_path) and os.path.samefile(
                host_path, container_path
            )
        except OSError:
            ready = False
        if ready:
            logger.info(
                "已建立宿主机路径别名：%s -> %s（%s）",
                host_path,
                container_path,
                mapping.source,
            )
        return ready

    async def _warn_legacy_bind_mounts(self) -> None:
        """检测旧版本相对挂载落到容器 DATA_ROOT 路径的容器并记录告警。"""
        data_root_mapping = next(
            (item for item in self._mappings if item.source == "data-root"), None
        )
        if data_root_mapping is None:
            return
        from app.services.docker import docker_manager

        legacy = await docker_manager.run_sync(
            self._find_legacy_bind_mounts_sync, data_root_mapping.container_path
        )
        if not legacy:
            return
        projects = sorted({item["project"] for item in legacy})
        logger.warning(
            "检测到 %d 个旧版挂载仍指向宿主机 %s 下的目录（项目：%s）。"
            "这些目录不是当前数据目录 %s 下的内容；后续重新部署会使用新的"
            "宿主机工作区路径，请先在宿主机确认并迁移旧数据。",
            len(legacy),
            data_root_mapping.container_path,
            "、".join(projects[:10]),
            data_root_mapping.host_path,
        )

    def _find_legacy_bind_mounts_sync(self, container_data_root: str) -> list[dict]:
        """找出 Compose 容器中 bind source 仍位于旧 DATA_ROOT 下的挂载。"""
        from app.services.docker import docker_manager

        client = docker_manager._client
        if client is None:
            return []
        legacy: list[dict] = []
        try:
            containers = client.containers.list(all=True)
        except Exception as exc:
            logger.debug("检查旧版挂载失败：%s", exc)
            return []
        for container in containers:
            attrs = container.attrs or {}
            labels = (attrs.get("Config", {}) or {}).get("Labels") or {}
            project = labels.get("com.docker.compose.project")
            if not project:
                continue
            for mount in attrs.get("Mounts", []) or []:
                if mount.get("Type") != "bind":
                    continue
                source = _normalize_path(mount.get("Source"))
                if source != container_data_root and _is_under(
                    source, container_data_root
                ):
                    legacy.append(
                        {
                            "project": project,
                            "container": container.name,
                            "source": source,
                            "destination": mount.get("Destination") or "",
                        }
                    )
        return legacy


host_path_aliases = HostPathAliasService()
