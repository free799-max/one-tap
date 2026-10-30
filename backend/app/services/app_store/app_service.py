"""应用商店业务服务。"""

import asyncio
import logging
import re

import yaml
from jinja2 import Environment, BaseLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.background import create_background_task
from app.core.exceptions import APIException
from app.database import async_session
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance
from app.services.app_store.app_validator import (
    extract_default_values,
    validate_app_config,
)
from app.services.compose import compose_manager
from app.services.orchestration.deploy_task_service import deploy_task_manager
from app.services.system.port_allocator import (
    allocate_free_ports,
    get_used_ports,
    release_ports,
    reserve_ports,
)
from app.services.system import (
    StoragePathResolver,
    storage_directory_service,
    system_config_service,
)
from app.services.system.storage import is_host_system_path

logger = logging.getLogger(__name__)


def _strip_builtin_volume_mounts(config: dict, yaml_template: str) -> None:
    """移除模板已内置的宿主机系统挂载行（原地修改）。

    Docker 套接字等挂载已写进新版应用模板；旧实例配置里仍保留同一条
    可编辑挂载，若原样渲染会重复挂载导致部署失败，这里统一去掉。
    """
    volumes = config.get("volumes")
    if not isinstance(volumes, list):
        return
    template = yaml_template or ""
    kept: list = []
    for volume in volumes:
        if isinstance(volume, dict):
            host_path = str(volume.get("host_path") or "").strip()
            container_path = str(volume.get("container_path") or "").strip()
            if (
                is_host_system_path(host_path)
                and host_path
                and container_path
                and f"{host_path}:{container_path}" in template
            ):
                continue
        kept.append(volume)
    config["volumes"] = kept


def _render_yaml_template(
    yaml_template: str,
    config_schema: dict,
    config: dict,
    project_name: str,
    app_name: str = "",
    resolver: StoragePathResolver | None = None,
) -> str:
    """使用 Jinja2 渲染 Compose YAML 模板。

    合并 Schema 默认值与用户配置后渲染，并校验结果为合法 YAML。
    同时注入宿主机/容器挂载基础路径变量及路径转换函数。
    """
    default_values = extract_default_values(config_schema or {})
    merged = {**default_values, **(config or {})}
    merged["project_name"] = project_name
    merged["app_name"] = app_name

    if resolver is not None:
        merged["host_mount_base"] = resolver.host_mount_base
        merged["container_mount_base"] = resolver.container_mount_base

    try:
        env = Environment(loader=BaseLoader(), autoescape=False)
        if resolver is not None:
            env.globals["make_host_path"] = resolver.make_host_path
            env.globals["make_container_path"] = resolver.make_container_path
            env.globals["to_host_path"] = resolver.to_host_path
            env.globals["to_container_path"] = resolver.to_container_path
        rendered = env.from_string(yaml_template).render(merged)
    except Exception as e:
        raise ValueError(f"应用模板渲染失败: {e}") from e

    try:
        yaml.safe_load(rendered)
    except yaml.YAMLError as e:
        raise ValueError(f"渲染结果不是合法 YAML: {e}") from e

    return rendered


# linuxserver/qbittorrent 与官方 qbittorrent-nox 镜像使用不同的环境变量名。
# 应用模板按官方镜像定义 QBT_WEBUI_PORT/QBT_TORRENTING_PORT，编排覆盖为
# linuxserver 镜像后若不转换，WebUI 会回落到 8080，与端口映射的容器端口
# 不一致，宿主机端口无法访问，自动配置也会一直等待。
_QBITTORRENT_LSIO_REPOS = {
    "linuxserver/qbittorrent",
    "lscr.io/linuxserver/qbittorrent",
}
_QBITTORRENT_LSIO_ENV_ALIASES = {
    "QBT_WEBUI_PORT": "WEBUI_PORT",
    "QBT_TORRENTING_PORT": "TORRENTING_PORT",
}
_QBITTORRENT_LSIO_IGNORED_ENV = {"QBT_LEGAL_NOTICE"}

_IMAGE_PLACEHOLDER = re.compile(r"\{\{\s*image\s*\}\}")


def _image_repository(image: str) -> str:
    """去掉镜像标签/摘要，返回仓库名。"""
    repo = (image or "").split("@", 1)[0]
    last_segment = repo.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repo = repo.rsplit(":", 1)[0]
    return repo.lower()


