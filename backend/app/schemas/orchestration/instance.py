"""编排实例组相关的 Pydantic Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrchestrationInstanceAppOut(BaseModel):
    """编排实例组中的应用实例概要。"""

    id: int
    app_name: str
    display_name: str
    icon: str | None = None
    status: str
    config: dict = {}
    # 关联的 Compose 项目；导入的独立容器为空
    project_id: int | None = None
    # 当前 Compose 主服务镜像；升级预览用
    current_image: str | None = None
    # 按当前模板升级后要使用的镜像
    target_image: str | None = None
    # 按当前访问主机与部署端口自动生成的访问地址；显式配置的 url 优先
    url: str | None = None


class OrchestrationInstanceGroupOut(BaseModel):
    """编排实例组（一次部署/导入记录）概要。"""

    id: int
    instance_name: str
    orchestration_name: str
    orchestration_display_name: str
    status: str
    created_at: datetime
    # 部署/升级时的方案模板版本快照；为空表示旧数据未记录，视为可升级
    orchestration_version: str | None = None
    # 模板当前版本
    template_version: str | None = None
    # 模板版本与实例组快照不一致（或快照缺失）时提示可升级
    upgrade_available: bool = False
    # 模板当前版本的更新说明
    template_changelog: str | None = None
    apps: list[OrchestrationInstanceAppOut] = []

    model_config = ConfigDict(from_attributes=True)


class OrchestrationInstanceDetailOut(OrchestrationInstanceGroupOut):
    """编排实例组详情，包含可编辑的配置信息。"""

    shared_config: dict = {}
    app_configs: dict = {}


class OrchestrationInstanceUpdateRequest(BaseModel):
    """编排实例组更新请求。"""

    instance_name: str | None = Field(None, min_length=1, max_length=100)
    shared_config: dict | None = None
    app_configs: dict | None = None


class InspectFixRequest(BaseModel):
    """检测项自动修复请求。"""

    item_ids: list[str] = []


class StorageMountRequest(BaseModel):
    """一键挂载请求：巡影网盘的连接 ID。"""

    source_id: str = Field(..., min_length=1, max_length=200)


class PluginInstallRequest(BaseModel):
    """应用插件安装请求。"""

    plugin_id: str = Field(..., min_length=1, max_length=100)
    repo_url: str | None = None


class OrchestrationBackupAppOut(BaseModel):
    """备份中的应用数据目录。"""

    app_name: str
    instance_name: str | None = None


class OrchestrationBackupOut(BaseModel):
    """可恢复的实例组备份。"""

    name: str
    instance_name: str
    orchestration_name: str | None = None
    deleted_at: str | None = None
    apps: list[OrchestrationBackupAppOut] = []


class OrchestrationRestoreRequest(BaseModel):
    """从备份恢复实例组请求。"""

    backup: str = Field(..., min_length=1, max_length=200)
    instance_name: str | None = Field(None, min_length=1, max_length=100)


class OrchestrationDeleteResponse(BaseModel):
    """删除实例组任务响应。"""

    task_id: str
