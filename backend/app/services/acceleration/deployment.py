"""通过应用市场 Compose 流程部署和删除 OneTap 托管的 Mihomo。"""

from __future__ import annotations

import asyncio
import copy
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.services.acceleration import mihomo
from app.services.compose import compose_manager
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)


def _schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(prop["default"])
        for key, prop in (schema.get("properties") or {}).items()
        if isinstance(prop, dict) and "default" in prop
    }


async def _load_app(db: AsyncSession) -> App:
    app = await db.scalar(select(App).where(App.name == mihomo.APP_NAME))
    if app is None or not app.yaml_template:
        raise APIException("应用市场未找到 Mihomo，请先同步应用市场", 400)
    return app


def _copy_previous_config(previous: Path, target: Path) -> None:
    if previous == target or not previous.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in ("config.yaml", "config.yaml.bak", "state.json"):
        source = previous / name
        destination = target / name
        if source.exists() and not destination.exists():
            shutil.copy2(source, destination)


async def _wait_app_deploy(task_id: str, outer_task_id: str) -> None:
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    async with asyncio.timeout(300):
        while True:
            task = deploy_task_manager.get_task(task_id)
            if task is None:
                raise RuntimeError("Mihomo 应用部署任务不存在")
            status = task.get("status")
            if status == "completed":
                return
            if status in {"failed", "cancelled"}:
                raise RuntimeError(task.get("error") or "Mihomo 应用部署已终止")
            progress = task.get("progress") or {}
            deploy_task_manager.update_progress(
                outer_task_id,
                str(progress.get("stage") or "starting_services"),
                min(90, int(5 + 85 * (progress.get("percentage") or 0) / 100)),
                str(progress.get("message") or "部署 Mihomo"),
                progress.get("detail"),
            )
            await asyncio.sleep(0.5)


async def _sync_subscription_after_deploy(db: AsyncSession) -> str:
    """部署完成后立刻拉取一次管理端订阅，返回附加到任务消息的说明。"""
    from app.services.acceleration import managed
    from app.services.system.config_service import system_config_service

    config = await system_config_service.get_or_create(db)
    if not config.acceleration_subscription_url:
        return ""
    try:
        result = await managed.sync(db, force=True, trigger="deploy")
        await db.commit()
        rules = int(result["detail"].get("rules") or 0)
        return f"，已同步订阅（{rules} 条规则）"
    except Exception as exc:
        await db.rollback()
        logger.warning("Mihomo 部署后同步订阅失败: %s", exc)
        return f"，订阅同步失败：{exc}"


async def _finish_deploy(outer_task_id: str, app_task_id: str) -> None:
    from app.database import async_session
    from app.models.orchestration import AppInstance
    from app.services.acceleration import apps as accel_apps
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    try:
        await _wait_app_deploy(app_task_id, outer_task_id)
        async with async_session() as db:
            status = await mihomo.status_async(db)
            if not status.get("running"):
                raise RuntimeError(status.get("error") or "Mihomo 部署后未正常运行")

            sync_note = await _sync_subscription_after_deploy(db)

            result = await db.execute(
                select(AppInstance.id).where(AppInstance.proxy_enabled.is_(True))
            )
            instance_ids = list(result.scalars())
            for instance_id in instance_ids:
                await accel_apps.set_proxy(db, instance_id, True)
                await db.commit()
        deploy_task_manager.complete_task(
            outer_task_id,
            message="Mihomo 已部署"
            + sync_note
            + (f"，已恢复 {len(instance_ids)} 个应用的加速" if instance_ids else ""),
        )
    except Exception as exc:
        logger.exception("Mihomo 部署任务失败: %s", outer_task_id)
        deploy_task_manager.fail_task(outer_task_id, str(exc))


async def start_deploy(
    db: AsyncSession, app_config: dict[str, Any] | None = None
) -> str:
    """准备持久化配置并返回统一应用部署任务 ID。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    for task_id in deploy_task_manager.list_active_task_ids():
        task = deploy_task_manager.get_task(task_id) or {}
        if (task.get("meta") or {}).get("operation") == "mihomo_deploy":
            return task_id

    pair = await storage_directory_service.get_app_data_pair(db)
    if not pair:
        raise APIException(
            "部署 Mihomo 需要先配置应用数据目录（系统设置 → 存储配置）", 400
        )
    app = await _load_app(db)
    config = (
        copy.deepcopy(app_config)
        if app_config is not None
        else _schema_defaults(app.config_schema or {})
    )

    previous = await mihomo.config_dir(db, create=False)
    target = await mihomo.config_dir(db, config=config)
    await asyncio.to_thread(_copy_previous_config, previous, target)
    config_file = target / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            yaml.safe_dump(mihomo.minimal_config(), allow_unicode=True, sort_keys=False),
            "utf-8",
        )

    project = await db.scalar(
        select(DockerComposeProject).where(
            DockerComposeProject.project_name == mihomo.PROJECT_NAME
        )
    )
    if project is not None:
        await compose_manager.delete_project(db, project, backup_data=False)
    else:
        await mihomo.remove_legacy_container()

    from sqlalchemy import delete
    from app.models.orchestration import AppInstance

    await db.execute(
        delete(AppInstance).where(AppInstance.instance_name == mihomo.PROJECT_NAME)
    )
    await db.commit()

    from app.core.background import create_background_task
    from app.services.app_store import app_service
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    _, app_task_id = await app_service.deploy(
        db,
        app_name=app.name,
        instance_name=mihomo.PROJECT_NAME,
        config=config,
    )
    outer_task_id = deploy_task_manager.create_task(
        "mihomo_deploy",
        meta={
            "app_name": "Mihomo",
            "instance_name": mihomo.PROJECT_NAME,
            "description": "部署 OneTap 网络加速专用 Mihomo",
            "operation": "mihomo_deploy",
        },
        enforce_limit=False,
    )
    create_background_task(_finish_deploy(outer_task_id, app_task_id))
    return outer_task_id


def _backup_directory(directory: Path, mount_base: str) -> None:
    if not directory.exists():
        return
    backup = (
        Path(mount_base)
        / "_backups"
        / f"{datetime.now():%Y%m%d%H%M%S}-{mihomo.PROJECT_NAME}"
        / "config"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(directory), str(backup))


async def delete_managed(db: AsyncSession) -> dict[str, Any]:
    """先恢复应用网络，再删除 Compose 项目并备份持久化数据。"""
    from app.services.acceleration import apps as accel_apps

    disabled = await accel_apps.disable_all(db)
    project = await db.scalar(
        select(DockerComposeProject).where(
            DockerComposeProject.project_name == mihomo.PROJECT_NAME
        )
    )
    if project is not None:
        await compose_manager.delete_project(db, project, backup_data=True)
    else:
        directory = await mihomo.config_dir(db, create=False)
        pair = await storage_directory_service.get_app_data_pair(db)
        if pair and directory.exists():
            await asyncio.to_thread(_backup_directory, directory, pair[1])
        await mihomo.remove_legacy_container()

    from sqlalchemy import delete
    from app.models.orchestration import AppInstance

    await db.execute(
        delete(AppInstance).where(AppInstance.instance_name == mihomo.PROJECT_NAME)
    )
    await db.commit()

    message = "Mihomo 已删除，数据已备份"
    if disabled:
        message += f"，已关闭 {len(disabled)} 个应用的加速"
    return {
        "removed": True,
        "disabled_apps": disabled,
        "warnings": [],
        "message": message,
    }
