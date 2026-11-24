"""外部 Compose 项目与独立容器的导入服务。

导入采用「就地纳管」，不会重建、重启或删除任何现有容器，也不会改写
宿主机上的原始 compose 文件：

- 先用 ``docker compose config`` 把原配置规范化为绝对路径，再复制到
  OneTap 工作区，后续维护只操作副本；
- 项目来源标记为 imported，服务概览据此展示并关闭自动升级；
- 导入前必须确认项目目录已挂载到 OneTap 容器，否则无法读取配置。
"""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

import yaml
from sqlalchemy import func, select, update

from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance
from app.services.compose.compose_service import compose_manager
from app.services.docker import docker_manager
from app.services.system.host_path_aliases import host_path_aliases

_PROJECT_LABEL = "com.docker.compose.project"
_ONETAP_PROJECT_LABEL = "onetap.compose.project"
_PLACEHOLDER_PREFIX = "imported-"
_PLACEHOLDER_MAX_NAME = 50


def _parse_config_files(project: DockerComposeProject) -> list[Path]:
    if not project.config_files:
        return []
    try:
        paths = json.loads(project.config_files)
    except json.JSONDecodeError:
        return []
    if not isinstance(paths, list):
        return []
    return [Path(str(path)) for path in paths if path]


def _project_working_dir(project: DockerComposeProject) -> Path | None:
    value = (project.working_dir or "").strip()
    if not value:
        return None
    return Path(value)


def _is_under(path: Path, base: Path) -> bool:
    value = os.path.normpath(str(path))
    root = os.path.normpath(str(base))
    return value == root or value.startswith(root + os.sep)


def _workspace_paths(project: DockerComposeProject) -> bool:
    """项目文件与工作目录是否都位于 OneTap 工作区（即 OneTap 自己创建）。"""
    files = _parse_config_files(project)
    working_dir = _project_working_dir(project)
    workspace = compose_manager.workspace
    if not files:
        return False
    if not all(_is_under(path, workspace) for path in files):
        return False
    return working_dir is None or _is_under(working_dir, workspace)


