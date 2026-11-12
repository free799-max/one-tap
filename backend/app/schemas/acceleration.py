"""网络加速（VLESS Reality）请求与响应 Schema。

客户端只消费管理端下发的订阅：保存订阅地址、规则快照与本机运行状态。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AccelMihomoStatusOut(BaseModel):
    """本机 Mihomo 运行状态。"""

    installed: bool = False
    running: bool = False
    container_name: str = "onetap-mihomo"
    version: str = ""
    mixed_port: int = 7890
    controller: str = ""
    subscription_name: str = ""
    applied_revision: int = 0
    config_updated_at: str | None = None
    last_sync_at: str | None = None
    last_error: str | None = None
    subscription_url: str = ""
    error: str | None = None


class AccelMoviePilotOut(BaseModel):
    """MoviePilot 接入状态。"""

    installed: bool = False
    container: str = ""
    status: str = "unknown"
    proxy_env: str = ""
    expected_proxy: str = ""
    message: str = ""


class AccelSyncResultOut(BaseModel):
    """最近一次手动或自动同步的执行结果。"""

    status: str
    message: str
    trigger: str = "manual"
    changed: bool | None = None
    rules: int = 0
    revision: int = 0
    synced_at: str | None = None


class AccelManagedOut(BaseModel):
    """管理端订阅与本机同步状态。"""

    configured: bool
    subscription_url: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    rules_synced_at: datetime | None = None
    last_sync_at: str | None = None
    last_sync_error: str | None = None
    auto_sync_enabled: bool
    auto_sync_interval_minutes: int
    last_sync_result: AccelSyncResultOut | None = None
    mihomo: AccelMihomoStatusOut
    moviepilot: AccelMoviePilotOut


class AccelManagedUpdate(BaseModel):
    """保存订阅地址。"""

    subscription_url: str = Field(..., max_length=500)


class AccelSyncSettingsUpdate(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=5, le=1440)


class AccelActionResult(BaseModel):
    """带说明的操作结果。"""

    ok: bool
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AccelMihomoDeploy(BaseModel):
    config: dict[str, Any] | None = None


class AccelDeployTaskOut(BaseModel):
    task_id: str
    instance_name: str


class AccelProxyAppOut(BaseModel):
    """支持网络加速的应用实例。"""

    instance_id: int
    instance_name: str
    display_name: str
    app_name: str | None = None
    icon: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    orchestration_group_id: int | None = None
    orchestration_group_name: str | None = None
    orchestration_name: str | None = None
    status: str = "unknown"
    services: list[str] = Field(default_factory=list)
    acceleration_enabled: bool = False
    proxy_enabled: bool = False
    dns: list[str] = Field(default_factory=list)
    can_enable: bool = False
    reason: str | None = None


class AccelProxyAppUpdate(BaseModel):
    enabled: bool
