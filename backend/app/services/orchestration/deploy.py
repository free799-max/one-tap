"""应用编排组合部署能力。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import (
    AppInstance,
    AppOrchestrationInstance,
)
from app.services.app_store import app_service
from app.services.app_store.app_validator import validate_app_config
from app.services.orchestration.credentials import (
    inherit_stored_secrets,
    prepare_app_credentials,
)
from app.services.orchestration.deploy_task_service import deploy_task_manager
from app.services.orchestration.events import add_update_event
from app.services.orchestration.shared_config import merge_shared_config

logger = logging.getLogger(__name__)


def _resolve_composition_image(item: dict | None) -> str | None:
    """按组合条目计算组件级覆盖镜像。

    条目 image 为空时返回 None（不注入，由 app_service 回落到应用默认镜像）；
    条目带 tag 且 image 最后一段（最后一个 / 之后）不含 : 时拼成 image:tag；
    image 自带 :tag 时忽略 tag 字段。
    """
    item = item or {}
    image = item.get("image") or ""
    if not image:
        return None
    tag = item.get("tag") or ""
    if tag and ":" not in image.rsplit("/", 1)[-1]:
        return f"{image}:{tag}"
    return image


class DeployMixin:
    """组合部署与重新部署能力。"""

    async def deploy(
        self,
        db: AsyncSession,
        orchestration_name: str,
        instance_name: str,
        selected_apps: list[str],
        app_configs: dict[str, dict],
        shared_config: dict,
        user_id: int | None = None,
        credential_overrides: dict[str, dict] | None = None,
        initial_app_configs: dict[str, dict] | None = None,
        allow_existing_group: bool = False,
    ) -> tuple[AppOrchestrationInstance, list[str]]:
        """组合部署：按 AppOrchestration.app_composition 的定义，批量部署多个独立 App。

        Args:
            credential_overrides: 指定应用的自动配置凭据（恢复备份时沿用原凭据，
                不再按统一密码重新生成）
            initial_app_configs: 新实例组的初始 ``app_configs``（恢复时继承
                原访问地址之外的认证信息）
            allow_existing_group: 恢复备份时允许沿用已存在的实例组名称

        返回组合部署记录和每个 App 对应的部署任务 ID 列表。
        """
        db_orchestration = await self.get_orchestration(db, orchestration_name)

        composition = db_orchestration.app_composition or []
        self._validate_composition(
            composition=composition,
            selected_apps=selected_apps,
        )

        # 校验所选 App 是否存在于应用商店
        app_map = await self._load_apps(db, selected_apps)
        missing = [name for name in selected_apps if name not in app_map]
        if missing:
            raise APIException(f"应用不存在: {', '.join(missing)}", 400)

        # 部署前先校验全部应用配置：任一应用配置不合法时直接报错，
        # 避免前面的应用已经启动、后面才发现缺少必填项
        self._preflight_app_configs(
            orchestration=db_orchestration,
            app_map=app_map,
            selected_apps=selected_apps,
            app_configs=app_configs,
            shared_config=shared_config,
        )

        # 同名实例组已存在时给出可操作提示：直接重新部署会被 Compose
        # 项目名冲突拦截，且容易产生重复的失败记录
        existing_group_result = await db.execute(
            select(AppOrchestrationInstance)
            .where(AppOrchestrationInstance.instance_name == instance_name)
            .order_by(AppOrchestrationInstance.id.desc())
        )
        existing_group = existing_group_result.scalars().first()
        if existing_group is not None and not allow_existing_group:
            status_label = {
                "error": "部署失败",
                "deploying": "部署中",
                "running": "运行中",
            }.get(existing_group.status, existing_group.status)
            raise APIException(
                f"实例组「{instance_name}」已存在（{status_label}），"
                "请在实例列表中执行「修复」，或更换实例名称后再部署",
                409,
            )

        # 全局互斥检测
        await self._check_global_conflicts(db, composition, selected_apps)
        self._ensure_deploy_capacity(len(selected_apps))
        await self._ensure_project_names_free(db, instance_name, selected_apps)

        # 创建组合部署记录
        group = AppOrchestrationInstance(
            orchestration_id=db_orchestration.id,
            instance_name=instance_name,
            shared_config=shared_config or {},
            status="deploying",
            # 记录本次部署对应的方案模板版本，供列表判断是否有新版本可升级
            orchestration_version=db_orchestration.version,
        )
        if initial_app_configs:
            group.app_configs = {
                name: dict(entry)
                for name, entry in initial_app_configs.items()
                if isinstance(entry, dict)
            }
        db.add(group)
        await db.flush()
        await db.refresh(group)
        await db.commit()

        task_ids: list[str] = []
        deployed_instances: dict[str, AppInstance] = {}
        pending_credentials: dict[str, dict] = {}
        composition_by_name = {item.get("app_name"): item for item in composition}

        try:
            for app_name in selected_apps:
                app_config = app_configs.get(app_name) or {}
                # 注入共享配置：volumes/env 数组追加合并，标量字段应用配置优先
                merged_config = merge_shared_config(
                    shared_config=shared_config,
                    app_config=app_config,
                    shared_schema=db_orchestration.shared_config_schema,
                    app_schema=app_map[app_name].config_schema,
                    app_name=app_name,
                )
                # 组合条目显式指定 image/tag 时覆盖应用默认镜像
                image_override = _resolve_composition_image(
                    composition_by_name.get(app_name)
                )
                if image_override:
                    merged_config["image"] = image_override
                # 注入统一账号密码（env 类应用写入 env 数组）；
                # 恢复备份时沿用备份中记录的凭据，避免与已恢复的数据不匹配
                if credential_overrides and app_name in credential_overrides:
                    creds = dict(credential_overrides[app_name])
                else:
                    creds = await prepare_app_credentials(db, app_name, merged_config)
                if creds:
                    pending_credentials[app_name] = creds
                sub_instance_name = f"{instance_name}-{app_name}"

                instance, task_id = await app_service.deploy(
                    db,
                    app_name=app_name,
                    instance_name=sub_instance_name,
                    config=merged_config,
                    user_id=user_id,
                )

                # 关联到组合部署组
                instance.orchestration_id = db_orchestration.id
                instance.orchestration_group_id = group.id
                await db.commit()

                task_ids.append(task_id)
                deployed_instances[app_name] = instance

            # 端口此时已分配完成，按实际端口写入各应用访问地址
            self._fill_access_urls(group, app_map, deployed_instances)
            group.status = "running"
            await db.commit()
        except Exception:
            group.status = "error"
            await db.commit()
            raise

        # 部署任务完成后把统一账号配置进真实服务（后台执行，不阻塞响应）；
        # 跟踪任务的 ID 一并返回，前端据此继续展示配置与装配进度
        provision_task_id = self._schedule_provisioning(
            group.id, instance_name, selected_apps, task_ids, pending_credentials
        )
        if provision_task_id:
            task_ids.append(provision_task_id)

        return group, task_ids

    async def redeploy_instance(
        self,
        db: AsyncSession,
        group_id: int,
        instance_name: str,
        selected_apps: list[str],
        app_configs: dict[str, dict],
        shared_config: dict,
        user_id: int | None = None,
    ) -> tuple[AppOrchestrationInstance, list[str]]:
        """重新部署已有编排实例组。

        先清理 selected_apps 对应的旧应用实例及其 Compose 项目，
        再按与 deploy 相同的路径在原组上重新创建实例与后台部署任务。
        完成后把组版本同步为当前模板版本并记录一条更新事件。
        """
        group = await self.get_group(db, group_id)
        db_orchestration = group.orchestration
        composition = db_orchestration.app_composition or []
        from_version = group.orchestration_version

        # 有正在进行的部署任务时拒绝修复：清理阶段会删除实例行，
        # 否则会打死后台任务并留下残留项目和卡住的任务槽位
        deploying = [
            inst.instance_name
            for inst in group.instances
            if deploy_task_manager.has_active_task(instance_id=inst.id)
        ]
        if deploying:
            raise APIException(
                f"以下应用正在部署中，请等待完成后再修复: {', '.join(deploying)}",
                409,
            )

        if not selected_apps:
            raise APIException("请至少选择一个要修复的应用", 400)

        self._validate_composition(
            composition=composition,
            selected_apps=selected_apps,
            require_required=False,
        )

        app_map = await self._load_apps(db, selected_apps)
        missing = [name for name in selected_apps if name not in app_map]
        if missing:
            raise APIException(f"应用不存在: {', '.join(missing)}", 400)

        # 清理旧部署前先校验配置，避免旧项目已删除、新配置却不合法
        self._preflight_app_configs(
            orchestration=db_orchestration,
            app_map=app_map,
            selected_apps=selected_apps,
            app_configs=app_configs,
            shared_config=shared_config,
        )

        # 清理阶段：删除旧实例及其 Compose 项目；任一失败则整体报错，不半删
        # 删除前快照各应用的部署配置，修复时继承其中的密钥类 env
        stored_configs: dict[str, dict] = {}
        try:
            for app_name in selected_apps:
                app_id = app_map[app_name].id
                old_instances = [
                    inst for inst in group.instances if inst.app_id == app_id
                ]
                for old in old_instances:
                    if old.config and app_name not in stored_configs:
                        stored_configs[app_name] = dict(old.config)
                    await self._cleanup_instance_project(db, old)
            await db.commit()
        except APIException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.exception("重新部署：清理实例组 %s 的旧部署失败", group_id)
            raise APIException(f"清理旧部署失败: {e}", 500) from e

        # 全局互斥检测（旧实例已清理，不会误判自身）
        await self._check_global_conflicts(db, composition, selected_apps)
        self._ensure_deploy_capacity(len(selected_apps))
        await self._ensure_project_names_free(db, instance_name, selected_apps)

        # 在原组上按与 deploy 相同的路径重建实例与部署任务
        group.instance_name = instance_name
        group.shared_config = shared_config or {}
        group.status = "deploying"
        await db.commit()

        task_ids: list[str] = []
        deployed_instances: dict[str, AppInstance] = {}
        pending_credentials: dict[str, dict] = {}
        composition_by_name = {item.get("app_name"): item for item in composition}

        try:
            for app_name in selected_apps:
                app_config = app_configs.get(app_name) or {}
                # 注入共享配置：volumes/env 数组追加合并，标量字段应用配置优先
                merged_config = merge_shared_config(
                    shared_config=shared_config,
                    app_config=app_config,
                    shared_schema=db_orchestration.shared_config_schema,
                    app_schema=app_map[app_name].config_schema,
                    app_name=app_name,
                )
                # 组合条目显式指定 image/tag 时覆盖应用默认镜像
                image_override = _resolve_composition_image(
                    composition_by_name.get(app_name)
                )
                if image_override:
                    merged_config["image"] = image_override
                # 修复时数据卷保留，继承上次部署的密钥类 env
                # （PostgreSQL/Redis 密码等仅首次初始化生效，重新随机必然不匹配）
                inherit_stored_secrets(stored_configs.get(app_name), merged_config)
                # 注入统一账号密码（env 类应用写入 env 数组）；
                # provision 类应用复用上次已配置的密码作为兜底/旧密码
                stored_entry = (group.app_configs or {}).get(app_name) or {}
                creds = await prepare_app_credentials(
                    db,
                    app_name,
                    merged_config,
                    fallback_password=stored_entry.get("password"),
                )
                if creds:
                    pending_credentials[app_name] = creds
                sub_instance_name = f"{instance_name}-{app_name}"

                instance, task_id = await app_service.deploy(
                    db,
                    app_name=app_name,
                    instance_name=sub_instance_name,
                    config=merged_config,
                    user_id=user_id,
                )

                # 关联到组合部署组
                instance.orchestration_id = db_orchestration.id
                instance.orchestration_group_id = group.id
                await db.commit()

                task_ids.append(task_id)
                deployed_instances[app_name] = instance

            # 端口可能已重新分配，按最新配置刷新各应用访问地址，
            # 认证信息等既有 app_configs 条目保留
            self._fill_access_urls(group, app_map, deployed_instances)
            group.status = "running"
            # 修复等价于按当前模板重建所选组件，成功后同步版本快照；
            # 失败时保留旧版本，避免掩盖仍可升级的状态
            group.orchestration_version = db_orchestration.version
            await db.commit()
        except Exception:
            group.status = "error"
            await db.commit()
            raise

        # 部署任务完成后把统一账号配置进真实服务（后台执行，不阻塞响应）；
        # 跟踪任务的 ID 一并返回，前端据此继续展示配置与装配进度
        provision_task_id = self._schedule_provisioning(
            group.id, instance_name, selected_apps, task_ids, pending_credentials
        )
        if provision_task_id:
            task_ids.append(provision_task_id)

        add_update_event(
            db,
            orchestration_name=db_orchestration.name,
            scope="instance",
            group_id=group.id,
            from_version=from_version,
            to_version=db_orchestration.version,
            mode="manual",
            status="applied",
            detail={"selected_apps": list(selected_apps)},
        )
        await db.commit()

        return group, task_ids

    async def _cleanup_instance_project(
        self,
        db: AsyncSession,
        instance: AppInstance,
    ) -> None:
        """删除应用实例关联的 Compose 项目及其实例行。

        项目删除会级联清理 project_id 关联的实例行；实例未关联项目或
        关联项目已丢失时（如部署中途失败、重启丢失协程），按项目名规则
        查找残留项目一并清理，避免同名重建时 409。
        """
        from app.services.app_store.app_service import _slugify
        from app.services.compose.compose_service import compose_manager

        cascade_deleted = False
        if instance.project_id:
            project = await db.get(DockerComposeProject, instance.project_id)
            if project is not None:
                await compose_manager.delete_project(db, project)
                cascade_deleted = True
        if not cascade_deleted:
            result = await db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name
                    == _slugify(instance.instance_name)
                )
            )
            project = result.scalar_one_or_none()
            if project is not None:
                await compose_manager.delete_project(db, project)
            await db.delete(instance)

    def _ensure_deploy_capacity(self, required: int) -> None:
        """部署前检查并发任务槽位，避免创建实例组后才发现无法部署。

        组合部署会为每个应用创建一个部署任务；槽位不足时若等到创建
        任务阶段才失败，会留下一个没有应用的空实例组，用户重试时还会
        产生同名重复记录。
        """
        available = deploy_task_manager.available_slots()
        if required > available:
            raise APIException(
                f"同时部署的应用过多：本次需要 {required} 个并发槽位，"
                f"当前仅剩 {available} 个，请等待正在进行的部署完成后再试",
                409,
            )

    async def _ensure_project_names_free(
        self,
        db: AsyncSession,
        instance_name: str,
        selected_apps: list[str],
    ) -> None:
        """部署前检查 Compose 项目名是否被占用。

        部署失败（如镜像拉取失败）可能留下未关联实例的项目记录；
        若等到逐应用创建时才报冲突，会留下空的实例组记录。
        """
        from app.services.app_store.app_service import _slugify

        project_names: list[str] = []
        for app_name in selected_apps:
            try:
                project_names.append(_slugify(f"{instance_name}-{app_name}"))
            except ValueError:
                continue
        if not project_names:
            return
        result = await db.execute(
            select(DockerComposeProject).where(
                DockerComposeProject.project_name.in_(project_names)
            )
        )
        existing = sorted(project.project_name for project in result.scalars())
        if existing:
            raise APIException(
                f"项目名 {', '.join(existing)} 已存在，请更换实例名称或先删除旧项目",
                409,
            )

    def _preflight_app_configs(
        self,
        orchestration,
        app_map,
        selected_apps: list[str],
        app_configs: dict[str, dict],
        shared_config: dict,
    ) -> None:
        """在创建实例组之前校验所有应用的合并配置。

        与部署循环使用相同的合并规则（共享配置 + 组合镜像覆盖），
        任一应用不合法时立即抛错，不产生半成品实例组或已启动的应用。
        """
        composition_by_name = {
            item.get("app_name"): item
            for item in (orchestration.app_composition or [])
        }
        for app_name in selected_apps:
            merged_config = merge_shared_config(
                shared_config=shared_config,
                app_config=app_configs.get(app_name) or {},
                shared_schema=orchestration.shared_config_schema,
                app_schema=app_map[app_name].config_schema,
                app_name=app_name,
            )
            image_override = _resolve_composition_image(
                composition_by_name.get(app_name)
            )
            if image_override:
                merged_config["image"] = image_override
            validate_app_config(app_map[app_name], merged_config)

    def _validate_composition(
        self,
        composition: list[dict],
        selected_apps: list[str],
        require_required: bool = True,
    ) -> None:
        """校验用户选择的应用是否满足组合关系。

        Args:
            require_required: 是否要求必选应用全部选中。修复/重新部署时
                只需重部署部分应用，运行中的必选应用保持不动。
        """
        selected_set = set(selected_apps)
        composition_by_name = {item.get("app_name"): item for item in composition}

        # 1. 必选应用必须选中
        if require_required:
            for item in composition:
                if item.get("relation") == "required":
                    app_name = item.get("app_name")
                    if app_name not in selected_set:
                        raise APIException(
                            f"应用 {app_name} 为必选应用，必须部署", 400
                        )

        # 2. 所选应用必须在组合定义中
        for app_name in selected_apps:
            if app_name not in composition_by_name:
                raise APIException(
                    f"应用 {app_name} 不在当前编排组合中", 400
                )

        # 3. 组合内互斥检测
        for item in composition:
            app_name = item.get("app_name")
            if app_name not in selected_set:
                continue
            conflict_with = item.get("conflict_with") or []
            for conflict_app in conflict_with:
                if conflict_app in selected_set:
                    raise APIException(
                        f"应用 {app_name} 与 {conflict_app} 互斥，不能同时部署", 409
                    )

    async def _load_apps(
        self,
        db: AsyncSession,
        app_names: list[str],
    ) -> dict[str, App]:
        """批量加载应用商店应用。"""
        if not app_names:
            return {}
        result = await db.execute(
            select(App).where(App.name.in_(app_names))
        )
        apps = result.scalars().all()
        return {app.name: app for app in apps}

    async def _check_global_conflicts(
        self,
        db: AsyncSession,
        composition: list[dict],
        selected_apps: list[str],
    ) -> None:
        """检测已部署实例中是否存在与本次部署应用互斥的运行中实例。"""
        selected_set = set(selected_apps)
        conflict_map: dict[str, set[str]] = {}

        for item in composition:
            app_name = item.get("app_name")
            if app_name not in selected_set:
                continue
            for conflict_app in item.get("conflict_with") or []:
                conflict_map.setdefault(conflict_app, set()).add(app_name)

        if not conflict_map:
            return

        result = await db.execute(
            select(AppInstance, App.name)
            .join(App, AppInstance.app_id == App.id)
            .where(
                App.name.in_(conflict_map.keys()),
                AppInstance.status == "running",
            )
        )
        rows = result.all()
        if rows:
            conflict_app = rows[0][1]
            conflicting_selected = conflict_map[conflict_app]
            raise APIException(
                f"应用 {conflict_app} 已在运行，与 {', '.join(conflicting_selected)} 互斥",
                409,
            )
