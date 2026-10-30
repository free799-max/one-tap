"""系统全局配置相关 Pydantic Schema。"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SystemConfigBase(BaseModel):
    """系统配置基础字段。"""

    proxy_enabled: bool = Field(False, description="是否启用代理")
    http_proxy: Optional[str] = Field(None, max_length=500, description="HTTP 代理地址")
    https_proxy: Optional[str] = Field(None, max_length=500, description="HTTPS 代理地址")
    no_proxy: Optional[str] = Field(None, max_length=500, description="不走代理的地址列表")
    service_port_start: int = Field(
        10000, ge=1, le=65535, description="服务端口顺序分配的起始端口"
    )
    default_admin_username: str = Field(
        "admin", max_length=255, description="部署服务时自动配置的统一管理员用户名"
    )
    default_admin_password: str = Field(
        "", max_length=255, description="部署服务时自动配置的统一管理员密码（留空则每次随机）"
    )
    default_email_domain: str = Field(
        "",
        max_length=255,
        description="需要邮箱登录的应用使用的邮箱后缀（不含 @）",
    )
    auto_update_enabled: bool = Field(
        False, description="是否开启自动更新（仅授权版可开启）"
    )
    docker_registry_mirrors: list[str] = Field(
        default_factory=list,
        description="Docker 镜像加速地址列表，按优先级排序",
    )


class SystemConfigOut(SystemConfigBase):
    """系统配置响应模型。"""

    id: int
    email_username_apps: list[str] = Field(
        default_factory=list,
        description="使用邮箱作为登录账号的应用标识",
    )


class SystemConfigUpdate(BaseModel):
    """系统配置更新请求模型（未提供的字段保持不变）。"""

    proxy_enabled: Optional[bool] = Field(None, description="是否启用代理")
    http_proxy: Optional[str] = Field(None, max_length=500)
    https_proxy: Optional[str] = Field(None, max_length=500)
    no_proxy: Optional[str] = Field(None, max_length=500)
    service_port_start: Optional[int] = Field(None, ge=1, le=65535)
    default_admin_username: Optional[str] = Field(None, max_length=255)
    default_admin_password: Optional[str] = Field(None, max_length=255)
    default_email_domain: Optional[str] = Field(None, max_length=255)
    auto_update_enabled: Optional[bool] = Field(
        None, description="是否开启自动更新（仅授权版可开启）"
    )
    docker_registry_mirrors: Optional[list[str]] = None


class ProxyTestRequest(BaseModel):
    """代理连通性检测请求；字段为 None 时使用已保存配置。"""

    http_proxy: Optional[str] = Field(None, max_length=500)
    https_proxy: Optional[str] = Field(None, max_length=500)


class ProxyTestSite(BaseModel):
    """单个站点的检测结果。"""

    name: str
    url: str
    ok: bool
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class ProxyTestResult(BaseModel):
    """一次代理连通性检测的完整结果。"""

    proxy: Optional[str] = Field(None, description="实际使用的代理地址，直接检测时为 null")
    ok_count: int
    total: int
    results: list[ProxyTestSite]


class RemoteDockerMirror(BaseModel):
    """管理端下发的 Docker 镜像加速源。"""

    name: str
    url: str
    priority: int = 100
    region: Optional[str] = None
    description: Optional[str] = None
    doc_url: Optional[str] = None
    last_check_ok: Optional[bool] = None
    last_latency_ms: Optional[int] = None
    last_checked_at: Optional[datetime] = None


class RemoteDockerMirrorsOut(BaseModel):
    """管理端镜像加速源拉取结果。"""

    configured: bool = Field(description="是否配置了管理端地址")
    mirrors: list[RemoteDockerMirror] = Field(default_factory=list)


class DockerMirrorTestRequest(BaseModel):
    """镜像加速地址检测请求。"""

    urls: list[str] = Field(default_factory=list, max_length=20)


class DockerMirrorTestItem(BaseModel):
    """单个镜像加速地址的检测结果。"""

    url: str
    ok: bool
    status_code: Optional[int] = None
    latency_ms: int
    error: Optional[str] = None


class DockerMirrorTestResult(BaseModel):
    """一次镜像加速地址检测的完整结果。"""

    ok_count: int
    total: int
    results: list[DockerMirrorTestItem]
