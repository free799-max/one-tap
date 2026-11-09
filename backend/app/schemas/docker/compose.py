"""Docker Compose 编排相关 Pydantic Schema。"""

from typing import Literal

from pydantic import BaseModel


class ComposeProjectCreate(BaseModel):
    """创建 Compose 项目请求。"""

    project_name: str
    description: str | None = None
    content: str


class ComposeProjectUpdate(BaseModel):
    """更新 Compose 项目元数据请求。"""

    description: str | None = None
    is_active: bool | None = None


class ComposeEditRequest(BaseModel):
    """编辑 Compose 项目并自动部署请求。"""

    content: str
    comment: str | None = None
    description: str | None = None


class ComposeVersionOut(BaseModel):
    """Compose 版本响应。"""

    id: int
    version_number: int
    content: str
    comment: str | None = None
    is_current: bool
    created_by_user_id: int | None = None
    created_at: str

    class Config:
        from_attributes = True


class ComposeVersionCreate(BaseModel):
    """新增 Compose 版本请求。"""

    content: str


class ComposeStackStatusOut(BaseModel):
    """Compose Stack 运行时状态响应。"""

    status: str
    service_count: int
    running_count: int
    ports: list[str]
    last_action: str | None = None
    last_action_at: str | None = None
    updated_at: str


class ComposeProjectOut(BaseModel):
    """Compose 项目响应。"""

    id: int
    project_name: str
    description: str | None = None
    is_active: bool
    current_version: ComposeVersionOut | None = None
    stack: ComposeStackStatusOut | None = None
    config_files: list[str] | None = None
    working_dir: str | None = None
    host_config_files: list[str] | None = None
    """宿主机路径视图；未启用路径别名或与容器路径相同时为 None。"""
    host_working_dir: str | None = None
    compose_available: bool = True
    """Compose 文件与工作目录是否在容器内可见，决定项目能否完整维护。"""
    compose_unavailable_reason: str | None = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class ComposeActionRequest(BaseModel):
    """Compose 项目操作请求。"""

    action: Literal["up", "down", "restart"]


class ComposeLogQuery(BaseModel):
    """Compose 日志查询参数。"""

    tail: int = 100
    services: list[str] | None = None
