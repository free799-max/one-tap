"""删除编排实例组时的应用数据目录备份。

删除实例组时，各应用自身的持久化数据（宿主机上位于应用数据目录下、
以 Compose 项目名命名的目录）不会被 ``docker compose down`` 清理。
若直接保留，同名重建会复用旧数据（如 PostgreSQL 密码已初始化），
导致新部署无法连接数据库。

因此删除时把这些目录移入备份目录而不是留在原地：

- 备份目录固定为 ``<应用数据目录>/_backups/<时间戳>-<实例组名>/``；
- 每个应用的数据目录按原 Compose 项目名移动，保留内部结构；
- 只处理应用自身的数据目录，共享存储（媒体/下载）与应用数据目录之外的
  路径保持不动；
- 备份目录内写入 ``backup.json`` 清单，记录来源、各目录映射与部署配置，
  用于“恢复”时把目录移回并按原配置重新部署。
"""

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)

# 备份目录名（位于应用数据目录下）
BACKUP_DIR_NAME = "_backups"
MANIFEST_NAME = "backup.json"

_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class AppDataEntry:
    """一个应用实例对应的数据目录信息。"""

    instance_name: str
    app_name: str | None
    project_name: str
    config: dict | None = None


@dataclass(frozen=True)
class BackupFailure:
    """一个未能移入备份目录的应用数据目录（原数据仍留在原路径）。"""

    instance_name: str
    project_name: str
    reason: str


def _safe_name(value: str) -> str:
    """将实例组名转换为可安全用于目录名的片段。"""
    cleaned = _SAFE_NAME_PATTERN.sub("-", value.strip()).strip("-.")
    return cleaned[:60] or "instance"


def _backup_destination(root: Path, group_name: str, now: datetime) -> Path:
    """生成不冲突的备份目录路径。"""
    base = f"{now.strftime('%Y%m%d-%H%M%S')}-{_safe_name(group_name)}"
    candidate = root / BACKUP_DIR_NAME / base
    index = 2
    while candidate.exists():
        candidate = root / BACKUP_DIR_NAME / f"{base}-{index}"
        index += 1
    return candidate


async def resolve_app_data_paths(db: AsyncSession) -> tuple[Path, Path] | None:
    """读取应用数据目录的宿主机路径与后端容器内路径。"""
    pair = await storage_directory_service.get_app_data_pair(db)
    if not pair:
        return None
    host_path = (pair[0] or "").strip()
    mount_path = (pair[1] or "").strip()
    if not host_path or not mount_path:
        return None
    return Path(host_path), Path(mount_path)


async def resolve_app_data_root(db: AsyncSession) -> Path | None:
    """读取后端容器内可见的应用数据目录；未配置时返回 None。"""
    paths = await resolve_app_data_paths(db)
    return paths[1] if paths else None


