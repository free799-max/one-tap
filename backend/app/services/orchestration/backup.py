"""删除实例组或 Compose 项目时的应用数据目录备份。

删除实例组或 Compose 项目时，各应用自身的持久化数据（宿主机上位于应用
数据目录下、按实例名或 Compose 项目名命名的目录）不会被
``docker compose down`` 清理。若直接保留，同名重建会复用旧数据
（如 PostgreSQL 密码、AdGuard 管理员密码已初始化），导致新部署失败。

因此删除时把这些目录移入备份目录而不是留在原地：

- 备份目录固定为 ``<应用数据目录>/_backups/<时间戳>-<实例组名>/``；
- 新布局的应用数据目录形如 ``<实例名>/<应用名>/...``，按实例名目录整体移动；
  旧布局按 Compose 项目名目录移动，两种都保留内部结构；
- 只处理应用配置里声明的数据目录，共享存储（媒体/下载）与应用数据目录
  之外的路径保持不动；
- 备份目录内写入 ``backup.json`` 清单，记录来源、各目录映射与部署配置，
  用于“恢复”时把目录移回并按原配置重新部署。

自动升级前的快照（``snapshot_app_data_dirs``）走另一条路径：复制而不是
移动应用数据目录到 ``_backups/auto-update/``，原目录保持可用，只用于升级
失败时回滚。
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
AUTO_BACKUP_DIR_NAME = "auto-update"
"""自动升级前快照的存放目录（位于 ``_backups`` 下，与删除备份隔离）。"""

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


def _managed_dir_names(entry: "AppDataEntry", root: Path) -> list[str]:
    """从应用配置的 volumes 推导应用数据目录下的直接子目录名。

    新部署的挂载路径形如 ``<应用数据目录>/<实例名>/<应用名>/<子目录>``，
    旧部署是 ``<应用数据目录>/<Compose 项目名>/...``。这里取每条绝对挂载
    相对应用数据目录的首段，供备份时整体移动这些目录；相对路径由调用方
    按 Compose 项目名兼容处理。
    """
    root_str = str(root).rstrip("/")
    names: list[str] = []
    volumes = (entry.config or {}).get("volumes")
    if not isinstance(volumes, list):
        return names
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        host = str(volume.get("host_path") or "").strip().rstrip("/")
        if not host or not host.startswith("/"):
            continue
        if not root_str or root_str == "/":
            rel = host.strip("/")
        elif host.startswith(f"{root_str}/"):
            rel = host[len(root_str) :].strip("/")
        else:
            continue
        if not rel:
            continue
        first = rel.split("/", 1)[0]
        if first and first != BACKUP_DIR_NAME and first not in names:
            names.append(first)
    return names


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
    instance_dir_name: str | None = None

    for entry in entries:
        project_name = (entry.project_name or "").strip()
        # 候选目录：旧布局的 Compose 项目名目录 + 新布局按实例名组织的首段目录
        candidates: list[tuple[str, str]] = []
        if project_name:
            candidates.append((project_name, "project"))
        for name in _managed_dir_names(entry, root):
            if name != project_name:
                candidates.append((name, "instance"))

        for dir_name, kind in candidates:
            if dir_name in seen:
                continue
            seen.add(dir_name)

            source = root / dir_name
            try:
                if source.is_symlink() or not source.is_dir():
                    continue
                if source.resolve().parent != root_resolved:
                    continue
            except OSError:
                continue

            if backup_dir is None:
                backup_dir = _backup_destination(root, group_name, now)
            destination = backup_dir / dir_name
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
                        project_name=dir_name,
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
                    "kind": kind,
                    "config": entry.config or {},
                }
            )
            if kind == "instance":
                instance_dir_name = dir_name
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
        "instance_dir": instance_dir_name,
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


def _auto_backup_base(root: Path) -> Path:
    """自动升级快照的根目录（``_backups/auto-update``）。"""
    return root / BACKUP_DIR_NAME / AUTO_BACKUP_DIR_NAME


def _prune_auto_backups(base: Path, keep: int) -> None:
    """只保留最近的 ``keep`` 份自动备份，其余删除。"""
    limit = max(1, keep)
    try:
        dirs = sorted(
            (item for item in base.iterdir() if item.is_dir() and not item.is_symlink()),
            key=lambda item: item.name,
            reverse=True,
        )
    except OSError:
        return
    for stale in dirs[limit:]:
        try:
            shutil.rmtree(stale)
            logger.info("已清理过期自动备份: %s", stale)
        except OSError as exc:
            logger.warning("清理过期自动备份失败 %s: %s", stale, exc)


async def snapshot_app_data_dirs(
    db: AsyncSession,
    entries: list[AppDataEntry],
    label: str,
    keep: int = 3,
) -> Path | None:
    """自动升级前把应用数据目录**复制**一份，返回快照目录。

    与删除时的 ``backup_app_data_dirs`` 不同，这里用复制而不是移动：应用
    升级后仍需使用原目录，快照只用于升级失败时回滚。快照保存在
    ``<应用数据目录>/_backups/auto-update/<时间戳>-<实例名>/``，写入
    ``backup.json`` 清单；只处理应用配置里声明的、位于应用数据目录下的
    目录，共享存储（媒体/下载）不复制。超过 ``keep`` 份的最旧快照会被删掉。
    """
    paths = await resolve_app_data_paths(db)
    if paths is None:
        logger.info("未配置应用数据目录，跳过 %s 的自动备份", label)
        return None
    _, root = paths

    try:
        root_resolved = root.resolve()
    except OSError:
        logger.warning("应用数据目录不可访问，跳过自动备份: %s", root)
        return None

    base = _auto_backup_base(root)
    now = datetime.now()
    name = f"{now.strftime('%Y%m%d-%H%M%S')}-{_safe_name(label)}"
    destination_root = base / name
    index = 2
    while destination_root.exists():
        destination_root = base / f"{name}-{index}"
        index += 1

    moved: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        project_name = (entry.project_name or "").strip()
        candidates: list[tuple[str, str]] = []
        if project_name:
            candidates.append((project_name, "project"))
        for dir_name in _managed_dir_names(entry, root):
            if dir_name != project_name:
                candidates.append((dir_name, "instance"))

        for dir_name, kind in candidates:
            if dir_name in seen or dir_name == BACKUP_DIR_NAME:
                continue
            seen.add(dir_name)

            source = root / dir_name
            try:
                if source.is_symlink() or not source.is_dir():
                    continue
                if source.resolve().parent != root_resolved:
                    continue
            except OSError:
                continue

            target = destination_root / dir_name
            try:
                await asyncio.to_thread(
                    shutil.copytree,
                    str(source),
                    str(target),
                    symlinks=True,
                )
            except (OSError, shutil.Error) as exc:
                logger.warning(
                    "自动备份应用数据目录失败 %s -> %s: %s", source, target, exc
                )
                try:
                    if target.exists():
                        await asyncio.to_thread(shutil.rmtree, str(target))
                except OSError:
                    pass
                continue

            moved.append(
                {
                    "instance_name": entry.instance_name,
                    "app_name": entry.app_name,
                    "project_name": project_name,
                    "path": target.name,
                    "kind": kind,
                    "config": entry.config or {},
                }
            )
            logger.info("自动备份应用数据目录: %s -> %s", source, target)

    if not moved:
        try:
            if destination_root.exists():
                await asyncio.to_thread(shutil.rmtree, str(destination_root))
        except OSError:
            pass
        return None

    manifest = {
        "instance_name": label,
        "auto": True,
        "reason": "auto_update",
        "created_at": now.isoformat(timespec="seconds"),
        "apps": moved,
    }
    try:
        (destination_root / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("写入自动备份清单失败 %s: %s", destination_root, exc)

    _prune_auto_backups(base, keep)
    return destination_root


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
    target_instance: str | None = None,
) -> tuple[dict, list[dict]]:
    """把备份中的应用数据目录移回应用数据目录。

    Args:
        backup_name: ``_backups`` 下的备份目录名
        target_projects: 应用名 -> 目标 Compose 项目名（旧布局按项目名恢复）
        target_instance: 目标实例名；新布局的``<实例名>/...`` 目录按它重命名，
            为空时沿用备份中的实例名

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
    handled: set[str] = set()

    # 新布局：整个实例名目录移回（恢复时可改名为新的实例名）
    instance_dir = str(manifest.get("instance_dir") or "").strip()
    if instance_dir:
        if Path(instance_dir).name != instance_dir or instance_dir in {".", ".."}:
            raise APIException("备份中的实例目录名无效", 400)
        instance_target = (target_instance or instance_dir).strip()
        if (
            not instance_target
            or Path(instance_target).name != instance_target
            or instance_target in {".", ".."}
        ):
            raise APIException("目标实例名称无效", 400)
        source = backup_dir / instance_dir
        try:
            valid = (
                not source.is_symlink()
                and source.is_dir()
                and source.resolve().parent == backup_resolved
            )
        except OSError:
            valid = False
        if valid:
            target = root / instance_target
            if target.exists() or target.is_symlink():
                raise APIException(f"目标数据目录已存在，无法恢复：{target}", 409)
            if target.resolve().parent != root_resolved:
                raise APIException(f"目标路径非法：{target}", 400)
            moves.append((source, target))
            restored.append(
                {
                    "app_name": None,
                    "instance_name": manifest.get("instance_name"),
                    "source": str(source),
                    "target": str(target),
                }
            )
            handled.add(instance_dir)

    for entry in manifest.get("apps") or []:
        if not isinstance(entry, dict):
            continue
        app_name = str(entry.get("app_name") or "")
        project_name = target_projects.get(app_name)
        record_path = str(entry.get("path") or entry.get("project_name") or "")
        if record_path in handled:
            continue
        if not project_name:
            continue
        source = backup_dir / record_path
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
