"""授权版自动更新：同步模板后升级 OneTap 自己部署的应用。

只处理 OneTap 通过应用市场或自动化方案创建、并且仍由 Compose 项目管理的
实例；用户在「容器编排」里手动创建的项目、以及导入的外部容器不在范围内。
与手动升级一致，自动更新只替换镜像：不新增应用、不改挂载目录与端口，
模板新增的应用留给用户手动部署。
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.database import async_session
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.services.orchestration import orchestration_service
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


async def _pending_group_ids() -> list[int]:
    """返回方案版本落后于模板、且处于运行状态的实例组。"""
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
            if group.orchestration is not None
            and group.orchestration_version != group.orchestration.version
            and group.status == "running"
        ]


async def _pending_app_ids() -> list[int]:
    """返回版本落后于应用模板的单应用实例。

    跳过导入的外部容器（不受 OneTap 管理），以及上轮自动升级失败的版本，
    避免同一版本每 30 分钟反复重试；管理端发布新版本后自动恢复。
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

        for group_id in await _pending_group_ids():
            try:
                outcome = await _upgrade_group(group_id)
            except APIException as exc:
                logger.info("自动更新跳过实例组 %s：%s", group_id, exc.message)
                outcome = "skipped"
            except Exception:
                logger.exception("自动更新实例组 %s 失败", group_id)
                outcome = "failed"
            stats[_outcome_key("groups", outcome)] += 1

        for instance_id in await _pending_app_ids():
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
