"""应用编排实例查询与管理能力。"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.models.docker import DockerComposeProject
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
    OrchestrationUpdateEvent,
)

logger = logging.getLogger(__name__)


class InstanceMixin:
    """编排实例组的查询、更新与删除能力。"""

    async def list_instances(
        self,
        db: AsyncSession,
        category: str | None = None,
    ) -> list[AppOrchestrationInstance]:
        """列出编排实例组（一次部署/导入记录）。

        返回结果前会根据 Docker 实时刷新每个应用实例的状态。
        可选按分类筛选，返回结果按创建时间倒序排列。
        """
        stmt = (
            select(AppOrchestrationInstance)
            .join(AppOrchestration)
            .options(
                selectinload(AppOrchestrationInstance.orchestration),
                selectinload(AppOrchestrationInstance.instances).selectinload(
                    AppInstance.app
                ),
                selectinload(AppOrchestrationInstance.instances).selectinload(
                    AppInstance.project
                ),
            )
        )
        if category:
            stmt = stmt.where(AppOrchestration.category == category)
        stmt = stmt.order_by(AppOrchestrationInstance.created_at.desc())
        result = await db.execute(stmt)
        groups = list(result.scalars().all())

        for group in groups:
            for instance in group.instances:
                await asyncio.to_thread(self._refresh_instance_status, instance)
            await self._reconcile_stuck_group_status(db, group)

        return groups

    async def _reconcile_stuck_group_status(
        self,
        db: AsyncSession,
        group: AppOrchestrationInstance,
    ) -> None:
        """恢复因进程重启而卡在 deploying 的实例组。

        部署与升级任务只存在于后端内存，进程重启后这些任务已消失，
        但数据库里的组状态仍停在 ``deploying``。读取时若找不到任何
        关联的进行中任务，就把组状态置为 ``error``，允许用户重新升级或修复。
        """
        if group.status != "deploying":
            return
        # 刚创建的部署/升级请求可能还没创建出任务，给一个宽限窗口避免误判
        created_at = group.created_at
        if created_at is not None:
            elapsed = (datetime.now() - created_at).total_seconds()
            if elapsed < 60:
                return
        from app.services.orchestration.deploy_task_service import deploy_task_manager

        if deploy_task_manager.has_active_group_task(group.id):
            return
        if any(
            deploy_task_manager.has_active_task(instance_id=instance.id)
            for instance in group.instances
        ):
            return

        logger.warning(
            "实例组 %s（%s）卡在 deploying 且没有进行中的任务，置为 error",
            group.id,
            group.instance_name,
        )
        group.status = "error"
        await db.commit()

    async def list_update_events(
        self,
        db: AsyncSession,
        scope: str | None = None,
        orchestration_name: str | None = None,
        mode: str | None = None,
        limit: int = 20,
    ) -> list[OrchestrationUpdateEvent]:
        """查询模板/实例组版本变更记录，按创建时间倒序。"""
        stmt = select(OrchestrationUpdateEvent)
        if scope:
            stmt = stmt.where(OrchestrationUpdateEvent.scope == scope)
        if mode:
            stmt = stmt.where(OrchestrationUpdateEvent.mode == mode)
        if orchestration_name:
            stmt = stmt.where(
                OrchestrationUpdateEvent.orchestration_name == orchestration_name
            )
        stmt = stmt.order_by(
            OrchestrationUpdateEvent.created_at.desc(),
            OrchestrationUpdateEvent.id.desc(),
        ).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_instance(self, db: AsyncSession, instance_id: int) -> AppInstance:
        """获取单个编排实例（携带编排、项目及 Stack 关系）。"""
        result = await db.execute(
            select(AppInstance)
            .where(AppInstance.id == instance_id)
            .options(
                selectinload(AppInstance.orchestration),
                selectinload(AppInstance.project).selectinload(
                    DockerComposeProject.stack
                ),
            )
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            raise APIException("编排实例不存在", 404)
        return instance

    async def get_group(
        self,
        db: AsyncSession,
        group_id: int,
    ) -> AppOrchestrationInstance:
        """获取组合部署记录及其关联实例。"""
        result = await db.execute(
            select(AppOrchestrationInstance)
            .where(AppOrchestrationInstance.id == group_id)
            .options(
                selectinload(AppOrchestrationInstance.orchestration),
                selectinload(AppOrchestrationInstance.instances),
            )
        )
        group = result.scalar_one_or_none()
        if group is None:
            raise APIException("组合部署记录不存在", 404)
        return group

    async def get_instance_detail(
        self,
        db: AsyncSession,
        instance_id: int,
    ) -> AppOrchestrationInstance:
        """获取编排实例组详情（含应用实例及应用信息）。

        返回前会根据 Docker 实时刷新每个应用实例的状态。
        """
        result = await db.execute(
            select(AppOrchestrationInstance)
            .where(AppOrchestrationInstance.id == instance_id)
            .options(
                selectinload(AppOrchestrationInstance.orchestration),
                selectinload(AppOrchestrationInstance.instances).selectinload(
                    AppInstance.app
                ),
                selectinload(AppOrchestrationInstance.instances).selectinload(
                    AppInstance.project
                ),
            )
        )
        group = result.scalar_one_or_none()
        if group is None:
            raise APIException("编排实例组不存在", 404)

        for instance in group.instances:
            await asyncio.to_thread(self._refresh_instance_status, instance)
        await self._reconcile_stuck_group_status(db, group)

        return group

    async def update_instance(
        self,
        db: AsyncSession,
        instance_id: int,
        instance_name: str | None = None,
        shared_config: dict | None = None,
        app_configs: dict | None = None,
    ) -> AppOrchestrationInstance:
        """更新编排实例组信息。"""
        group = await self.get_instance_detail(db, instance_id)
        if instance_name is not None:
            group.instance_name = instance_name
        if shared_config is not None:
            group.shared_config = shared_config
        if app_configs is not None:
            group.app_configs = app_configs
        await db.commit()
        await db.refresh(group)
        return group

    async def delete_instance(
        self,
        db: AsyncSession,
        instance_id: int,
        progress=None,
    ):
        """删除编排实例组及其关联的应用实例与 Docker 项目。

        删除前先把各应用自身的数据目录移入备份目录，避免同名重建时
        复用旧数据（如已初始化的数据库密码与本次部署不一致）。

        Args:
            progress: 可选进度回调 ``(stage, percentage, message, detail)``，
                供后台删除任务推送进度

        Returns:
            ``(backup_dir, failures)``：备份目录路径（未产生备份时为 None）
            与未能备份的数据目录列表；后者非空时旧数据仍留在原路径，
            调用方必须向用户告警
        """
        from app.services.app_store.app_service import _slugify
        from app.services.compose.compose_service import compose_manager
        from app.services.orchestration.backup import AppDataEntry, backup_app_data_dirs

        def report(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            if progress is not None:
                progress(stage, percentage, message, detail)

        report("preparing", 5, "准备删除实例组")
        group = await self.get_instance_detail(db, instance_id)
        group_name = group.instance_name
        orchestration_name = group.orchestration.name if group.orchestration else None

        # 先记录应用信息：delete_project 会级联删除实例行
        projects: list[DockerComposeProject] = []
        entries: list[AppDataEntry] = []
        seen_project_ids: set[int] = set()
        for instance in list(group.instances):
            app_name = instance.app.name if instance.app else None
            project = None
            if instance.project_id:
                project = await db.get(DockerComposeProject, instance.project_id)
            if project is not None:
                project_name = project.project_name
            else:
                # 无关联项目的实例（部署中途失败、导入的外部容器）按项目名规则兜底；
                # 部署在 compose up 阶段失败时项目已落库但未回写到实例，仍需一并清理
                try:
                    project_name = _slugify(instance.instance_name)
                except ValueError:
                    continue
                result = await db.execute(
                    select(DockerComposeProject).where(
                        DockerComposeProject.project_name == project_name
                    )
                )
                project = result.scalar_one_or_none()
            if project is not None and project.id not in seen_project_ids:
                seen_project_ids.add(project.id)
                projects.append(project)
            entries.append(
                AppDataEntry(
                    instance_name=instance.instance_name,
                    app_name=app_name,
                    project_name=project_name,
                    config=dict(instance.config or {}),
                )
            )

        # 先停止并移除容器；单个项目清理失败不应影响其余
        total = max(len(projects), 1)
        for index, project in enumerate(projects):
            report(
                "stopping_services",
                10 + int(50 * index / total),
                f"停止并移除容器（{index + 1}/{total}）",
                project.project_name,
            )
            try:
                await compose_manager.delete_project(db, project)
            except Exception as e:
                logger.warning(
                    "清理实例组 %s 的项目 %s 失败: %s",
                    instance_id,
                    project.project_name,
                    e,
                )

        # 容器已停止，再把应用数据目录移入备份目录
        report("backing_up_data", 65, "备份应用数据目录")
        backup_dir, backup_failures = await backup_app_data_dirs(
            db,
            entries,
            group_name=group_name,
            orchestration_name=orchestration_name,
            shared_config=group.shared_config,
            app_configs=group.app_configs,
        )
        if backup_failures:
            failed_names = "、".join(
                item.project_name for item in backup_failures
            )
            report(
                "backing_up_data",
                75,
                f"有 {len(backup_failures)} 个应用数据目录未能备份",
                failed_names,
            )

        # 清理无项目实例（如导入的外部容器）及实例组本身
        report("cleaning_records", 85, "清理实例组记录")
        await db.execute(
            delete(AppInstance).where(
                AppInstance.orchestration_group_id == instance_id
            )
        )
        await db.execute(
            delete(AppOrchestrationInstance).where(
                AppOrchestrationInstance.id == instance_id
            )
        )
        await db.commit()
        return backup_dir, backup_failures

    async def start_delete_instance(
        self,
        db: AsyncSession,
        instance_id: int,
    ) -> str:
        """创建后台删除任务并返回任务 ID。

        删除涉及停止容器与移动数据目录，耗时较长，改由部署任务通道
        推送进度，前端复用统一的进度弹窗展示。
        """
        from app.core.background import create_background_task
        from app.services.orchestration.deploy_task_service import deploy_task_manager

        group = await self.get_group(db, instance_id)

        deploying = [
            inst.instance_name
            for inst in group.instances
            if deploy_task_manager.has_active_task(instance_id=inst.id)
        ]
        if deploying or deploy_task_manager.has_active_group_task(instance_id):
            names = "、".join(deploying) or group.instance_name
            raise APIException(
                f"{names} 正在部署中，请等待完成后再删除", 409
            )

        task_id = deploy_task_manager.create_task(
            "orchestration_delete",
            # 不用 instance_id：它与 AppInstance.id 是不同命名空间，复用会误判
            meta={
                "instance_name": group.instance_name,
                "group_id": instance_id,
                "description": "删除实例组",
                "operation": "delete",
            },
        )
        create_background_task(self._do_delete(task_id, instance_id))
        return task_id

    async def _do_delete(self, task_id: str, instance_id: int) -> None:
        """执行后台删除任务并推送进度。"""
        from app.database import async_session
        from app.services.orchestration.deploy_task_service import deploy_task_manager

        def progress(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            deploy_task_manager.update_progress(
                task_id, stage, percentage, message, detail
            )

        async with async_session() as db:
            try:
                backup_dir, backup_failures = await self.delete_instance(
                    db, instance_id, progress=progress
                )
            except Exception as exc:
                logger.exception("删除实例组 %s 失败", instance_id)
                await db.rollback()
                deploy_task_manager.fail_task(task_id, str(exc))
                return

        if backup_failures:
            failed_names = "、".join(
                item.project_name for item in backup_failures
            )
            deploy_task_manager.complete_task(
                task_id,
                message=f"删除完成，但 {len(backup_failures)} 个应用数据目录未能备份",
                warning=(
                    f"{failed_names} 的数据仍保留在原来的应用数据目录中，"
                    "同名重建会复用旧数据（如已初始化的数据库密码）。"
                    "通常是数据目录属主为 root、后端进程无权限移动导致，"
                    "请手动确认并清理这些目录后再部署。"
                ),
            )
            return
        message = "删除完成，应用数据已备份" if backup_dir else "删除完成"
        deploy_task_manager.complete_task(task_id, message=message)
