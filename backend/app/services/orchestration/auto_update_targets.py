"""自动更新服务候选列表。

自动更新只允许更新用户在设置里明确勾选的服务。候选分两类：

- ``group:<id>``：一次组合部署（AppOrchestrationInstance），按方案整体升级；
- ``app:<id>``：应用市场单独部署的应用实例（AppInstance）。

导入的外部容器、未关联 Compose 项目的实例不可选；组合部署与多容器应用
会标记为复杂，前端提示用户只更新主服务镜像。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.app_store import App
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.services.orchestration.external import is_external_app


def _container_count(db_app: App) -> int:
    """从 config_schema 读取应用声明的容器数量；解析失败按单容器处理。"""
    schema = db_app.config_schema or {}
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError:
            return 1
    if not isinstance(schema, dict):
        return 1
    containers = schema.get("containers")
    if isinstance(containers, list) and containers:
        return len(containers)
    return 1


def _version_fields(current: str | None, target: str | None) -> dict:
    return {
        "current_version": current,
        "target_version": target,
        "upgrade_available": bool(current and target and current != target),
    }


def _usable_icon(icon: str | None) -> str | None:
    """只接受外链或前端静态路径，其他格式交给静态图标回退。"""
    value = (icon or "").strip()
    if value.startswith("http://") or value.startswith("https://") or value.startswith("/"):
        return value
    return None


async def list_auto_update_targets(db: AsyncSession) -> list[dict]:
    """返回当前可配置自动更新的组合与应用实例。"""
    targets: list[dict] = []

    groups = (
        (
            await db.execute(
                select(AppOrchestrationInstance)
                .options(
                    selectinload(AppOrchestrationInstance.orchestration),
                    selectinload(AppOrchestrationInstance.instances).selectinload(
                        AppInstance.app
                    ),
                )
                .order_by(AppOrchestrationInstance.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    for group in groups:
        template = group.orchestration
        if template is None:
            continue
        apps = [instance.app for instance in group.instances if instance.app]
        app_names = [app.display_name or app.name for app in apps]
        first_app = apps[0] if apps else None
        missing_project = any(
            instance.project_id is None for instance in group.instances
        )
        complex_target = len(app_names) > 1
        reason = None
        if missing_project:
            reason = "存在未关联 Compose 项目的应用，无法自动更新"
        elif complex_target:
            reason = "组合部署，将按方案升级所选应用的主服务镜像"
        targets.append(
            {
                "key": f"group:{group.id}",
                "type": "group",
                "id": group.id,
                "name": group.instance_name,
                "display_name": template.display_name or template.name,
                "icon": _usable_icon(template.icon)
                or (first_app.icon if first_app else None),
                "icon_name": first_app.name if first_app else group.instance_name,
                "status": group.status,
                "app_count": len(app_names),
                "apps": app_names,
                "complex": complex_target,
                "selectable": not missing_project,
                "reason": reason,
                **_version_fields(group.orchestration_version, template.version),
            }
        )

    instances = (
        (
            await db.execute(
                select(AppInstance)
                .where(
                    AppInstance.orchestration_group_id.is_(None),
                    AppInstance.app_id.is_not(None),
                )
                .options(selectinload(AppInstance.app))
                .order_by(AppInstance.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    for instance in instances:
        db_app = instance.app
        if db_app is None:
            continue
        config = instance.config or {}
        imported = bool(config.get("imported"))
        external = is_external_app(db_app)
        containers = _container_count(db_app)
        complex_target = containers > 1
        reason = None
        if external:
            reason = "外部服务不创建容器，无法自动更新"
        elif imported:
            reason = "导入的外部容器不由 OneTap 管理"
        elif instance.project_id is None:
            reason = "未关联 Compose 项目，无法自动更新"
        elif complex_target:
            reason = f"多容器应用（{containers} 个容器），仅更新主服务镜像"
        targets.append(
            {
                "key": f"app:{instance.id}",
                "type": "app",
                "id": instance.id,
                "name": instance.instance_name,
                "display_name": db_app.display_name or db_app.name,
                "icon": db_app.icon,
                "icon_name": db_app.name,
                "status": instance.status,
                "app_count": 1,
                "apps": [db_app.display_name or db_app.name],
                "complex": complex_target,
                "selectable": not (external or imported or instance.project_id is None),
                "reason": reason,
                **_version_fields(instance.orchestration_version, db_app.version),
            }
        )

    return targets