def _normalize_qbittorrent_env(config: dict, image: str) -> None:
    """按 linuxserver 镜像的环境变量名改写 qBittorrent 配置（原地修改）。"""
    if _image_repository(image) not in _QBITTORRENT_LSIO_REPOS:
        return
    env_list = config.get("qbittorrent_env")
    if not isinstance(env_list, list):
        return

    normalized: list[dict] = []
    present_keys = {
        str(item.get("key"))
        for item in env_list
        if isinstance(item, dict) and item.get("key")
    }
    for item in env_list:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        key = str(item.get("key") or "")
        if key in _QBITTORRENT_LSIO_IGNORED_ENV:
            continue
        alias = _QBITTORRENT_LSIO_ENV_ALIASES.get(key)
        if alias:
            # 用户已显式提供目标变量时以用户配置为准
            if alias in present_keys:
                continue
            item = {**item, "key": alias}
            present_keys.add(alias)
        normalized.append(item)
    config["qbittorrent_env"] = normalized


def _apply_template_image(template: str, default_image: str, image: str) -> str:
    """让编排级镜像覆盖对未使用 ``{{ image }}`` 的旧模板也生效。

    部分应用模板把镜像写死（如 ``image: jellyfin/jellyfin``），而编排方案
    在组件级别声明了目标镜像/标签。这里仅在模板完全没有镜像占位符时替换
    与应用默认镜像完全一致的 image 行，避免误改多服务模板中依赖服务的镜像
    （如 MoviePilot 自带 postgresql/redis）。
    """
    if not image or not default_image or image == default_image:
        return template
    if _IMAGE_PLACEHOLDER.search(template):
        return template
    pattern = re.compile(
        rf"(?m)^(\s*image:\s*){re.escape(default_image)}(\s*)$"
    )
    return pattern.sub(rf"\g<1>{image}\g<2>", template)


def _main_container_name(db_app: App) -> str:
    """返回应用主容器对应的 Compose 服务名。"""
    containers = (db_app.config_schema or {}).get("containers") or []
    names = [str(item.get("name") or "") for item in containers]
    if db_app.name in names:
        return db_app.name
    return next((name for name in names if name), db_app.name)


def _extract_container_images(db_app: App) -> dict[str, str]:
    """从 Compose 模板提取各容器默认镜像，保留可选容器的镜像表达式。"""
    template = db_app.yaml_template or ""
    schema = db_app.config_schema or {}
    default_image = db_app.image or ""
    if not template or not default_image:
        return {}

    properties = schema.get("properties") or {}
    default_tag = str(
        (properties.get("image_tag") or {}).get("default") or "latest"
    )
    template_image = default_image
    if re.search(
        r"\{\{\s*image\s*\}\}\s*:\s*\{\{\s*image_tag\s*\}\}", template
    ):
        last_segment = default_image.rsplit("/", 1)[-1]
        if ":" in last_segment:
            template_image, default_tag = default_image.rsplit(":", 1)

    container_names = {
        str(item.get("name") or "")
        for item in (schema.get("containers") or [])
        if item.get("name")
    }
    images: dict[str, str] = {}
    env = Environment(loader=BaseLoader(), autoescape=False)
    in_services = False
    current_service = ""
    for line in template.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            current_service = service_match.group(1)
            continue
        image_match = re.match(r"^    image:\s*(.+?)\s*$", line)
        if not image_match or current_service not in container_names:
            continue
        raw_image = image_match.group(1).strip()
        if (
            len(raw_image) >= 2
            and raw_image[0] == raw_image[-1]
            and raw_image[0] in {"'", '"'}
        ):
            raw_image = raw_image[1:-1]
        try:
            image = env.from_string(raw_image).render(
                image=template_image,
                image_tag=default_tag,
            ).strip()
        except Exception:
            logger.warning("解析容器默认镜像失败: %s/%s", db_app.name, current_service)
            continue
        if image:
            images[current_service] = image

    main_container = _main_container_name(db_app)
    if main_container not in images:
        images[main_container] = default_image
    return images


