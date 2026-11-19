"""应用编排相关的 Pydantic Schema 包。

按职责拆分为组合/导入（composition）、实例组（instance）、
模板与部署（orchestration）、部署任务（deploy_task）四个模块，
此处统一 re-export 以保持外部 import 路径不变。
"""

from app.schemas.orchestration.composition import (
    AppCompositionItem,
    ContainerMatch,
    ImportCandidateApp,
    OrchestrationImportAppConfig,
    OrchestrationImportRequest,
    OrchestrationImportResponse,
)
from app.schemas.orchestration.deploy_task import (
    ComposeDeployResponse,
    DeployTaskCreateResponse,
    DeployTaskProgress,
    DeployTaskStage,
    DeployTaskStatus,
    DeployTaskType,
)
from app.schemas.orchestration.instance import (
    InspectFixRequest,
    OrchestrationBackupAppOut,
    OrchestrationBackupOut,
    OrchestrationDeleteResponse,
    OrchestrationInstanceAppOut,
    OrchestrationInstanceDetailOut,
    OrchestrationInstanceGroupOut,
    OrchestrationInstanceUpdateRequest,
    OrchestrationRestoreRequest,
    PluginInstallRequest,
    StorageMountRequest,
)
from app.schemas.orchestration.moviepilot import (
    DirectoriesUpdateRequest,
    Directory,
    MediaLibrariesUpdateRequest,
    MediaLibrary,
    MediaLibraryAutoSyncUpdate,
    MediaServer,
    MediaServerSyncUpdate,
)
from app.schemas.orchestration.music_auto import (
    MusicAutoDirItem,
    MusicAutoOrganizeConfigIn,
)
from app.schemas.orchestration.reader_auto import ReaderAutoSyncConfigIn
from app.schemas.orchestration.resource_sources import ResourceSourceConfigIn
from app.schemas.orchestration.orchestration import (
    OrchestrationDeployRequest,
    OrchestrationDeployResponse,
    OrchestrationDetailOut,
    OrchestrationOut,
    OrchestrationUpdateEventOut,
)

__all__ = [
    "AppCompositionItem",
    "ComposeDeployResponse",
    "ContainerMatch",
    "DeployTaskCreateResponse",
    "DeployTaskProgress",
    "DeployTaskStage",
    "DeployTaskStatus",
    "DeployTaskType",
    "DirectoriesUpdateRequest",
    "Directory",
    "ImportCandidateApp",
    "InspectFixRequest",
    "MediaLibrariesUpdateRequest",
    "MediaLibrary",
    "MediaLibraryAutoSyncUpdate",
    "MediaServer",
    "MediaServerSyncUpdate",
    "MusicAutoDirItem",
    "MusicAutoOrganizeConfigIn",
    "ReaderAutoSyncConfigIn",
    "ResourceSourceConfigIn",
    "OrchestrationDeployRequest",
    "OrchestrationDeployResponse",
    "OrchestrationDetailOut",
    "OrchestrationImportAppConfig",
    "OrchestrationImportRequest",
    "OrchestrationImportResponse",
    "OrchestrationInstanceAppOut",
    "OrchestrationInstanceDetailOut",
    "OrchestrationInstanceGroupOut",
    "OrchestrationInstanceUpdateRequest",
    "OrchestrationOut",
    "OrchestrationUpdateEventOut",
    "OrchestrationRestoreRequest",
    "OrchestrationBackupAppOut",
    "OrchestrationBackupOut",
    "OrchestrationDeleteResponse",
    "PluginInstallRequest",
    "StorageMountRequest",
]
