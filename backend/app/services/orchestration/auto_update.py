"""自动更新：同步模板后升级 OneTap 自己部署的应用。

只处理 OneTap 通过应用市场或自动化方案创建、并且仍由 Compose 项目管理的
实例；用户在「容器编排」里手动创建的项目、以及导入的外部容器不在范围内。
与手动升级一致，自动更新只替换镜像：不新增应用、不改挂载目录与端口，
模板新增的应用留给用户手动部署。

只允许在系统设置指定的夜间时段运行（默认 02:00-06:00），每晚执行一次，
避免白天重启应用影响使用。
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.database import async_session
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.models.system_config import SystemConfig
from app.services.orchestration import orchestration_service
from app.services.orchestration.backup import (
    AppDataEntry,
    snapshot_app_data_dirs,
)
from app.services.orchestration.deploy_task_service import deploy_task_manager
from app.services.orchestration.events import add_update_event
from app.services.orchestration.upgrade import (
    _single_event_name,
    read_current_image,
    resolve_single_target_image,
    resolve_target_image,
)

logger = logging.getLogger(__name__)

TASK_TIMEOUT_SECONDS = 3600.0
"""单个升级任务的最长等待时间（拉镜像可能较慢）。"""

_run_lock = asyncio.Lock()
"""避免启动后的首轮更新与定时更新同时执行。"""


def in_update_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """判断当前时间是否落在自动更新的夜间时段内。

    支持跨零点窗口（如 22 点至次日 6 点）；起止相同时视为全天。
    """
    if start_hour == end_hour:
        return True
    hour = now.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def window_key(now: datetime, start_hour: int, end_hour: int) -> str:
    """返回当前夜间时段的标识（跨零点时归到起始那一天）。

    调度器用它保证同一晚只执行一轮自动更新。
    """
    day: date = now.date()
    if start_hour > end_hour and now.hour < end_hour:
        day -= timedelta(days=1)
    return day.isoformat()


async def _auto_backup_settings(db) -> tuple[bool, int]:
    """读取自动备份开关与保留份数（无配置记录时按默认值）。"""
    config = await db.get(SystemConfig, 1)
    if config is None:
        return False, 3
    return bool(config.auto_backup_enabled), int(config.auto_backup_keep or 3)


async def _snapshot_group_data(db, group: AppOrchestrationInstance) -> None:
    """自动升级前复制实例组的应用数据目录，失败只告警不阻断升级。"""
    enabled, keep = await _auto_backup_settings(db)
    if not enabled:
        return
    entries: list[AppDataEntry] = []
    for instance in group.instances:
        db_app = instance.app
        if db_app is None:
            continue
        project = (
            await db.get(DockerComposeProject, instance.project_id)
            if instance.project_id
            else None
        )
        entries.append(
            AppDataEntry(
                instance_name=instance.instance_name,
                app_name=db_app.name,
                project_name=project.project_name if project else "",
                config=instance.config or {},
            )
        )
    try:
        await snapshot_app_data_dirs(
            db, entries, label=group.instance_name, keep=keep
        )
    except Exception:
        logger.warning("自动备份实例组 %s 失败，继续升级", group.id, exc_info=True)


async def _snapshot_instance_data(db, instance: AppInstance) -> None:
    """自动升级前复制单个应用的数据目录，失败只告警不阻断升级。"""
    enabled, keep = await _auto_backup_settings(db)
    if not enabled:
        return
    project = (
        await db.get(DockerComposeProject, instance.project_id)
        if instance.project_id
        else None
    )
    entries = [
        AppDataEntry(
            instance_name=instance.instance_name,
            app_name=instance.app.name if instance.app else None,
            project_name=project.project_name if project else "",
            config=instance.config or {},
        )
    ]
    try:
        await snapshot_app_data_dirs(
            db, entries, label=instance.instance_name, keep=keep
        )
    except Exception:
        logger.warning(
            "自动备份应用实例 %s 失败，继续升级", instance.id, exc_info=True
        )


async def _selected_targets() -> set[str]:
    """读取用户勾选的自动更新服务；未配置时返回空集合。"""
    async with async_session() as db:
        config = await db.get(SystemConfig, 1)
        if config is None:
            return set()
        return {str(item) for item in (config.auto_update_targets or []) if item}


async def _pending_group_ids(selected: set[str]) -> list[int]:
    """返回已勾选、方案版本落后于模板且处于运行状态的实例组。"""
    async with async_session() as db:
        groups = (
            (
                await db.execute(
                    select(AppOrchestrationInstance).options(
                        selectinload(AppOrchestrationInstance.orchestration)
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            group.id
            for group in groups
            if f"group:{group.id}" in selected
            and group.orchestration is not None
            and group.orchestration_version != group.orchestration.version
            and group.status == "running"
        ]


async def _pending_app_ids(selected: set[str]) -> list[int]:
    """返回已勾选、版本落后于应用模板的单应用实例。

    跳过导入的外部容器（不受 OneTap 管理），以及上轮自动升级失败的版本，
    避免每晚自动更新反复重试同一版本；管理端发布新版本后自动恢复。
    """
    async with async_session() as db:
        instances = (
            (
                await db.execute(
                    select(AppInstance)
                    .where(
                        AppInstance.orchestration_group_id.is_(None),
                        AppInstance.app_id.is_not(None),
                        AppInstance.project_id.is_not(None),
                        AppInstance.status == "running",
                    )
                    .options(selectinload(AppInstance.app))
                )
            )
            .scalars()
            .all()
        )
        result: list[int] = []
        for instance in instances:
            if f"app:{instance.id}" not in selected:
                continue
            db_app = instance.app
            if db_app is None or instance.orchestration_version == db_app.version:
                continue
            config = instance.config or {}
            if config.get("imported"):
                continue
            if config.get("auto_update_failed_version") == db_app.version:
                continue
            result.append(instance.id)
        return result


async def _upgrade_group(group_id: int) -> str:
    """自动升级一个实例组，返回 upgraded / skipped / failed。"""
    async with async_session() as db:
        group = (
            await db.execute(
                select(AppOrchestrationInstance)
                .where(AppOrchestrationInstance.id == group_id)
                .options(
                    selectinload(AppOrchestrationInstance.orchestration),
                    selectinload(AppOrchestrationInstance.instances).selectinload(
                        AppInstance.app
                    ),
                )
            )
        ).scalar_one_or_none()
        if group is None:
            return "skipped"

        template = group.orchestration
        if template is None or group.orchestration_version == template.version:
            return "skipped"
        if group.status != "running":
            return "skipped"
        if deploy_task_manager.has_active_group_task(group.id):
            return "skipped"
        if any(
            deploy_task_manager.has_active_task(instance_id=instance.id)
            for instance in group.instances
        ):
            return "skipped"

        composition_by_name = {
            item.get("app_name"): item for item in (template.app_composition or [])
        }
        deployed_names = {
            instance.app.name for instance in group.instances if instance.app
        }
        # 模板新增了应用：自动更新不安装新应用，也不推进版本号，
        # 保留「可升级」提示让用户手动升级并确认新增内容
        missing_apps = sorted(set(composition_by_name) - deployed_names)
        if missing_apps:
            logger.info(
                "实例组 %s 有模板新增应用 %s，跳过自动更新，等待手动升级",
                group.id,
                ", ".join(missing_apps),
            )
            return "skipped"

        selected: list[str] = []
        for instance in group.instances:
            db_app = instance.app
            # 模板已移除的应用保留运行，不在自动更新范围内
            if db_app is None or db_app.name not in composition_by_name:
                continue
            if not instance.project_id:
                logger.info(
                    "实例组 %s 的应用 %s 缺少 Compose 项目，跳过本轮自动更新",
                    group.id,
                    db_app.name,
                )
                return "skipped"
            current_image = await read_current_image(db, instance, db_app)
            target_image = resolve_target_image(
                composition_by_name[db_app.name], db_app, instance.config
            )
            if current_image is None or not target_image:
                return "skipped"
            if current_image != target_image:
                selected.append(db_app.name)

        from_version = group.orchestration_version
        to_version = template.version
        if not selected:
            # 只有版本号变化、没有镜像变化：对齐版本快照，不操作容器
            group.orchestration_version = to_version
            add_update_event(
                db,
                orchestration_name=template.name,
                scope="instance",
                group_id=group.id,
                from_version=from_version,
                to_version=to_version,
                mode="auto",
                status="applied",
                detail={"mode": "version_only", "apps_added": []},
            )
            await db.commit()
            return "upgraded"

        await _snapshot_group_data(db, group)
        _, task_ids = await orchestration_service.upgrade_instance(
            db,
            group_id=group.id,
            selected_apps=selected,
            app_configs={},
            shared_config=group.shared_config or {},
            user_id=None,
            mode="auto",
        )
    status = await orchestration_service._wait_task_terminal(
        task_ids[0], timeout=TASK_TIMEOUT_SECONDS
    )
    return "upgraded" if status == "completed" else "failed"


async def _upgrade_app(instance_id: int) -> str:
    """自动升级一个单应用实例，返回 upgraded / skipped / failed。"""
    async with async_session() as db:
        instance = (
            await db.execute(
                select(AppInstance)
                .where(AppInstance.id == instance_id)
                .options(selectinload(AppInstance.app))
            )
        ).scalar_one_or_none()
        if instance is None:
            return "skipped"

        db_app = instance.app
        if db_app is None or instance.orchestration_version == db_app.version:
            return "skipped"
        if instance.status != "running":
            return "skipped"
        config = instance.config or {}
        if config.get("imported"):
            return "skipped"
        if config.get("auto_update_failed_version") == db_app.version:
            return "skipped"
        if deploy_task_manager.has_active_task(
            instance_id=instance.id, project_id=instance.project_id
        ):
            return "skipped"

        target_image = await resolve_single_target_image(db, instance, db_app)
        if not target_image:
            return "skipped"
        current_image = await read_current_image(db, instance, db_app)
        from_version = instance.orchestration_version

        if current_image == target_image:
            # 镜像已是最新：只对齐版本快照，避免下一轮重复检查
            instance.orchestration_version = db_app.version
            add_update_event(
                db,
                orchestration_name=_single_event_name(db_app.name),
                scope="instance",
                from_version=from_version,
                to_version=db_app.version,
                mode="auto",
                status="applied",
                detail={
                    "mode": "version_only",
                    "app_instance_id": instance.id,
                    "app_name": db_app.name,
                },
            )
            await db.commit()
            return "upgraded"

        await _snapshot_instance_data(db, instance)
        _, task_id = await orchestration_service.upgrade_single_instance(
            db,
            instance.id,
            mode="auto",
            target_image=target_image,
        )

    status = await orchestration_service._wait_task_terminal(
        task_id, timeout=TASK_TIMEOUT_SECONDS
    )
    return "upgraded" if status == "completed" else "failed"


async def run_auto_update() -> dict:
    """执行一轮自动更新，返回实例组与单应用的升级统计。"""
    if _run_lock.locked():
        return {"skipped": True}

    async with _run_lock:
        stats = {
            "groups": 0,
            "groups_skipped": 0,
            "groups_failed": 0,
            "apps": 0,
            "apps_skipped": 0,
            "apps_failed": 0,
        }

        selected = await _selected_targets()
        if not selected:
            logger.info("自动更新未勾选任何服务，跳过本轮")
            return {**stats, "skipped": True, "reason": "no_targets"}

        for group_id in await _pending_group_ids(selected):
            try:
                outcome = await _upgrade_group(group_id)
            except APIException as exc:
                logger.info("自动更新跳过实例组 %s：%s", group_id, exc.message)
                outcome = "skipped"
            except Exception:
                logger.exception("自动更新实例组 %s 失败", group_id)
                outcome = "failed"
            stats[_outcome_key("groups", outcome)] += 1

        for instance_id in await _pending_app_ids(selected):
            try:
                outcome = await _upgrade_app(instance_id)
            except APIException as exc:
                logger.info("自动更新跳过应用实例 %s：%s", instance_id, exc.message)
                outcome = "skipped"
            except Exception:
                logger.exception("自动更新应用实例 %s 失败", instance_id)
                outcome = "failed"
            stats[_outcome_key("apps", outcome)] += 1

        logger.info("自动更新完成：%s", stats)
        return stats


def _outcome_key(prefix: str, outcome: str) -> str:
    """把升级结果映射为统计字段名。"""
    if outcome == "failed":
        return f"{prefix}_failed"
    if outcome == "skipped":
        return f"{prefix}_skipped"
    return prefix