def _normalize_container_image_overrides(config: dict) -> dict[str, str]:
    """把逐容器表单配置转换为服务名到完整镜像引用的映射。"""
    raw_overrides = config.get("container_images")
    if not isinstance(raw_overrides, dict):
        return {}
    overrides: dict[str, str] = {}
    for service_name, value in raw_overrides.items():
        if isinstance(value, str) and value.strip():
            overrides[str(service_name)] = value.strip()
            continue
        if not isinstance(value, dict):
            continue
        repository = str(value.get("repository") or "").strip()
        tag = str(value.get("tag") or "").strip()
        if not repository:
            continue
        last_segment = repository.rsplit("/", 1)[-1]
        if ":" in last_segment:
            repository, embedded_tag = repository.rsplit(":", 1)
            tag = embedded_tag or tag
        overrides[str(service_name)] = (
            f"{repository}:{tag}" if tag else repository
        )
    return overrides


def _apply_container_images(rendered_yaml: str, images: dict[str, str]) -> str:
    """按 Compose 服务名覆盖镜像，不创建模板中未启用的服务。"""
    compose = yaml.safe_load(rendered_yaml) or {}
    services = compose.get("services") or {}
    changed = False
    for service_name, image in images.items():
        service = services.get(service_name)
        if (
            image
            and isinstance(service, dict)
            and service.get("image") != image
        ):
            service["image"] = image
            changed = True
    if not changed:
        return rendered_yaml
    return yaml.safe_dump(compose, allow_unicode=True, sort_keys=False)


def _slugify(name: str) -> str:
    """将实例名称转换为合法 Compose 项目名。"""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_-]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    if not name:
        raise ValueError("实例名称无法生成有效项目名")
    return name[:50]


def _extract_app_ports(config_schema: dict) -> list[str]:
    """从 JSON Schema 中提取类型为 integer 且命名包含 port 的字段名。"""
    ports = []
    for key, prop in (config_schema.get("properties") or {}).items():
        if "port" in key.lower() and prop.get("type") == "integer":
            ports.append(key)
    return ports


def _collect_config_ports(config_schema: dict, config: dict) -> list[int]:
    """收集应用配置中会绑定到宿主机的端口（去重并保持顺序）。

    与 `_validate_and_prepare` 的分配口径一致：单端口字段 + ports 数组的
    local_port。固定端口应用靠这份列表做占用检测与预留。
    """
    ports: list[int] = []

    def _add(value) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            ports.append(value)
        elif isinstance(value, str) and value.isdigit():
            ports.append(int(value))

    for key in _extract_app_ports(config_schema):
        _add(config.get(key))
    entries = config.get("ports")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                _add(entry.get("local_port"))
    return list(dict.fromkeys(ports))