async def backup_app_data_dirs(
    db: AsyncSession,
    entries: list[AppDataEntry],
    group_name: str,
    orchestration_name: str | None = None,
    shared_config: dict | None = None,
    app_configs: dict | None = None,
) -> tuple[Path | None, list[BackupFailure]]:
    """把应用自身的数据目录移入备份目录。

    只移动应用数据目录下、以 Compose 项目名命名的直接子目录；共享存储
    与应用数据目录之外的路径不动。移动使用 ``os.rename``（同文件系统的
    原子操作）：失败就是整体失败，不会像 ``shutil.move`` 那样在 rename
    被拒后退化为半截的复制，把旧数据留在原地、备份里却只有空壳。

    Returns:
        ``(backup_dir, failures)``：没有任何目录被移动时 backup_dir 为
        None；failures 记录移动失败的目录，其数据仍保留在原路径，
        调用方必须提示用户，否则同名重建会静默复用旧数据。
    """
    paths = await resolve_app_data_paths(db)
    if paths is None:
        logger.warning("未配置应用数据目录，跳过实例组 %s 的数据备份", group_name)
        return None, []
    host_root, root = paths

    try:
        root_resolved = root.resolve()
    except OSError:
        logger.warning("应用数据目录不可访问，跳过数据备份: %s", root)
        return None, []

    now = datetime.now()
    backup_dir: Path | None = None
    moved: list[dict] = []
    failures: list[BackupFailure] = []
    seen: set[str] = set()

    for entry in entries:
        project_name = (entry.project_name or "").strip()
        if not project_name or project_name in seen:
            continue
        seen.add(project_name)

        source = root / project_name
        try:
            if source.is_symlink() or not source.is_dir():
                continue
            if source.resolve().parent != root_resolved:
                continue
        except OSError:
            continue

        if backup_dir is None:
            backup_dir = _backup_destination(root, group_name, now)
        destination = backup_dir / project_name
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            await asyncio.to_thread(os.rename, str(source), str(destination))
        except OSError as exc:
            logger.warning(
                "备份应用数据目录失败 %s -> %s: %s", source, destination, exc
            )
            # rename 失败不会留下半成品；防御性清理可能残留的目标目录
            try:
                if destination.exists():
                    await asyncio.to_thread(shutil.rmtree, str(destination))
            except OSError:
                pass
            failures.append(
                BackupFailure(
                    instance_name=entry.instance_name,
                    project_name=project_name,
                    reason=str(exc),
                )
            )
            continue

        moved.append(
            {
                "instance_name": entry.instance_name,
                "app_name": entry.app_name,
                "project_name": project_name,
                "path": destination.name,
                "config": entry.config or {},
            }
        )
        logger.info("应用数据目录已备份: %s -> %s", source, destination)

    if backup_dir is None or not moved:
        if backup_dir is not None:
            # 清掉本次创建的空备份目录；父目录 _backups 若已空也一并移除
            for path in (backup_dir, backup_dir.parent):
                try:
                    path.rmdir()
                except OSError:
                    break
        return None, failures

    manifest = {
        "instance_name": group_name,
        "orchestration": orchestration_name,
        "deleted_at": now.isoformat(timespec="seconds"),
        "app_data_root": str(host_root),
        "shared_config": shared_config or {},
        "app_configs": app_configs or {},
        "apps": moved,
    }
    try:
        (backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("写入备份清单失败 %s: %s", backup_dir, exc)

    return backup_dir, failures


def _read_manifest(backup_dir: Path) -> dict | None:
    """读取备份清单；文件缺失或损坏时返回 None。"""
    manifest_path = backup_dir / MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def list_app_backups(
    db: AsyncSession,
    orchestration_name: str | None = None,
) -> list[dict]:
    """列出可恢复的备份，按备份目录名（时间戳）倒序。

    返回 ``{"name", "manifest"}`` 列表；仅包含应用数据目录下 ``_backups``
    中带合法清单的目录。可按编排名过滤。
    """
    root = await resolve_app_data_root(db)
    if root is None:
        return []
    base = root / BACKUP_DIR_NAME
    if not base.is_dir():
        return []

    backups: list[dict] = []
    for entry in sorted(base.iterdir(), reverse=True):
        if not entry.is_dir() or entry.is_symlink():
            continue
        manifest = _read_manifest(entry)
        if manifest is None:
            continue
        if orchestration_name and manifest.get("orchestration") != orchestration_name:
            continue
        backups.append({"name": entry.name, "manifest": manifest})
    return backups


def _validate_backup_name(backup_name: str) -> str:
    """校验备份目录名，阻止路径穿越。"""
    name = (backup_name or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise APIException("备份名称无效", 400)
    return name


async def restore_app_data_dirs(
    db: AsyncSession,
    backup_name: str,
    target_projects: dict[str, str],
) -> tuple[dict, list[dict]]:
    """把备份中的应用数据目录移回应用数据目录。

    Args:
        backup_name: ``_backups`` 下的备份目录名
        target_projects: 应用名 -> 目标 Compose 项目名

    Returns:
        ``(manifest, restored)``，restored 为已移动目录的映射列表

    Raises:
        APIException: 未配置应用数据目录、备份不存在或目标目录已存在
    """
    root = await resolve_app_data_root(db)
    if root is None:
        raise APIException("未配置应用数据目录，无法恢复备份", 400)

    name = _validate_backup_name(backup_name)
    backup_dir = root / BACKUP_DIR_NAME / name
    manifest = _read_manifest(backup_dir) if backup_dir.is_dir() else None
    if manifest is None:
        raise APIException("备份不存在或清单已损坏", 404)

    try:
        backup_resolved = backup_dir.resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise APIException("备份目录不可访问", 400) from exc

    # 先校验全部目标路径，避免移动到一半才失败
    moves: list[tuple[Path, Path]] = []
    restored: list[dict] = []
    for entry in manifest.get("apps") or []:
        if not isinstance(entry, dict):
            continue
        app_name = str(entry.get("app_name") or "")
        project_name = target_projects.get(app_name)
        if not project_name:
            continue
        source = backup_dir / str(entry.get("path") or entry.get("project_name") or "")
        try:
            if source.is_symlink() or not source.is_dir():
                continue
            if source.resolve().parent != backup_resolved:
                continue
        except OSError:
            continue
        target = root / project_name
        if target.exists() or target.is_symlink():
            raise APIException(
                f"目标数据目录已存在，无法恢复：{target}", 409
            )
        if target.resolve().parent != root_resolved:
            raise APIException(f"目标路径非法：{target}", 400)
        moves.append((source, target))
        restored.append(
            {
                "app_name": app_name,
                "instance_name": entry.get("instance_name"),
                "source": str(source),
                "target": str(target),
            }
        )

    if not moves:
        raise APIException("备份中没有可恢复的应用数据", 400)

    done: list[tuple[Path, Path]] = []
    try:
        for source, target in moves:
            await asyncio.to_thread(os.rename, str(source), str(target))
            done.append((source, target))
    except OSError as exc:
        # 尽力回滚已移动的目录，保持备份完整
        for source, target in reversed(done):
            try:
                await asyncio.to_thread(os.rename, str(target), str(source))
            except OSError:
                logger.exception("回滚恢复的数据目录失败: %s -> %s", target, source)
        raise APIException(f"恢复应用数据失败: {exc}", 500) from exc

    logger.info("已从备份 %s 恢复 %d 个应用数据目录", name, len(done))
    # 所有数据目录都已移回时，备份已被消费，清理清单与空目录
    try:
        remaining = [
            item for item in backup_dir.iterdir() if item.name != MANIFEST_NAME
        ]
        if not remaining:
            await asyncio.to_thread(shutil.rmtree, str(backup_dir))
    except OSError:
        pass
    return manifest, restored
