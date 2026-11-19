"""已部署 Compose 的账号认证变量同步。

账号密码类配置（MoviePilot / Reader / Suwayomi）落在容器环境变量里。
升级与「重新装配」都要把它们写进现有 Compose，否则模板新增的认证开关
（例如 Suwayomi 的 simple_login）在旧实例里永远不会生效。
"""

import logging
from typing import Callable

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance
from app.services.compose import compose_manager

logger = logging.getLogger(__name__)


def resolve_main_service(services: dict, db_app: App, project_name: str) -> str:
    """从 Compose 服务中定位应用主服务，避免误改 Redis/Postgres 等依赖。"""
    containers = (db_app.config_schema or {}).get("containers") or []
    main_name = next(
        (
            container.get("name")
            for container in containers
            if container.get("name") == db_app.name
        ),
        None,
    )
    if main_name is None and containers:
        main_name = containers[0].get("name")

    for candidate in (db_app.name, main_name):
        if candidate and candidate in services:
            return candidate
    for name, service in services.items():
        if (
            isinstance(service, dict)
            and service.get("container_name") == project_name
        ):
            return name
    if len(services) == 1:
        return next(iter(services))
    raise APIException(
        f"无法识别应用 {db_app.name} 的主服务，已取消升级", 409
    )


def merge_env(service: dict, updates: dict[str, str]) -> bool:
    """把认证变量写入主服务 environment，返回是否有变化。

    Compose 的 environment 支持列表（``KEY=value``）与映射两种写法，
    两种都要兼容；未声明的变量保持原样。
    """
    if not updates:
        return False
    env = service.get("environment")
    if isinstance(env, dict):
        changed = False
        for key, value in updates.items():
            if str(env.get(key)) != str(value):
                env[key] = value
                changed = True
        return changed
    rows = [str(item) for item in (env or [])]
    index = {row.split("=", 1)[0]: position for position, row in enumerate(rows)}
    changed = False
    for key, value in updates.items():
        row = f"{key}={value}"
        position = index.get(key)
        if position is None:
            rows.append(row)
            index[key] = len(rows) - 1
            changed = True
        elif rows[position] != row:
            rows[position] = row
            changed = True
    if changed:
        service["environment"] = rows
    return changed


async def sync_env(
    db: AsyncSession,
    *,
    instance: AppInstance,
    db_app: App,
    updates: dict[str, str],
    comment: str,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """把认证变量写进实例现有 Compose 并重新拉起，返回是否有改动。

    只在变量确实变化时新增 Compose 版本，重复调用不会反复重启容器。
    """
    if not updates or not instance.project_id:
        return False
    project = await db.get(DockerComposeProject, instance.project_id)
    if project is None:
        return False

    current_version = (
        await db.execute(
            select(DockerComposeVersion)
            .where(
                DockerComposeVersion.project_id == project.id,
                DockerComposeVersion.is_current == True,  # noqa: E712
            )
            .order_by(DockerComposeVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current_version is None:
        return False

    try:
        compose_data = yaml.safe_load(current_version.content) or {}
    except yaml.YAMLError as exc:
        raise APIException(
            f"应用 {db_app.name} 的 Compose 配置无法解析", 500
        ) from exc

    services = compose_data.get("services")
    if not isinstance(services, dict) or not services:
        return False

    service_name = resolve_main_service(services, db_app, project.project_name)
    service = services[service_name]
    if not isinstance(service, dict) or not merge_env(service, updates):
        return False

    if progress:
        progress("同步账号与认证配置")
    updated_yaml = yaml.safe_dump(
        compose_data, sort_keys=False, allow_unicode=True
    )
    await compose_manager.edit_and_deploy_async(
        db,
        project,
        updated_yaml,
        comment=comment,
        progress_callback=None,
    )
    return True