def is_external_project(
    project: DockerComposeProject, has_instance: bool
) -> bool:
    """判断项目是否为尚未接管的外部项目。"""
    if project.source == "imported" or has_instance:
        return False
    if not _parse_config_files(project):
        return False
    return not _workspace_paths(project)


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = os.path.normpath(str(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _inspect_project(
    project: DockerComposeProject, containers: list[dict]
) -> dict:
    """检查项目目录挂载状态并汇总展示信息。"""
    files = _parse_config_files(project)
    working_dir = _project_working_dir(project)
    required = [path.parent for path in files]
    if working_dir is not None:
        required.append(working_dir)
    required = _unique_paths(required)

    missing_dirs = [path for path in required if not path.is_dir()]
    missing_files = [
        path
        for path in files
        if path.parent.is_dir() and not path.is_file()
    ]

    reason: str | None = None
    if missing_dirs:
        reason = "项目目录未挂载到 OneTap 容器"
    elif missing_files:
        reason = "Compose 文件不存在"

    ready = not missing_dirs and not missing_files
    if ready:
        try:
            files[0].read_text(encoding="utf-8")
        except OSError:
            ready = False
            reason = "Compose 文件不可读"

    project_containers = [
        container
        for container in containers
        if (container.get("labels") or {}).get(_PROJECT_LABEL)
        == project.project_name
    ]
    images: list[str] = []
    ports: list[str] = []
    running = 0
    for container in project_containers:
        if container.get("status") == "running":
            running += 1
        image = str(container.get("image") or "")
        if image and image != "unknown" and image not in images:
            images.append(image)
        summary = str(container.get("ports") or "")
        if summary:
            ports.append(summary)

    return {
        "project_id": project.id,
        "project_name": project.project_name,
        "config_files": [str(path) for path in files],
        "host_config_files": [
            host_path_aliases.to_host_path(path) for path in files
        ],
        "working_dir": str(working_dir) if working_dir is not None else None,
        "host_working_dir": (
            host_path_aliases.to_host_path(working_dir)
            if working_dir is not None
            else None
        ),
        "required_dirs": [host_path_aliases.to_host_path(path) for path in required],
        "missing_dirs": [
            host_path_aliases.to_host_path(path) for path in missing_dirs
        ],
        "ready": ready,
        "reason": reason,
        "container_count": len(project_containers),
        "running_count": running,
        "images": images,
        "ports": ports,
    }


def _managed_container_refs(configs: list[dict]) -> set[str]:
    refs: set[str] = set()
    for config in configs:
        if not isinstance(config, dict):
            continue
        for key in ("container_id", "container_name"):
            value = config.get(key)
            if value:
                refs.add(str(value))
    return refs


def _first_published_port(container: dict) -> tuple[int | None, int | None]:
    """取容器第一个已发布的宿主机端口与容器端口。"""
    for binding in container.get("port_bindings") or []:
        host_port = binding.get("host_port")
        container_port = str(binding.get("container_port") or "").split("/")[0]
        try:
            return int(host_port), int(container_port)
        except (TypeError, ValueError):
            continue
    return None, None


def _is_self_container(container: dict) -> bool:
    hostname = socket.gethostname()
    container_id = str(container.get("id") or "")
    container_name = str(container.get("name") or "")
    return (
        container_name == hostname
        or container_id[:12] == hostname[:12]
    )


async def list_live_containers() -> list[dict]:
    if not await docker_manager.run_sync(docker_manager.check_available):
        return []
    return await docker_manager.run_sync(docker_manager.list_containers)


async def _load_projects(
    db,
) -> list[tuple[DockerComposeProject, int | None]]:
    result = await db.execute(
        select(DockerComposeProject, AppInstance.id)
        .outerjoin(
            AppInstance, AppInstance.project_id == DockerComposeProject.id
        )
        .order_by(DockerComposeProject.id.desc())
    )
    return [(row[0], row[1]) for row in result.all()]


async def scan_candidates(db) -> dict:
    """扫描尚未接管的外部 Compose 项目与独立容器。"""
    await compose_manager.discover_projects(db)
    containers = await list_live_containers()

    configs_result = await db.execute(select(AppInstance.config))
    managed_refs = _managed_container_refs(
        [config for (config,) in configs_result.all()]
    )

    # OneTap 自身所在的 Compose 项目不提示导入
    self_projects = {
        (container.get("labels") or {}).get(_PROJECT_LABEL)
        for container in containers
        if _is_self_container(container)
    }
    self_projects.discard(None)

    projects: list[dict] = []
    for project, instance_id in await _load_projects(db):
        if project.project_name in self_projects:
            continue
        if not is_external_project(project, instance_id is not None):
            continue
        projects.append(_inspect_project(project, containers))

    container_items: list[dict] = []
    for container in containers:
        labels = container.get("labels") or {}
        if labels.get(_PROJECT_LABEL) or labels.get(_ONETAP_PROJECT_LABEL):
            continue
        if _is_self_container(container):
            continue
        if (
            str(container.get("id") or "") in managed_refs
            or str(container.get("name") or "") in managed_refs
        ):
            continue
        container_items.append(
            {
                "container_id": container.get("id") or "",
                "container_name": container.get("name") or "",
                "image": container.get("image"),
                "status": container.get("status") or "unknown",
                "state": container.get("state") or "",
                "ports": container.get("ports") or "",
            }
        )

    return {"projects": projects, "containers": container_items}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug or "external"


async def _get_or_create_import_app(
    db,
    *,
    base_name: str,
    display_name: str,
    image: str | None,
    content: str,
) -> App:
    """复用镜像完全匹配的应用，否则创建一个不在市场展示的占位应用。"""
    if image and image != "unknown":
        result = await db.execute(
            select(App).where(
                App.image == image, App.is_listed.is_(True)
            )
        )
        matches = result.scalars().all()
        if len(matches) == 1:
            return matches[0]

    base = f"{_PLACEHOLDER_PREFIX}{_slugify(base_name)}"
    base = base[:_PLACEHOLDER_MAX_NAME].rstrip("-")
    name = base
    index = 1
    while await db.scalar(select(App.id).where(App.name == name)) is not None:
        index += 1
        suffix = f"-{index}"
        name = f"{base[:_PLACEHOLDER_MAX_NAME - len(suffix)]}{suffix}"

    app = App(
        name=name,
        display_name=display_name[:100] or name,
        description=f"由外部对象导入：{display_name}",
        category="other",
        tags=["imported"],
        image=image if image and image != "unknown" else None,
        yaml_template=content or "services: {}",
        version="1.0.0",
        type="compose",
        is_listed=False,
    )
    db.add(app)
    await db.flush()
    return app


async def _canonical_compose_content(project: DockerComposeProject) -> str:
    """把原始配置规范化为绝对路径，避免复制到工作区后相对路径失效。"""
    try:
        result = await compose_manager._run(project, "config", timeout=60)
    except Exception as exc:  # noqa: BLE001 - 统一转换为可展示的错误
        raise ValueError(f"读取 Compose 配置失败：{exc}") from exc
    if result["returncode"] != 0:
        message = (result.get("stderr") or "").strip().splitlines()
        detail = message[-1] if message else "未知错误"
        raise ValueError(f"Compose 配置解析失败：{detail}")

    try:
        data = yaml.safe_load(result["stdout"])
    except yaml.YAMLError as exc:
        raise ValueError(f"Compose 配置解析失败：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ValueError("Compose 配置解析失败：未找到 services")

    # 顶层 name 交给 -p 参数决定，避免副本与项目名不一致
    data.pop("name", None)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


async def import_project(
    db, project: DockerComposeProject, user_id: int | None = None
) -> str:
    """把一个外部 Compose 项目导入为受管项目，返回应用名称。"""
    if project.source == "imported":
        raise ValueError("项目已导入")
    instance_id = await db.scalar(
        select(AppInstance.id).where(AppInstance.project_id == project.id)
    )
    if instance_id is not None:
        raise ValueError("项目已导入")

    containers = await list_live_containers()
    if any(
        _is_self_container(container)
        and (container.get("labels") or {}).get(_PROJECT_LABEL)
        == project.project_name
        for container in containers
    ):
        raise ValueError("不能导入 OneTap 自身所在的项目")
    state = _inspect_project(project, containers)
    if not state["ready"]:
        if state["missing_dirs"]:
            dirs = "、".join(state["missing_dirs"])
            raise ValueError(
                f"项目目录未挂载到 OneTap 容器：{dirs}；请先挂载目录后重试"
            )
        raise ValueError(state["reason"] or "项目配置不可读")

    content = await _canonical_compose_content(project)

    project_dir = compose_manager.workspace / project.project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    compose_file = project_dir / "docker-compose.yml"
    compose_file.write_text(content, encoding="utf-8")

    await db.execute(
        update(DockerComposeVersion)
        .where(DockerComposeVersion.project_id == project.id)
        .values(is_current=False)
    )
    max_version = (
        await db.scalar(
            select(func.max(DockerComposeVersion.version_number)).where(
                DockerComposeVersion.project_id == project.id
            )
        )
        or 0
    )
    version = DockerComposeVersion(
        project_id=project.id,
        version_number=max_version + 1,
        content=content,
        comment="从外部项目导入",
        is_current=True,
        created_by_user_id=user_id,
    )
    db.add(version)

    original_files = [str(path) for path in _parse_config_files(project)]
    original_working_dir = project.working_dir
    project.config_files = json.dumps([str(compose_file)], ensure_ascii=False)
    project.working_dir = str(project_dir)
    project.source = "imported"

    first_image = state["images"][0] if state["images"] else None
    app = await _get_or_create_import_app(
        db,
        base_name=project.project_name,
        display_name=project.project_name,
        image=first_image,
        content=content,
    )

    host_port: int | None = None
    container_port: int | None = None
    for container in containers:
        labels = container.get("labels") or {}
        if labels.get(_PROJECT_LABEL) != project.project_name:
            continue
        if container.get("status") != "running":
            continue
        host_port, container_port = _first_published_port(container)
        if host_port is not None:
            break

    config = {
        "imported": True,
        "source": "external_compose",
        "original_config_files": original_files,
        "original_working_dir": original_working_dir,
    }
    if host_port is not None:
        config["host_port"] = host_port
    if container_port is not None:
        config["container_port"] = container_port

    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name=project.project_name[:100],
        config=config,
        orchestration_version=app.version,
        status="running" if state["running_count"] > 0 else "stopped",
    )
    db.add(instance)
    await db.commit()
    return app.name


async def import_container(
    db, container: dict, user_id: int | None = None
) -> str:
    """把一个独立容器导入为受管应用（不创建 Compose 项目）。"""
    # 兼容两种来源：扫描候选（container_id/container_name）与 Docker 原始信息（id/name）
    container_id = str(
        container.get("container_id") or container.get("id") or ""
    )
    container_name = str(
        container.get("container_name") or container.get("name") or ""
    )
    if not container_id or not container_name:
        raise ValueError("容器信息不完整")

    configs = (await db.execute(select(AppInstance.config))).scalars().all()
    refs = _managed_container_refs(list(configs))
    if container_id in refs or container_name in refs:
        raise ValueError("容器已导入")

    host_port, container_port = _first_published_port(container)
    app = await _get_or_create_import_app(
        db,
        base_name=container_name,
        display_name=container_name,
        image=container.get("image"),
        content="services: {}",
    )
    config: dict = {
        "imported": True,
        "source": "docker_container",
        "container_id": container_id,
        "container_name": container_name,
    }
    if host_port is not None:
        config["host_port"] = host_port
    if container_port is not None:
        config["container_port"] = container_port

    instance = AppInstance(
        app_id=app.id,
        project_id=None,
        instance_name=container_name[:100],
        config=config,
        orchestration_version=app.version,
        status=(
            "running" if container.get("status") == "running" else "stopped"
        ),
    )
    db.add(instance)
    await db.commit()
    return app.name