class AppService:
    """应用商店服务。

    负责应用查询与一键部署。
    """

    async def list_apps(
        self,
        db: AsyncSession,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[App]:
        """列出应用商店应用，支持分类和标签筛选。"""
        stmt = select(App)
        if category:
            stmt = stmt.where(App.category == category)
        stmt = stmt.order_by(App.category, App.display_name)
        result = await db.execute(stmt)
        apps = list(result.scalars().all())
        # tags 为 JSON 列，PostgreSQL 的 json 类型不支持 contains 查询，
        # 为保证 SQLite/PG 可移植性，标签过滤在 Python 侧做精确匹配
        if tag:
            apps = [a for a in apps if tag in (a.tags or [])]
        return apps

    async def get_app(self, db: AsyncSession, name: str) -> App:
        """获取单个应用详情。"""
        result = await db.execute(
            select(App).where(App.name == name)
        )
        app = result.scalar_one_or_none()
        if app is None:
            raise APIException("应用不存在", 404)
        app.container_images = _extract_container_images(app)
        return app

    async def _get_resolver(self, db: AsyncSession) -> StoragePathResolver:
        """读取存储目录配置构建路径解析器；未配置应用数据目录时使用根目录默认值。

        同时注入启用的数据目录，供模板渲染时做数据目录的路径前缀转换。
        """
        resolver = StoragePathResolver(
            app_data=await storage_directory_service.get_app_data_pair(db),
            data_directories=await storage_directory_service.list_enabled_pairs(db),
        )
        if not resolver.configured:
            resolver = resolver.with_defaults()
        return resolver

    async def preview(
        self,
        db: AsyncSession,
        app_name: str,
        instance_name: str,
        config: dict,
    ) -> dict:
        """预览应用渲染后的 Compose YAML。

        复用部署前的 JSON Schema 校验与渲染逻辑，但不创建项目与实例，
        也不做端口冲突预检（端口检测留在部署阶段执行）。
        校验失败时返回包含错误信息的字典，而不是抛异常。
        """
        db_app = await self.get_app(db, app_name)

        # 0. 读取系统存储配置；未配置时使用根目录默认值继续预览，
        #    实际部署前仍需配置真实路径。
        resolver = await self._get_resolver(db)

        # 1. JSON Schema 校验与端口预检失败时，把错误返回给前端展示
        try:
            project_name, _ = await self._validate_and_prepare(
                db_app=db_app,
                instance_name=instance_name,
                config=config,
                check_ports=False,
                resolver=resolver,
            )
        except APIException as e:
            return {"yaml": None, "error": e.message}

        return {
            "yaml": self._render_app(
                db_app=db_app,
                config=config,
                project_name=project_name,
                resolver=resolver,
            ),
            "error": None,
        }

    async def _validate_and_prepare(
        self,
        db_app: App,
        instance_name: str,
        config: dict,
        check_ports: bool = True,
        port_start: int = 10000,
        resolver: StoragePathResolver | None = None,
    ) -> tuple[str, list[int]]:
        """执行部署/预览前的公共校验，返回 (可用的项目名, 分配的端口列表)。

        Args:
            db_app: 应用模型
            instance_name: 实例名称
            config: 用户配置（部署时会原地改写其中的端口字段为自动分配的端口）
            check_ports: 是否进行端口分配；预览阶段为 False，部署阶段为 True
            port_start: 服务端口顺序分配的起始端口
            resolver: 存储路径解析器；配置了数据目录时用于校验 volumes 的
                宿主机路径是否落在受管理的存储目录下

        Note:
            返回的分配端口未预留，调用方需在部署前通过 reserve_ports 预留、
            部署结束后 release_ports 释放，防止并发部署重复分配。
        """
        allocated: list[int] = []
        schema = db_app.config_schema or {}

        # 1. JSON Schema 校验
        validate_app_config(db_app, config)

        # 1.2 新版模板已内置 Docker 套接字等系统挂载，旧实例配置中的
        #     同一条挂载需要剔除，否则会重复挂载同一容器路径。
        _strip_builtin_volume_mounts(config, db_app.yaml_template or "")

        # 1.5 数据目录受管路径校验（仅在配置了数据目录时执行）：
        #     此处校验的是应用自身的 volumes，共享配置合并在更上层发生；
        #     host_path 为空或相对路径会自动拼到 Docker 挂载目录下，视为受管，跳过校验；
        #     Docker 套接字等宿主机内置文件直接放行。
        if resolver is not None and resolver.has_data_directories:
            volumes = config.get("volumes")
            if isinstance(volumes, list):
                for volume in volumes:
                    if not isinstance(volume, dict):
                        continue
                    host_path = str(volume.get("host_path") or "").strip()
                    if not host_path or not host_path.startswith("/"):
                        continue
                    if is_host_system_path(host_path):
                        continue
                    if not resolver.is_managed_path(host_path):
                        raise APIException(
                            f"路径 {host_path} 不在已配置的应用数据目录或数据目录下，"
                            "请前往系统设置-数据目录配置",
                            400,
                        )

        # 2. 端口处理（仅在部署时执行）：
        #    - 普通应用从起始端口开始顺序分配，自动跳过已占用端口；
        #    - x-fixed-ports 应用（如 DNS 必须用 53）保留配置中的端口，
        #      不参与顺序分配，但仍需检测占用并预留，否则同批部署的其他
        #      应用会把相同端口分配走，直到 Docker 启动容器才报端口冲突。
        if check_ports and schema.get("x-fixed-ports"):
            fixed_ports = _collect_config_ports(schema, config)
            if fixed_ports:
                used_ports = await asyncio.to_thread(get_used_ports)
                conflicts = sorted(set(fixed_ports) & used_ports)
                if conflicts:
                    app_label = (
                        getattr(db_app, "display_name", None)
                        or getattr(db_app, "name", "")
                    )
                    prefix = f"{app_label} 的" if app_label else "固定端口"
                    raise APIException(
                        f"{prefix}端口已被占用，请调整应用端口或先释放占用后重试: "
                        + ", ".join(str(port) for port in conflicts),
                        409,
                    )
                # 交给调用方 reserve_ports 预留，部署结束后统一释放
                allocated = sorted(fixed_ports)
        elif check_ports:
            # 收集端口写入位置：(单端口字段名, None) 或 ("ports", 数组索引)
            targets: list[tuple[str, int | None]] = []

            # 2.1 单端口字段（按 schema properties 顺序，如 moviepilot_port: 3000）
            for key in _extract_app_ports(schema):
                if config.get(key) is not None:
                    targets.append((key, None))

            # 2.2 端口数组（按数组顺序，如 ports: [{local_port: 3000, ...}]）
            ports = config.get("ports")
            if isinstance(ports, list):
                for index, port_entry in enumerate(ports):
                    if not isinstance(port_entry, dict):
                        continue
                    if port_entry.get("local_port") is not None:
                        targets.append(("ports", index))

            if targets:
                used_ports = await asyncio.to_thread(get_used_ports)
                try:
                    allocated = allocate_free_ports(
                        used_ports, len(targets), port_start
                    )
                except ValueError as e:
                    raise APIException(str(e), 400) from e
                for (key, index), port in zip(targets, allocated):
                    if index is None:
                        config[key] = port
                    else:
                        config["ports"][index]["local_port"] = port

        # 3. 生成项目名
        return _slugify(instance_name), allocated

    async def deploy(
        self,
        db: AsyncSession,
        app_name: str,
        instance_name: str,
        config: dict,
        user_id: int | None = None,
    ) -> tuple[AppInstance, str]:
        """一键部署应用：校验并创建 deploying 实例，后台异步执行部署。

        返回 (AppInstance, task_id)，调用方应立即将 task_id 返回给前端。
        """
        db_app = await self.get_app(db, app_name)

        # 0. 读取并校验存储配置（应用数据目录 + 启用的数据目录）
        system_config = await system_config_service.get_or_create(db)
        resolver = await self._build_storage_resolver(db)

        project_name, allocated_ports = await self._validate_and_prepare(
            db_app=db_app,
            instance_name=instance_name,
            config=config,
            port_start=system_config.service_port_start or 10000,
            resolver=resolver,
        )

        # 4. 检查项目名是否已存在
        existing_project = await db.execute(
            select(DockerComposeProject).where(
                DockerComposeProject.project_name == project_name
            )
        )
        if existing_project.scalar_one_or_none():
            raise APIException(
                f"项目名 {project_name} 已存在，请更换实例名称", 409
            )

        # 4.1 预留分配端口，防止并发部署（同组多应用同时部署）重复分配；
        #     部署结束由 _do_deploy 释放，此处异常需主动释放
        reserve_ports(allocated_ports)

        # 5-7. 渲染 YAML、创建实例与部署任务；任一失败都要释放预留端口
        try:
            # 5. 渲染 YAML
            rendered_yaml = self._render_app(
                db_app=db_app,
                config=config,
                project_name=project_name,
                resolver=resolver,
            )

            # 6. 创建 deploying 状态实例
            instance = AppInstance(
                app_id=db_app.id,
                instance_name=instance_name,
                config=config,
                orchestration_version=db_app.version,
                status="deploying",
            )
            db.add(instance)
            await db.flush()
            await db.refresh(instance)

            # 7. 创建部署任务并在主事件循环中启动后台协程。
            # 先建任务再提交实例：任务创建失败（如并发超限）时回滚，不留孤儿实例
            try:
                task_id = deploy_task_manager.create_task(
                    "app_deploy",
                    instance_id=instance.id,
                    meta={
                        "app_id": db_app.id,
                        "app_name": db_app.name,
                        "icon": db_app.icon,
                        "instance_name": instance_name,
                        "project_name": project_name,
                        "rendered_yaml": rendered_yaml,
                        "allocated_ports": allocated_ports,
                        "user_id": user_id,
                        "description": f"由应用 {db_app.display_name} 部署",
                    },
                )
            except RuntimeError as e:
                await db.rollback()
                raise APIException(str(e), 409) from e
            await db.commit()
        except Exception:
            release_ports(allocated_ports)
            raise

        create_background_task(self._do_deploy(task_id, instance.id))

        return instance, task_id

    async def _do_deploy(self, task_id: str, instance_id: int):
        """实际执行应用部署并更新实例状态。"""
        task = deploy_task_manager.get_task(task_id)
        if not task:
            logger.error("部署任务不存在: %s", task_id)
            return

        meta = task.get("meta") or {}
        rendered_yaml = meta.get("rendered_yaml", "")
        project_name = meta.get("project_name", "")
        user_id = meta.get("user_id")
        description = meta.get("description")

        def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
            deploy_task_manager.update_progress(task_id, stage, percentage, message, detail)

        async with async_session() as db:
            try:
                _progress("preparing", 5, "准备部署")
                project = await compose_manager.create_project_async(
                    db,
                    project_name=project_name,
                    content=rendered_yaml,
                    user_id=user_id,
                    description=description,
                    progress_callback=_progress,
                    # 端口已在 deploy 阶段分配并预留，这里不再二次分配
                    allocate_ports=False,
                )

                # 更新实例为 running 并关联 project
                instance = await self.get_instance(db, instance_id)
                instance.project_id = project.id
                instance.status = "running"
                await db.commit()

                deploy_task_manager.complete_task(task_id)
            except Exception as e:
                logger.exception("应用部署失败: %s", e)
                error_msg = str(e)
                deploy_task_manager.fail_task(task_id, error_msg)

                try:
                    instance = await self.get_instance(db, instance_id)
                    instance.status = "error"
                    # 若项目已创建则关联 project_id，便于用户继续操作
                    if "project" in locals() and project:
                        instance.project_id = project.id
                    await db.commit()
                except Exception:
                    logger.exception("更新实例失败状态失败")
            finally:
                # 部署结束（成功/失败/超时），释放预留端口。
                # 成功时端口已绑定到容器，占用检测可接管；失败时端口未绑定，正确释放
                release_ports(meta.get("allocated_ports") or [])

    def _render_app(
        self,
        db_app: App,
        config: dict,
        project_name: str,
        resolver: StoragePathResolver,
    ) -> str:
        """渲染应用为 Compose YAML。"""
        if not db_app.yaml_template:
            raise APIException("应用模板为空，无法渲染", 500)

        # 主镜像继续注入现有模板上下文；逐容器覆盖优先于旧的全局配置。
        config = config or {}
        container_overrides = _normalize_container_image_overrides(config)
        main_container = _main_container_name(db_app)
        image = (
            container_overrides.get(main_container)
            or config.get("image")
            or db_app.image
        )
        merged_config = {**config, "image": image}

        # 部分模板把镜像与标签分开渲染（{{ image }}:{{ image_tag }}）。
        # 覆盖镜像可能已带标签，需拆开避免拼成 repo:tag:latest。
        if image and re.search(
            r"\{\{\s*image\s*\}\}\s*:\s*\{\{\s*image_tag\s*\}\}",
            db_app.yaml_template or "",
        ):
            last_segment = image.rsplit("/", 1)[-1]
            if ":" in last_segment:
                repo, tag = image.rsplit(":", 1)
                merged_config["image"] = repo
                merged_config["image_tag"] = tag

        _normalize_qbittorrent_env(merged_config, image)
        template = _apply_template_image(
            db_app.yaml_template, db_app.image, image
        )

        try:
            rendered = _render_yaml_template(
                template,
                db_app.config_schema,
                merged_config,
                project_name,
                db_app.name,
                resolver,
            )
            if container_overrides:
                container_images = {
                    **_extract_container_images(db_app),
                    **container_overrides,
                }
                if image:
                    container_images[main_container] = image
                return _apply_container_images(rendered, container_images)
            return rendered
        except ValueError as e:
            raise APIException(str(e), 500) from e

    async def _build_storage_resolver(self, db: AsyncSession) -> StoragePathResolver:
        """读取并校验当前存储目录配置。"""
        resolver = StoragePathResolver(
            app_data=await storage_directory_service.get_app_data_pair(db),
            data_directories=await storage_directory_service.list_enabled_pairs(db),
        )
        resolver.validate()
        return resolver

    async def get_instance(self, db: AsyncSession, instance_id: int) -> AppInstance:
        """获取单个应用实例（携带应用、项目及 Stack 关系）。"""
        from app.models.docker import DockerComposeProject

        result = await db.execute(
            select(AppInstance)
            .where(AppInstance.id == instance_id)
            .options(
                selectinload(AppInstance.app),
                selectinload(AppInstance.project).selectinload(
                    DockerComposeProject.stack
                ),
            )
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            raise APIException("应用实例不存在", 404)
        return instance
