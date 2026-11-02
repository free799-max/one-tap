"""
模型包初始化模块。

集中导出所有 SQLAlchemy ORM 模型类，方便其他模块通过
`from app.models import User` 的方式直接引用，
而无需关心模型定义在哪个子模块中。

导出的模型：
- User: 用户模型
- DockerMirrorConfig: Docker 镜像查询配置模型
- DockerComposeProject: Docker Compose 项目模型
- DockerComposeVersion: Docker Compose 版本模型
- DockerComposeStack: Docker Compose Stack 运行时状态模型
- StorageDirectory: 数据目录模型
"""

from app.models.user import User
from app.models.app_store import App
from app.models.orchestration import (
    AppOrchestration,
    AppOrchestrationInstance,
    AppInstance,
    AppInstanceBackup,
    MusicAutoOrganizeFile,
    OrchestrationUpdateEvent,
    OrchestrationUpgradeBackup,
    ReaderAutoSyncBook,
)
from app.models.system_config import SystemConfig
from app.models.integration import IntegrationConfig
from app.models.storage_directory import StorageDirectory
from app.models.license import LicenseState
from app.models.docker import (
    DockerMirrorConfig,
    DockerComposeProject,
    DockerComposeVersion,
    DockerComposeStack,
    COMPOSE_PROJECT_LABEL,
)

# __all__ 显式声明公开导出的模型名称，便于 `from app.models import *` 使用
__all__ = [
    "User", "App", "AppOrchestration", "AppOrchestrationInstance", "AppInstance", "AppInstanceBackup",
    "MusicAutoOrganizeFile",
    "OrchestrationUpdateEvent", "OrchestrationUpgradeBackup",
    "ReaderAutoSyncBook",
    "SystemConfig", "StorageDirectory", "LicenseState",
    "IntegrationConfig",
    "DockerMirrorConfig",
    "DockerComposeProject", "DockerComposeVersion", "DockerComposeStack",
    "COMPOSE_PROJECT_LABEL",
]
