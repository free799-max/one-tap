"""Docker Compose 项目自动发现服务。"""

import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.models.docker import DockerComposeProject, DockerComposeVersion

logger = logging.getLogger(__name__)

# Docker Compose 标准项目标签
_DOCKER_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_DOCKER_COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
_DOCKER_COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


class ComposeDiscoveryService:
    """扫描运行中的容器，自动发现/补全 Compose 项目记录。"""

    async def discover_projects(self, db) -> list[DockerComposeProject]:
        """扫描 Docker 容器，自动发现/补全 Compose 项目记录。

        通过标准标签 com.docker.compose.project 识别项目，
        对未入库项目创建记录并尝试读取当前 compose 文件内容作为初始版本。
        出于安全考虑，只读取 Compose 工作区目录内的文件，
        避免容器标签指向任意宿主机文件被吸收入库。
        """
        from app.config import settings
        from app.services.compose.yaml_utils import validate_yaml
        from app.services.docker import docker_manager

        if not docker_manager.available:
            result = await db.execute(
                select(DockerComposeProject).order_by(DockerComposeProject.id.desc())
            )
            return result.scalars().all()

        containers = docker_manager.list_containers()
        projects_map: dict[str, dict] = {}
        for c in containers:
            labels = c.get("labels") or {}
            if not isinstance(labels, dict):
                continue
            project_name = labels.get(_DOCKER_COMPOSE_PROJECT_LABEL)
            if not project_name:
                continue
            if project_name not in projects_map:
                projects_map[project_name] = {
                    "config_files": set(),
                    "working_dir": labels.get(_DOCKER_COMPOSE_WORKING_DIR_LABEL, ""),
                }
            config_files = labels.get(_DOCKER_COMPOSE_CONFIG_FILES_LABEL, "")
            if config_files:
                for cf in config_files.split(","):
                    cf = cf.strip()
                    if cf:
                        projects_map[project_name]["config_files"].add(cf)

        result = await db.execute(select(DockerComposeProject))
        existing = {p.project_name: p for p in result.scalars().all()}

        discovered: list[DockerComposeProject] = []
        for project_name, info in projects_map.items():
            config_files = sorted(info["config_files"])
            working_dir = info["working_dir"]
            if project_name in existing:
                project = existing[project_name]
                if config_files and not project.config_files:
                    project.config_files = json.dumps(config_files, ensure_ascii=False)
                if working_dir and not project.working_dir:
                    project.working_dir = working_dir
                discovered.append(project)
                continue

            project = DockerComposeProject(
                project_name=project_name,
                description=None,
                source="external",
                config_files=json.dumps(config_files, ensure_ascii=False) if config_files else None,
                working_dir=working_dir or None,
            )
            db.add(project)
            await db.flush()

            if config_files:
                first_file = Path(config_files[0])
                try:
                    resolved_file = first_file.resolve()
                except OSError:
                    resolved_file = None
                # 只允许读取 Compose 工作区内的文件，防止容器标签指向
                # 任意宿主机文件（如 /etc/passwd）被读取入库
                workspace = Path(settings.COMPOSE_WORKSPACE_DIR).resolve()
                if resolved_file is None or not resolved_file.is_relative_to(workspace):
                    if resolved_file is not None and resolved_file.exists():
                        logger.warning(
                            "跳过读取工作区外的 compose 文件: %s", first_file
                        )
                elif resolved_file.exists():
                    try:
                        content = resolved_file.read_text(encoding="utf-8")
                        validate_yaml(content)
                        version = DockerComposeVersion(
                            project_id=project.id,
                            version_number=1,
                            content=content,
                            comment=None,
                            is_current=True,
                            created_by_user_id=None,
                        )
                        db.add(version)
                    except Exception as e:
                        logger.warning(
                            "读取外部项目 %s 的 compose 文件失败: %s", project_name, e
                        )

            discovered.append(project)

        await db.commit()
        for p in discovered:
            await db.refresh(p)
        return discovered
