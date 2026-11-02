"""清理 Docker 中已不存在容器的应用实例记录。

失败的部署会在 ``app_instances`` 留下 error 状态记录，而容器早已不存在。
这些记录不会被自动回收，会出现在应用列表和集成选择器中。启动时清理，
为了避免误删：

- Docker 必须可用且能读取容器列表；
- 只处理 error 状态且创建时间超过 ``min_age`` 的实例；
- compose 项目、容器名、容器 ID 在 Docker 中都不存在才删除；
- 同名 error 记录只保留关联项目的最新一条；
- 存在备份的实例不删除。
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.docker.compose import DockerComposeProject
from app.models.orchestration import AppInstance, AppInstanceBackup
from app.services.docker import docker_manager

logger = logging.getLogger(__name__)

ORPHAN_MIN_AGE = timedelta(days=1)


async def purge_orphaned_instances(
    db: AsyncSession, *, min_age: timedelta = ORPHAN_MIN_AGE
) -> list[int]:
    """删除 Docker 中已没有任何容器的失败部署记录，返回删除的实例 ID。"""
    try:
        available = await docker_manager.run_sync(docker_manager.check_available)
        if not available:
            return []
        containers = await docker_manager.run_sync(docker_manager.list_containers)
    except Exception:
        logger.warning("清理无效实例时读取 Docker 失败，跳过", exc_info=True)
        return []

    project_names: set[str] = set()
    container_keys: set[str] = set()
    for item in containers:
        labels = item.get("labels") or {}
        project = labels.get("com.docker.compose.project")
        if project:
            project_names.add(project)
        for key in ("name", "id"):
            value = item.get(key)
            if value:
                container_keys.add(value)

    rows = (
        await db.execute(
            select(
                AppInstance.id,
                AppInstance.app_id,
                AppInstance.orchestration_id,
                AppInstance.instance_name,
                AppInstance.config,
                AppInstance.created_at,
                AppInstance.project_id,
                DockerComposeProject.project_name,
            )
            .outerjoin(
                DockerComposeProject,
                AppInstance.project_id == DockerComposeProject.id,
            )
            .where(AppInstance.status == "error")
        )
    ).all()

    cutoff = datetime.now(UTC).replace(tzinfo=None) - min_age
    backup_ids = set(
        (await db.execute(select(AppInstanceBackup.instance_id))).scalars().all()
    )
    stale_ids: list[int] = []
    duplicate_groups: dict[
        tuple[int | None, int | None, str], list[tuple[int, bool]]
    ] = {}
    for (
        instance_id,
        app_id,
        orchestration_id,
        instance_name,
        config,
        created_at,
        project_id,
        project_name,
    ) in rows:
        if created_at is not None and created_at > cutoff:
            continue
        if instance_id in backup_ids:
            continue
        config = config if isinstance(config, dict) else {}
        candidates = {instance_name}
        if project_name:
            candidates.add(project_name)
        if config.get("container_name"):
            candidates.add(str(config["container_name"]))
        if config.get("container_id"):
            candidates.add(str(config["container_id"])[:12])
        if candidates & project_names or candidates & container_keys:
            duplicate_groups.setdefault(
                (app_id, orchestration_id, instance_name), []
            ).append((instance_id, project_id is not None))
            continue
        stale_ids.append(instance_id)

    # 同一应用的同名 error 记录通常来自重复部署，只保留关联项目的最新一条
    for group in duplicate_groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda item: (item[1], item[0]), reverse=True)
        stale_ids.extend(item[0] for item in group[1:])

    if not stale_ids:
        return []

    await db.execute(delete(AppInstance).where(AppInstance.id.in_(stale_ids)))
    await db.commit()
    logger.warning(
        "已清理 %s 条无容器的失败部署记录：%s", len(stale_ids), stale_ids
    )
    return stale_ids
