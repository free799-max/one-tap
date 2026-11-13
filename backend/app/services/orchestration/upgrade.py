"""编排实例组一键升级能力。

升级只更新应用镜像，不重建实例组：

- 已部署应用的挂载目录、端口、环境变量、凭据全部沿用当前实例配置；
- 使用现有 Compose 项目原地写入新版本并 ``docker compose up -d``，
  不删除项目、不重新分配端口；
- 模板新增的应用按默认配置部署；
- 模板已移除的应用保留运行，不自动删除数据；
- 升级前把组配置、应用配置与当前 Compose YAML 写入
  ``orchestration_upgrade_backups``，便于失败时回查或恢复；
- 全部应用成功后更新方案版本快照，失败时保留旧版本并回滚该应用的
  Compose 版本。
"""

import asyncio
import copy
import logging
from typing import Callable

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import create_background_task
from app.core.exceptions import APIException
from app.database import async_session
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import (
    AppInstance,
    AppOrchestrationInstance,
    OrchestrationUpgradeBackup,
)
from app.services.app_store import app_service
from app.services.app_store.app_validator import extract_default_values
from app.services.compose import compose_manager
from app.services.orchestration.credentials import prepare_app_credentials
from app.services.orchestration.deploy import _resolve_composition_image
from app.services.orchestration.deploy_task_service import deploy_task_manager
from app.services.orchestration.events import add_update_event
from app.services.orchestration.external import (
    is_external_app,
    register_external_instance,
)
from app.services.orchestration.shared_config import merge_shared_config

logger = logging.getLogger(__name__)


def _split_image_tag(image: str) -> tuple[str, str | None]:
    """拆分镜像名与标签，保留仓库地址中的端口。"""
    last_segment = image.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return image, None
    repo, tag = image.rsplit(":", 1)
    return repo, tag


def resolve_target_image(
    composition_item: dict,
    db_app: App,
    instance_config: dict | None,
) -> str | None:
    """计算升级后应用要使用的镜像，供升级流程与预览复用。

    优先级：模板组合显式 image/tag > 应用默认镜像；
    用户显式选择过 image_tag 且模板未覆盖时保留该标签。
    """
    image_override = _resolve_composition_image(composition_item)
    target_image = image_override or db_app.image
    if not target_image:
        return None
    user_tag = str((instance_config or {}).get("image_tag") or "").strip()
    if not image_override and user_tag:
        repo, _ = _split_image_tag(target_image)
        target_image = f"{repo}:{user_tag}"
    return target_image


async def read_current_image(
    db: AsyncSession,
    instance: AppInstance,
    db_app: App,
) -> str | None:
    """读取实例当前 Compose 主服务的镜像，用于升级预览。"""
    if not instance.project_id:
        return None
    project = await db.get(DockerComposeProject, instance.project_id)
    if project is None:
        return None
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
        return None
    try:
        compose_data = yaml.safe_load(current_version.content) or {}
    except yaml.YAMLError:
        return None
    services = compose_data.get("services")
    if not isinstance(services, dict) or not services:
        return None
    try:
        service_name = UpgradeMixin._resolve_main_service(
            services, db_app, project.project_name
        )
    except APIException:
        return None
    service = services.get(service_name)
    if not isinstance(service, dict):
        return None
    return str(service.get("image") or "") or None


def _single_event_name(app_name: str) -> str:
    """单应用升级事件的展示名（列宽 50，前缀标明不是编排方案）。"""
    return f"app:{app_name}"[:50]


async def resolve_single_target_image(
    db: AsyncSession,
    instance: AppInstance,
    db_app: App,
) -> str | None:
    """计算单应用升级后的主服务镜像。

    按实例现有配置重新渲染应用模板，取主服务镜像，与重新部署时的
    镜像解析规则保持一致；渲染或解析失败时返回 None。
    """
    if not instance.project_id:
        return None
    project = await db.get(DockerComposeProject, instance.project_id)
    if project is None:
        return None
    try:
        resolver = await app_service._build_storage_resolver(db)
        rendered = app_service._render_app(
            db_app=db_app,
            config=copy.deepcopy(instance.config or {}),
            project_name=project.project_name,
            resolver=resolver,
        )
        compose_data = yaml.safe_load(rendered) or {}
    except Exception:
        logger.exception("应用 %s 渲染目标镜像失败", db_app.name)
        return None
    services = compose_data.get("services")
    if not isinstance(services, dict) or not services:
        return None
    try:
        service_name = UpgradeMixin._resolve_main_service(
            services, db_app, project.project_name
        )
    except APIException:
        return None
    service = services.get(service_name)
    if not isinstance(service, dict):
        return None
    return str(service.get("image") or "") or None


