"""从删除备份恢复编排实例组。

删除实例组时应用数据目录被移入 ``<应用数据目录>/_backups``；恢复时：

1. 读取备份清单，按新的实例名称计算各应用的目标 Compose 项目名；
2. 把备份中的数据目录移回应用数据目录；
3. 使用备份中记录的部署配置与账号凭据重新组合部署，使旧数据可用。

恢复只处理应用数据目录，共享存储（媒体/下载）按备份记录原样沿用。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.docker import DockerComposeProject
from app.services.orchestration.backup import (
    list_app_backups,
    restore_app_data_dirs,
)

logger = logging.getLogger(__name__)


class RestoreMixin:
    """备份查询与恢复能力。"""

    async def list_backups(
        self,
        db: AsyncSession,
        orchestration_name: str | None = None,
    ) -> list[dict]:
        """列出可恢复的实例组备份（按时间倒序）。"""
        backups = await list_app_backups(db, orchestration_name)
        result: list[dict] = []
        for item in backups:
            manifest = item["manifest"]
            apps = [
                {
                    "app_name": entry.get("app_name"),
                    "instance_name": entry.get("instance_name"),
                }
                for entry in manifest.get("apps") or []
                if isinstance(entry, dict) and entry.get("app_name")
            ]
            result.append(
                {
                    "name": item["name"],
                    "instance_name": manifest.get("instance_name") or "",
                    "orchestration_name": manifest.get("orchestration"),
                    "deleted_at": manifest.get("deleted_at"),
                    "apps": apps,
                }
            )
        return result

    async def restore_backup(
        self,
        db: AsyncSession,
        backup_name: str,
        instance_name: str | None = None,
        user_id: int | None = None,
    ):
        """从备份恢复实例组：移回数据目录并按原配置重新部署。

        Returns:
            ``(group, task_ids)``，与组合部署一致
        """
        # 延迟导入：避免 app_store -> orchestration 的循环依赖
        from app.services.app_store.app_service import _slugify
        from app.services.orchestration.backup import resolve_app_data_root
        from app.services.orchestration.credentials import (
            _ENV_CREDENTIAL_SPECS,
            _get_env,
        )
        from app.services.system import system_config_service
        from app.services.system.config_service import resolve_username_for_app

        if await resolve_app_data_root(db) is None:
            raise APIException("未配置应用数据目录，无法恢复备份", 400)

        backups = await list_app_backups(db)
        matched = next((b for b in backups if b["name"] == backup_name), None)
        if matched is None:
            raise APIException("备份不存在", 404)
        manifest = matched["manifest"]

        orchestration_name = str(manifest.get("orchestration") or "").strip()
        if not orchestration_name:
            raise APIException("备份缺少编排信息，无法恢复", 400)
        orchestration = await self.get_orchestration(db, orchestration_name)
        composition = orchestration.app_composition or []

        origin_name = str(manifest.get("instance_name") or "")
        target_name = _slugify(instance_name or origin_name)
        if not target_name:
            raise APIException("请输入有效的实例名称", 400)

        app_entries = [
            entry
            for entry in manifest.get("apps") or []
            if isinstance(entry, dict) and entry.get("app_name")
        ]
        app_names: list[str] = []
        for entry in app_entries:
            if entry["app_name"] not in app_names:
                app_names.append(entry["app_name"])
        if not app_names:
            raise APIException("备份中没有可恢复的应用", 400)

        # 部署前校验：组合关系、应用是否存在、全局互斥
        self._validate_composition(composition, app_names)
        app_map = await self._load_apps(db, app_names)
        missing = [name for name in app_names if name not in app_map]
        if missing:
            raise APIException(f"应用不存在: {', '.join(missing)}", 400)
        await self._check_global_conflicts(db, composition, app_names)

        # 目标 Compose 项目名不能与现有项目冲突
        target_projects = {
            app_name: _slugify(f"{target_name}-{app_name}")
            for app_name in app_names
        }
        result = await db.execute(
            select(DockerComposeProject.project_name).where(
                DockerComposeProject.project_name.in_(
                    target_projects.values()
                )
            )
        )
        existing = list(result.scalars().all())
        if existing:
            raise APIException(
                f"目标项目已存在，请更换实例名称: {', '.join(existing)}", 409
            )

        deploy_configs = {
            entry["app_name"]: dict(entry.get("config") or {})
            for entry in app_entries
        }
        provision_configs = {
            name: value
            for name, value in (manifest.get("app_configs") or {}).items()
            if name in app_names and isinstance(value, dict)
        }

        # 新布局的挂载路径以实例名目录开头，恢复时换了实例名需要同步改写
        origin_slug = _slugify(origin_name) if origin_name else ""
        if origin_slug and target_name != origin_slug:
            from app.services.orchestration.backup import resolve_app_data_paths

            paths = await resolve_app_data_paths(db)
            if paths is not None:
                mount = str(paths[1]).rstrip("/")
                old_prefix = f"{mount}/{origin_slug}/"
                new_prefix = f"{mount}/{target_name}/"
                for config in deploy_configs.values():
                    volumes = config.get("volumes")
                    if not isinstance(volumes, list):
                        continue
                    for volume in volumes:
                        if not isinstance(volume, dict):
                            continue
                        host = str(volume.get("host_path") or "")
                        if host.startswith(old_prefix):
                            volume["host_path"] = new_prefix + host[len(old_prefix) :]

        # 把数据目录移回；目标目录冲突会在此处拒绝，不会动任何文件
        manifest, _restored = await restore_app_data_dirs(
            db, backup_name, target_projects, target_instance=target_name
        )

        # 沿用备份中的账号凭据；系统统一密码变化时通过 previous_password
        # 让客户端登录旧密码后再改成统一密码
        sys_config = await system_config_service.get_or_create(db)
        default_username = (
            (sys_config.default_admin_username or "").strip() or "admin"
        )
        email_domain = (sys_config.default_email_domain or "").strip()
        unified_password = (
            (sys_config.default_admin_password or "").strip() or None
        )

        credential_overrides: dict[str, dict] = {}
        for app_name in app_names:
            entry = provision_configs.get(app_name) or {}
            username = str(entry.get("username") or default_username)
            password = str(entry.get("password") or "")
            api_key = entry.get("api_key")

            spec = _ENV_CREDENTIAL_SPECS.get(app_name)
            if spec:
                # env 注入类应用（MoviePilot）的凭据只存在于部署配置里
                config = deploy_configs.get(app_name) or {}
                username = (
                    str(entry.get("username") or "")
                    or _get_env(config, spec["env_key"], spec["username_key"])
                    or default_username
                )
                password = password or (
                    _get_env(config, spec["env_key"], spec["password_key"]) or ""
                )
                api_key = api_key or _get_env(
                    config, spec["env_key"], spec["api_token_key"]
                )

            username = resolve_username_for_app(app_name, username, email_domain)

            if not password:
                # 旧备份没有凭据记录：交给部署流程按统一密码/随机生成处理
                continue
            creds: dict = {"username": username}
            if unified_password and unified_password != password:
                creds["password"] = unified_password
                creds["previous_password"] = password
            else:
                creds["password"] = password
            if api_key:
                creds["api_key"] = api_key
            credential_overrides[app_name] = creds

        # 新实例组继承认证信息（url 与装配结果由本次部署重新生成）
        initial_app_configs: dict[str, dict] = {}
        for app_name, entry in provision_configs.items():
            seeded = {
                key: entry[key]
                for key in ("auth_type", "username", "password", "api_key")
                if entry.get(key)
            }
            if seeded:
                if seeded.get("username"):
                    seeded["username"] = resolve_username_for_app(
                        app_name, str(seeded["username"]), email_domain
                    )
                initial_app_configs[app_name] = seeded

        logger.info(
            "从备份 %s 恢复实例组 %s（原实例组 %s）",
            backup_name,
            target_name,
            origin_name,
        )
        return await self.deploy(
            db,
            orchestration_name=orchestration_name,
            instance_name=target_name,
            selected_apps=app_names,
            app_configs=deploy_configs,
            shared_config=manifest.get("shared_config") or {},
            user_id=user_id,
            credential_overrides=credential_overrides,
            initial_app_configs=initial_app_configs,
            allow_existing_group=True,
        )
