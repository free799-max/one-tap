"""存储目录服务。

存储目录统一管理系统里的可挂载目录：
- 应用数据目录（is_app_data=True，全局至多一条）：存应用自身持久化小数据；
- 数据目录（多条）：供大数据量应用使用。

每条记录维护宿主机路径（compose bind source）与容器内路径（后端容器视角）
的对应关系，保存时会做唯一性、路径嵌套与容器可见性校验。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.storage_directory import StorageDirectory
from app.schemas.storage_directory import StorageDirectoryCreate, StorageDirectoryUpdate

logger = logging.getLogger(__name__)


async def _refresh_host_path_aliases(db: AsyncSession) -> None:
    """存储映射变更后补齐宿主机路径软链，失败不影响目录配置本身。"""
    try:
        from app.services.system.host_path_aliases import host_path_aliases

        await host_path_aliases.refresh(db)
    except Exception as exc:
        logger.warning("刷新宿主机路径别名失败：%s", exc)


def _normalize_path(path: str) -> str:
    """规范化绝对路径：去空白、去尾部斜杠（根目录除外）。"""
    value = (path or "").strip().rstrip("/")
    return value if value else "/"


def _paths_overlap(a: str, b: str) -> bool:
    """判断两个路径是否相同或互为父子路径前缀嵌套。"""
    pa, pb = Path(a), Path(b)
    try:
        pa.relative_to(pb)
        return True
    except ValueError:
        pass
    try:
        pb.relative_to(pa)
        return True
    except ValueError:
        return False


class StorageDirectoryService:
    """数据目录服务。"""

    async def list(self, db: AsyncSession) -> list[StorageDirectory]:
        """按 sort_order、id 排序列出全部数据目录。"""
        result = await db.execute(
            select(StorageDirectory).order_by(
                StorageDirectory.sort_order, StorageDirectory.id
            )
        )
        return list(result.scalars().all())

    async def list_enabled_pairs(self, db: AsyncSession) -> list[tuple[str, str]]:
        """列出启用的普通数据目录（不含应用数据目录）的 (host_path, mount_path) 列表。"""
        result = await db.execute(
            select(StorageDirectory)
            .where(StorageDirectory.enabled.is_(True))
            .where(StorageDirectory.is_app_data.is_(False))
            .order_by(StorageDirectory.sort_order, StorageDirectory.id)
        )
        return [(d.host_path, d.mount_path) for d in result.scalars().all()]

    async def get_app_data_pair(self, db: AsyncSession) -> tuple[str, str] | None:
        """获取启用的应用数据目录的 (host_path, mount_path)，未配置时返回 None。"""
        result = await db.execute(
            select(StorageDirectory)
            .where(StorageDirectory.enabled.is_(True))
            .where(StorageDirectory.is_app_data.is_(True))
            .order_by(StorageDirectory.id)
        )
        directory = result.scalars().first()
        if directory is None:
            return None
        return (directory.host_path, directory.mount_path)

    async def get(self, db: AsyncSession, dir_id: int) -> StorageDirectory:
        """获取单个数据目录，不存在时抛 404。"""
        result = await db.execute(
            select(StorageDirectory).where(StorageDirectory.id == dir_id)
        )
        directory = result.scalar_one_or_none()
        if directory is None:
            raise APIException("数据目录不存在", 404)
        return directory

    async def _validate_and_normalize(
        self,
        db: AsyncSession,
        name: str,
        host_path: str,
        mount_path: str,
        exclude_id: int | None = None,
    ) -> tuple[str, str]:
        """保存前校验并规范化路径，返回 (host_path, mount_path)。

        校验规则：
        - name 全局唯一；
        - host_path、mount_path 必须为绝对路径，规范化去掉尾部 /；
        - 不得与其他存储目录（含应用数据目录）的同类路径相同或互为父子路径嵌套；
        - mount_path 必须在后端容器内可见（已挂载进 backend 容器）。
        """
        name = (name or "").strip()
        stmt = select(StorageDirectory).where(StorageDirectory.name == name)
        if exclude_id is not None:
            stmt = stmt.where(StorageDirectory.id != exclude_id)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise APIException(f"数据目录名称 '{name}' 已存在", 400)

        host_path = _normalize_path(host_path)
        mount_path = _normalize_path(mount_path)
        if not host_path.startswith("/"):
            raise APIException("宿主机路径必须是以 / 开头的绝对路径", 400)
        if not mount_path.startswith("/"):
            raise APIException("容器内路径必须是以 / 开头的绝对路径", 400)

        # 与其他存储目录（含应用数据目录）的同类路径不得相同或嵌套
        result = await db.execute(select(StorageDirectory))
        for other in result.scalars().all():
            if exclude_id is not None and other.id == exclude_id:
                continue
            if _paths_overlap(host_path, other.host_path):
                raise APIException(
                    f"宿主机路径 {host_path} 与存储目录 '{other.name}' 的宿主机路径 "
                    f"{other.host_path} 相同或存在嵌套关系",
                    400,
                )
            if _paths_overlap(mount_path, other.mount_path):
                raise APIException(
                    f"容器内路径 {mount_path} 与存储目录 '{other.name}' 的容器内路径 "
                    f"{other.mount_path} 相同或存在嵌套关系",
                    400,
                )

        # 容器内路径必须在后端容器内可见
        if not os.path.isdir(mount_path):
            raise APIException(
                f"容器内路径 {mount_path} 在后端容器中不可见，请将宿主机目录挂载进 "
                f"backend 容器，例如在 docker-compose.yml 的 backend.volumes 中添加："
                f"- {host_path}:{mount_path}",
                400,
            )

        return host_path, mount_path

    async def _clear_other_app_data(
        self, db: AsyncSession, exclude_id: int | None = None
    ) -> None:
        """将其他记录的 is_app_data 置为 False，保证应用数据目录全局至多一条。"""
        stmt = update(StorageDirectory).values(is_app_data=False)
        if exclude_id is not None:
            stmt = stmt.where(StorageDirectory.id != exclude_id)
        await db.execute(stmt)

    async def create(
        self, db: AsyncSession, data: StorageDirectoryCreate
    ) -> StorageDirectory:
        """创建存储目录；is_app_data=True 时同事务内清除其他记录的应用数据目录标记。"""
        host_path, mount_path = await self._validate_and_normalize(
            db, data.name, data.host_path, data.mount_path
        )
        directory = StorageDirectory(
            name=data.name.strip(),
            host_path=host_path,
            mount_path=mount_path,
            remark=data.remark,
            enabled=data.enabled,
            is_app_data=data.is_app_data,
            sort_order=data.sort_order,
        )
        db.add(directory)
        await db.flush()
        if data.is_app_data:
            await self._clear_other_app_data(db, exclude_id=directory.id)
        await db.flush()
        await db.refresh(directory)
        await _refresh_host_path_aliases(db)
        return directory

    async def update(
        self, db: AsyncSession, dir_id: int, data: StorageDirectoryUpdate
    ) -> StorageDirectory:
        """更新存储目录（未提供的字段保持不变，按合并后的值重新校验）。

        is_app_data=True 时同事务内清除其他记录的应用数据目录标记。
        """
        directory = await self.get(db, dir_id)

        updates = data.model_dump(exclude_unset=True)
        if not updates:
            return directory

        name = updates.get("name", directory.name)
        host_path, mount_path = await self._validate_and_normalize(
            db,
            name,
            updates.get("host_path", directory.host_path),
            updates.get("mount_path", directory.mount_path),
            exclude_id=directory.id,
        )

        directory.name = name.strip()
        directory.host_path = host_path
        directory.mount_path = mount_path
        if "remark" in updates:
            directory.remark = updates["remark"]
        if "enabled" in updates:
            directory.enabled = updates["enabled"]
        if "is_app_data" in updates:
            directory.is_app_data = updates["is_app_data"]
        if "sort_order" in updates:
            directory.sort_order = updates["sort_order"]

        await db.flush()
        if directory.is_app_data:
            await self._clear_other_app_data(db, exclude_id=directory.id)
            await db.flush()
        await db.refresh(directory)
        await _refresh_host_path_aliases(db)
        return directory

    async def delete(self, db: AsyncSession, dir_id: int) -> None:
        """删除数据目录，不存在时抛 404。"""
        directory = await self.get(db, dir_id)
        await db.delete(directory)
        await db.flush()
        await _refresh_host_path_aliases(db)

    def to_dict(self, directory: StorageDirectory) -> dict:
        """将存储目录对象转换为响应字典，附带容器可见性计算字段。"""
        return {
            "id": directory.id,
            "name": directory.name,
            "host_path": directory.host_path,
            "mount_path": directory.mount_path,
            "remark": directory.remark,
            "enabled": directory.enabled,
            "is_app_data": directory.is_app_data,
            "sort_order": directory.sort_order,
            "visible": os.path.isdir(directory.mount_path),
            "created_at": directory.created_at,
            "updated_at": directory.updated_at,
        }


async def get_browse_roots(db: AsyncSession) -> list[str]:
    """获取目录浏览允许的根目录集合。

    根目录集合 = 所有启用且在后端容器内可见的存储目录（含应用数据目录）
    的 mount_path；一个都不可用时抛出异常。
    """
    roots = [
        directory.mount_path
        for directory in await storage_directory_service.list(db)
        if directory.enabled and os.path.isdir(directory.mount_path)
    ]
    if not roots:
        raise APIException("未配置存储目录，请前往系统设置-存储配置", 400)
    return roots


# 全局服务单例
storage_directory_service = StorageDirectoryService()