class UpgradeMixin:
    """按最新模板升级已有编排实例组。"""

    def _track_upgrade_task(self, task_id: str, coro) -> asyncio.Task:
        task = create_background_task(coro)
        tasks = getattr(self, "_upgrade_tasks", None)
        if tasks is None:
            tasks = {}
            self._upgrade_tasks = tasks
        tasks[task_id] = task

        def cleanup(done_task: asyncio.Task) -> None:
            if tasks.get(task_id) is done_task:
                tasks.pop(task_id, None)

        task.add_done_callback(cleanup)
        return task

    async def cancel_upgrade(self, task_id: str) -> None:
        state = deploy_task_manager.get_task(task_id)
        if state is None:
            raise APIException("任务不存在", 404)
        if state.get("type") != "app_upgrade":
            raise APIException("仅单应用升级任务支持停止", 400)
        if state.get("status") != "deploying":
            raise APIException("任务已结束", 409)

        task = getattr(self, "_upgrade_tasks", {}).get(task_id)
        if task is None or task.done():
            raise APIException("任务已结束或当前无法停止", 409)
        deploy_task_manager.cancel_task(task_id, "已停止升级，镜像下载已中断")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def upgrade_instance(
        self,
        db: AsyncSession,
        group_id: int,
        selected_apps: list[str],
        app_configs: dict[str, dict],
        shared_config: dict,
        user_id: int | None = None,
        mode: str = "manual",
    ) -> tuple[AppOrchestrationInstance, list[str]]:
        """一键升级编排实例组到模板当前版本。

        已存在的应用只更新镜像，忽略请求里的 ``app_configs`` / ``shared_config``，
        避免误改挂载与端口；这两个参数只用于模板新增应用的初始化配置。
        ``mode`` 用于更新事件标记来源：用户触发为 manual，自动更新为 auto。

        Returns:
            实例组与升级进度任务 ID 列表（统一为一个组级任务）。
        """
        group = await self.get_group(db, group_id)
        template = group.orchestration
        composition = template.app_composition or []
        composition_by_name = {item.get("app_name"): item for item in composition}
        from_version = group.orchestration_version
        to_version = template.version

        # 有正在进行的部署任务时拒绝升级：升级会重写 Compose 版本，
        # 与后台部署并发会互相覆盖
        deploying = [
            inst.instance_name
            for inst in group.instances
            if deploy_task_manager.has_active_task(instance_id=inst.id)
        ]
        if deploying:
            raise APIException(
                f"以下应用正在部署中，请等待完成后再升级: {', '.join(deploying)}",
                409,
            )
        if deploy_task_manager.has_active_group_task(group.id):
            raise APIException("实例组正在升级中，请等待完成后再试", 409)

        if not selected_apps:
            raise APIException("请至少选择一个要升级的应用", 400)

        # 所选应用必须在当前模板组合中（含互斥检测），不要求必选应用全选
        self._validate_composition(
            composition=composition,
            selected_apps=selected_apps,
            require_required=False,
        )

        app_map = await self._load_apps(db, selected_apps)
        missing = [name for name in selected_apps if name not in app_map]
        if missing:
            raise APIException(f"应用不存在: {', '.join(missing)}", 400)

        app_names = await self._group_app_names(db, group)
        existing_by_name = {
            name: inst
            for inst in group.instances
            if (name := app_names.get(inst.id))
        }
        new_apps = [name for name in selected_apps if name not in existing_by_name]
        missing_projects = [
            name
            for name in selected_apps
            if name in existing_by_name
            and not existing_by_name[name].project_id
            and not is_external_app(app_map[name])
        ]
        if missing_projects:
            raise APIException(
                "以下应用缺少 Compose 项目，请先执行「修复」后再升级: "
                + ", ".join(missing_projects),
                409,
            )

        removed_names = sorted(
            name for name in existing_by_name if name not in composition_by_name
        )

        self._ensure_deploy_capacity(1)

        # 先落库完整配置快照：即使后续升级中断，也能回查旧挂载/端口/密钥
        backup = await self._create_upgrade_backup(
            db,
            group,
            from_version=from_version,
            to_version=to_version,
        )

        task_id = deploy_task_manager.create_task(
            "orchestration_upgrade",
            meta={
                "instance_name": group.instance_name,
                "orchestration_group_id": group.id,
                "description": f"升级方案到 v{to_version}",
                "operation": "upgrade",
                "selected_apps": list(selected_apps),
                "backup_id": backup.id,
            },
        )

        group.status = "deploying"
        await db.commit()

        create_background_task(
            self._run_upgrade(
                group_id=group.id,
                selected_apps=list(selected_apps),
                new_apps=new_apps,
                app_configs=app_configs,
                removed_names=removed_names,
                from_version=from_version,
                task_id=task_id,
                backup_id=backup.id,
                user_id=user_id,
                mode=mode,
            )
        )

        return group, [task_id]

    async def _run_upgrade(
        self,
        *,
        group_id: int,
        selected_apps: list[str],
        new_apps: list[str],
        app_configs: dict[str, dict],
        removed_names: list[str],
        from_version: str | None,
        task_id: str,
        backup_id: int,
        user_id: int | None,
        mode: str = "manual",
    ) -> None:
        """后台执行升级：逐个应用原地更新镜像，全部成功后更新组版本。"""
        total = len(selected_apps)

        def report(
            stage: str,
            percentage: int,
            message: str,
            detail: str | None = None,
        ) -> None:
            deploy_task_manager.update_progress(
                task_id, stage, percentage, message, detail
            )

        try:
            new_task_ids: list[str] = []
            new_credentials: dict[str, dict] = {}
            async with async_session() as db:
                group = await self.get_group(db, group_id)
                template = group.orchestration
                composition_by_name = {
                    item.get("app_name"): item
                    for item in (template.app_composition or [])
                }
                app_map = await self._load_apps(db, selected_apps)
                app_names = await self._group_app_names(db, group)
                existing_by_name = {
                    name: inst
                    for inst in group.instances
                    if (name := app_names.get(inst.id))
                }

                for index, app_name in enumerate(selected_apps, start=1):
                    start = 5 + int(85 * (index - 1) / max(total, 1))
                    end = 5 + int(85 * index / max(total, 1))
                    db_app = app_map[app_name]

                    if is_external_app(db_app):
                        external_config = dict(app_configs.get(app_name) or {})
                        existing = existing_by_name.get(app_name)
                        if existing is not None:
                            existing.config = {"external": True, **external_config}
                            existing.status = "running"
                            group.app_configs = {
                                **(group.app_configs or {}),
                                app_name: external_config,
                            }
                            await db.commit()
                        else:
                            await register_external_instance(
                                db,
                                group=group,
                                orchestration_id=template.id,
                                instance_name=group.instance_name,
                                app=db_app,
                                app_config=external_config,
                                version=template.version,
                            )
                            await db.commit()
                        report(
                            "completed",
                            end,
                            f"{db_app.display_name} 外部服务已登记",
                        )
                        continue

                    if app_name in existing_by_name:
                        report(
                            "preparing",
                            start,
                            f"升级 {db_app.display_name}（{index}/{total}）",
                        )
                        await self._upgrade_existing_app(
                            db,
                            instance=existing_by_name[app_name],
                            db_app=db_app,
                            composition_item=composition_by_name.get(app_name) or {},
                            task_id=task_id,
                            start=start,
                            end=end,
                            to_version=template.version,
                            user_id=user_id,
                        )
                    else:
                        report(
                            "preparing",
                            start,
                            f"部署新增应用 {db_app.display_name}（{index}/{total}）",
                        )
                        deploy_task_id, credentials = await self._deploy_new_app(
                            db,
                            group=group,
                            template=template,
                            db_app=db_app,
                            composition_item=composition_by_name.get(app_name) or {},
                            app_config=app_configs.get(app_name) or {},
                            user_id=user_id,
                        )
                        status = await self._wait_task_terminal(
                            deploy_task_id, timeout=3600
                        )
                        if status != "completed":
                            raise APIException(
                                f"新增应用 {app_name} 部署失败", 500
                            )
                        new_task_ids.append(deploy_task_id)
                        if credentials:
                            new_credentials[app_name] = credentials

                    report(
                        "syncing_status",
                        end,
                        f"{db_app.display_name} 升级完成（{index}/{total}）",
                    )

                group.status = "running"
                group.orchestration_version = template.version
                add_update_event(
                    db,
                    orchestration_name=template.name,
                    scope="instance",
                    group_id=group.id,
                    from_version=from_version,
                    to_version=template.version,
                    mode=mode,
                    status="applied",
                    detail={
                        "mode": "image_only",
                        "backup_id": backup_id,
                        "apps_added": new_apps,
                        "apps_retained": removed_names,
                        "selected_apps": selected_apps,
                    },
                )
                await db.commit()

                # 新增应用的账号自动配置在后台继续，不阻塞升级进度
                if new_task_ids:
                    self._schedule_provisioning(
                        group.id,
                        group.instance_name,
                        new_apps,
                        new_task_ids,
                        new_credentials,
                    )

            report("completed", 100, "升级完成")
            deploy_task_manager.complete_task(task_id)
        except Exception as exc:
            logger.exception("升级实例组 %s 失败", group_id)
            await self._record_upgrade_failure(
                group_id=group_id,
                from_version=from_version,
                to_version=None,
                selected_apps=selected_apps,
                backup_id=backup_id,
                message=str(exc),
                mode=mode,
            )
            deploy_task_manager.fail_task(task_id, str(exc))

    async def _upgrade_existing_app(
        self,
        db: AsyncSession,
        *,
        instance: AppInstance,
        db_app: App,
        composition_item: dict,
        task_id: str,
        start: int,
        end: int,
        to_version: str,
        user_id: int | None,
    ) -> None:
        """原地更新单个应用的镜像，保留全部运行时配置。

        直接复用当前 Compose YAML，只替换主服务的 ``image``，不重新渲染
        模板、不合并 Schema 默认值。这样挂载目录、端口、环境变量与旧部署
        完全一致，即使新模板改了字段默认值也不会影响现有实例。
        """
        if is_external_app(db_app):
            instance.status = "running"
            await db.commit()
            return
        target_image = resolve_target_image(
            composition_item, db_app, instance.config
        )
        if not target_image:
            raise APIException(f"应用 {db_app.name} 未配置镜像", 400)

        def _progress(
            stage: str,
            percentage: int,
            message: str,
            detail: str | None = None,
        ) -> None:
            ratio = max(0, min(100, percentage)) / 100
            mapped = start + int((end - start) * ratio)
            deploy_task_manager.update_progress(
                task_id,
                stage,
                mapped,
                f"{db_app.display_name}：{message}",
                detail,
            )

        await self._swap_image_and_deploy(
            db,
            instance=instance,
            db_app=db_app,
            target_image=target_image,
            progress=_progress,
            comment=f"方案升级到 v{to_version}",
            user_id=user_id,
        )

        # 仅记录镜像覆盖变化，挂载/端口/环境变量保持原样
        target_config = copy.deepcopy(instance.config or {})
        image_override = _resolve_composition_image(composition_item)
        if image_override:
            target_config["image"] = image_override
        else:
            target_config.pop("image", None)
        instance.config = target_config
        instance.status = "running"
        await db.commit()

    async def _swap_image_and_deploy(
        self,
        db: AsyncSession,
        *,
        instance: AppInstance,
        db_app: App,
        target_image: str,
        progress: Callable[..., None],
        comment: str,
        user_id: int | None = None,
    ) -> str:
        """把实例当前 Compose 主服务镜像替换为目标镜像并重新拉起。

        只改 ``image``，挂载目录、端口、环境变量保持原样；返回替换前的镜像。
        """
        project = await db.get(DockerComposeProject, instance.project_id)
        if project is None:
            raise APIException(f"应用 {db_app.name} 的 Compose 项目不存在", 409)

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
            raise APIException(
                f"应用 {db_app.name} 缺少当前 Compose 版本，请先执行「修复」",
                409,
            )

        try:
            compose_data = yaml.safe_load(current_version.content) or {}
        except yaml.YAMLError as exc:
            raise APIException(f"应用 {db_app.name} 的 Compose 配置无法解析", 500) from exc

        services = compose_data.get("services")
        if not isinstance(services, dict) or not services:
            raise APIException(f"应用 {db_app.name} 的 Compose 配置没有服务", 409)

        service_name = self._resolve_main_service(
            services, db_app, project.project_name
        )
        service = services[service_name]
        if not isinstance(service, dict):
            raise APIException(f"应用 {db_app.name} 的主服务配置无效", 500)

        current_image = str(service.get("image") or "")
        if current_image == target_image:
            # 镜像已是目标版本：可能是上次升级在 compose up 阶段中断，
            # YAML 已写好但容器还没拉起来。补一次 up -d 确保服务运行，
            # 不新增 Compose 版本，避免产生内容相同的版本记录。
            await compose_manager.action_async(
                db, project, "up", progress_callback=progress
            )
            return current_image

        current_tag = _split_image_tag(current_image)[1] or current_image
        target_tag = _split_image_tag(target_image)[1] or target_image
        progress(
            "preparing",
            0,
            f"镜像 {current_tag} → {target_tag}",
        )
        # 同一应用可能有多个服务复用同一镜像（如主服务 + worker），
        # 只替换镜像相同的服务，避免误改 Redis/Postgres 等依赖镜像
        targets = [
            item
            for item in services.values()
            if isinstance(item, dict)
            and current_image
            and str(item.get("image") or "") == current_image
        ]
        for item in targets or [service]:
            item["image"] = target_image
        updated_yaml = yaml.safe_dump(
            compose_data, sort_keys=False, allow_unicode=True
        )
        try:
            await compose_manager.edit_and_deploy_async(
                db,
                project,
                updated_yaml,
                user_id=user_id,
                comment=comment,
                progress_callback=progress,
            )
        except (Exception, asyncio.CancelledError):
            try:
                await compose_manager.rollback_version(
                    db, project, current_version
                )
            except Exception:
                logger.exception(
                    "应用 %s 升级中断后回滚 Compose 版本失败", db_app.name
                )
            raise
        return current_image

    async def upgrade_single_instance(
        self,
        db: AsyncSession,
        instance_id: int,
        *,
        mode: str = "manual",
        user_id: int | None = None,
        target_image: str | None = None,
    ) -> tuple[AppInstance, str]:
        """升级应用市场单独部署的应用：只替换镜像，保留运行时配置。

        编排部署的应用由实例组统一升级，导入的外部容器不受管理，均拒绝。
        ``mode`` 用于更新事件标记来源：用户触发为 manual，自动更新为 auto。
        """
        instance = await app_service.get_instance(db, instance_id)
        db_app = instance.app
        if db_app is None:
            raise APIException("应用不存在", 404)
        if is_external_app(db_app):
            raise APIException("外部服务不创建容器，无需升级", 400)
        if instance.orchestration_group_id is not None:
            raise APIException("编排部署的应用请在实例组中升级", 400)
        if (instance.config or {}).get("imported"):
            raise APIException("导入的外部容器不由 OneTap 维护，无法自动升级", 400)
        if not instance.project_id:
            raise APIException("该应用没有关联 Compose 项目，无法升级", 409)
        if instance.status != "running":
            raise APIException("应用当前不在运行状态，请先启动后再升级", 409)
        if deploy_task_manager.has_active_task(instance_id=instance.id):
            raise APIException("应用正在部署或升级中，请等待完成后再试", 409)

        if target_image is None:
            target_image = await resolve_single_target_image(db, instance, db_app)
        if not target_image:
            raise APIException("无法确定目标镜像，请先执行「修复」", 409)

        self._ensure_deploy_capacity(1)
        task_id = deploy_task_manager.create_task(
            "app_upgrade",
            instance_id=instance.id,
            project_id=instance.project_id,
            meta={
                "app_name": db_app.name,
                "instance_name": instance.instance_name,
                "description": f"升级 {db_app.display_name}",
                "operation": "upgrade",
                "mode": mode,
            },
        )
        instance.status = "deploying"
        await db.commit()

        self._track_upgrade_task(
            task_id,
            self._run_single_upgrade(
                instance_id=instance.id,
                db_app_name=db_app.name,
                target_image=target_image,
                task_id=task_id,
                mode=mode,
                user_id=user_id,
            ),
        )
        return instance, task_id

    async def _run_single_upgrade(
        self,
        *,
        instance_id: int,
        db_app_name: str,
        target_image: str,
        task_id: str,
        mode: str,
        user_id: int | None,
    ) -> None:
        """后台执行单应用升级：替换镜像并重新拉起，成功后更新版本快照。"""

        def report(
            stage: str,
            percentage: int,
            message: str,
            detail: str | None = None,
        ) -> None:
            deploy_task_manager.update_progress(
                task_id, stage, percentage, message, detail
            )

        from_version: str | None = None
        to_version: str | None = None
        try:
            async with async_session() as db:
                instance = await app_service.get_instance(db, instance_id)
                db_app = instance.app
                if db_app is None:
                    raise APIException("应用不存在", 404)
                from_version = instance.orchestration_version
                to_version = db_app.version
                report("preparing", 5, f"准备升级到 v{to_version}")
                current_image = await self._swap_image_and_deploy(
                    db,
                    instance=instance,
                    db_app=db_app,
                    target_image=target_image,
                    progress=report,
                    comment=(
                        f"自动升级到 v{to_version}"
                        if mode == "auto"
                        else f"升级到 v{to_version}"
                    ),
                    user_id=user_id,
                )
                instance.orchestration_version = to_version
                instance.status = "running"
                config = copy.deepcopy(instance.config or {})
                config.pop("auto_update_failed_version", None)
                instance.config = config
                add_update_event(
                    db,
                    orchestration_name=_single_event_name(db_app.name),
                    scope="instance",
                    from_version=from_version,
                    to_version=to_version,
                    mode=mode,
                    status="applied",
                    detail={
                        "mode": "image_only",
                        "app_instance_id": instance.id,
                        "app_name": db_app.name,
                        "image_from": current_image,
                        "image_to": target_image,
                    },
                )
                await db.commit()

            report("completed", 100, "升级完成")
            deploy_task_manager.complete_task(task_id, "升级完成")
        except asyncio.CancelledError:
            try:
                async with async_session() as db:
                    instance = await app_service.get_instance(db, instance_id)
                    instance.status = "running"
                    await db.commit()
            except Exception:
                logger.exception("应用实例 %s 取消升级后恢复状态失败", instance_id)
            deploy_task_manager.cancel_task(task_id, "已停止升级，镜像下载已中断")
            raise
        except Exception as exc:
            logger.exception("应用实例 %s 升级失败", instance_id)
            await self._record_single_failure(
                instance_id=instance_id,
                app_name=db_app_name,
                from_version=from_version,
                to_version=to_version,
                target_image=target_image,
                message=str(exc),
                mode=mode,
            )
            deploy_task_manager.fail_task(task_id, str(exc))

    async def _record_single_failure(
        self,
        *,
        instance_id: int,
        app_name: str,
        from_version: str | None,
        to_version: str | None,
        target_image: str,
        message: str,
        mode: str,
    ) -> None:
        """升级失败后恢复运行状态并记录 failed 事件（尽力而为）。

        自动更新失败时把失败版本记到实例配置，避免每轮定时任务反复重试
        同一版本；管理端发布下一个版本后自动恢复尝试。
        """
        try:
            async with async_session() as db:
                instance = await app_service.get_instance(db, instance_id)
                instance.status = "running"
                if mode == "auto" and to_version:
                    config = copy.deepcopy(instance.config or {})
                    config["auto_update_failed_version"] = to_version
                    instance.config = config
                add_update_event(
                    db,
                    orchestration_name=_single_event_name(app_name),
                    scope="instance",
                    from_version=from_version,
                    to_version=to_version,
                    mode=mode,
                    status="failed",
                    detail={
                        "mode": "image_only",
                        "app_instance_id": instance.id,
                        "app_name": app_name,
                        "image_to": target_image,
                    },
                    message=message,
                )
                await db.commit()
        except Exception:
            logger.exception("应用实例 %s 升级失败后记录事件出错", instance_id)

    @staticmethod
    def _resolve_main_service(
        services: dict, db_app: App, project_name: str
    ) -> str:
        """从 Compose 服务中定位应用主服务，避免误改 Redis/Postgres 镜像。"""
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

    async def _deploy_new_app(
        self,
        db: AsyncSession,
        *,
        group: AppOrchestrationInstance,
        template,
        db_app: App,
        composition_item: dict,
        app_config: dict,
        user_id: int | None,
    ) -> tuple[str, dict | None]:
        """部署模板新增的应用，返回部署任务 ID 与待配置凭据。"""
        config = extract_default_values(db_app.config_schema or {})
        config.update(copy.deepcopy(app_config))

        merged_config = merge_shared_config(
            shared_config=group.shared_config,
            app_config=config,
            shared_schema=template.shared_config_schema,
            app_schema=db_app.config_schema,
            app_name=db_app.name,
        )
        image_override = _resolve_composition_image(composition_item)
        if image_override:
            merged_config["image"] = image_override

        credentials = await prepare_app_credentials(
            db, db_app.name, merged_config
        )
        instance, task_id = await app_service.deploy(
            db,
            app_name=db_app.name,
            instance_name=f"{group.instance_name}-{db_app.name}",
            config=merged_config,
            user_id=user_id,
        )
        instance.orchestration_id = template.id
        instance.orchestration_group_id = group.id
        await db.commit()
        return task_id, credentials

    async def _create_upgrade_backup(
        self,
        db: AsyncSession,
        group: AppOrchestrationInstance,
        *,
        from_version: str | None,
        to_version: str | None,
    ) -> OrchestrationUpgradeBackup:
        """保存升级前的完整配置快照（含 Compose YAML 与密钥类配置）。"""
        app_names = await self._group_app_names(db, group)
        apps: list[dict] = []
        for inst in group.instances:
            project = (
                await db.get(DockerComposeProject, inst.project_id)
                if inst.project_id
                else None
            )
            current_version = None
            if project is not None:
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
            apps.append(
                {
                    "instance_id": inst.id,
                    "app_name": app_names.get(inst.id),
                    "instance_name": inst.instance_name,
                    "project_id": inst.project_id,
                    "project_name": project.project_name if project else None,
                    "config": copy.deepcopy(inst.config or {}),
                    "compose_version_id": (
                        current_version.id if current_version else None
                    ),
                    "compose_version_number": (
                        current_version.version_number if current_version else None
                    ),
                    "compose_yaml": (
                        current_version.content if current_version else None
                    ),
                }
            )

        backup = OrchestrationUpgradeBackup(
            group_id=group.id,
            orchestration_name=group.orchestration.name,
            instance_name=group.instance_name,
            from_version=from_version,
            to_version=to_version,
            payload={
                "shared_config": copy.deepcopy(group.shared_config or {}),
                "app_configs": copy.deepcopy(group.app_configs or {}),
                "apps": apps,
            },
        )
        db.add(backup)
        await db.flush()
        await db.refresh(backup)
        return backup

    async def _group_app_names(
        self,
        db: AsyncSession,
        group: AppOrchestrationInstance,
    ) -> dict[int, str | None]:
        """返回组内每个应用实例（按实例 ID）对应的应用商店应用名。"""
        app_ids = {inst.app_id for inst in group.instances if inst.app_id}
        if not app_ids:
            return {inst.id: None for inst in group.instances}
        rows = (
            await db.execute(select(App.id, App.name).where(App.id.in_(app_ids)))
        ).all()
        name_by_id = {app_id: name for app_id, name in rows}
        return {
            inst.id: name_by_id.get(inst.app_id) for inst in group.instances
        }

    async def _record_upgrade_failure(
        self,
        *,
        group_id: int,
        from_version: str | None,
        to_version: str | None,
        selected_apps: list[str],
        backup_id: int,
        message: str,
        mode: str = "manual",
    ) -> None:
        """升级失败后回写组状态为 error 并记录 failed 事件（尽力而为）。"""
        try:
            async with async_session() as db:
                group = await self.get_group(db, group_id)
                group.status = "error"
                orchestration_name = group.orchestration.name
                from_version = from_version or group.orchestration_version
                to_version = to_version or group.orchestration.version
                add_update_event(
                    db,
                    orchestration_name=orchestration_name,
                    scope="instance",
                    group_id=group_id,
                    from_version=from_version,
                    to_version=to_version,
                    mode=mode,
                    status="failed",
                    detail={
                        "mode": "image_only",
                        "backup_id": backup_id,
                        "selected_apps": list(selected_apps),
                    },
                    message=message,
                )
                await db.commit()
        except Exception:
            logger.exception("升级实例组 %s 失败后记录事件出错", group_id)
