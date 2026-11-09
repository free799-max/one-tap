"""Hosts 优选相关 Pydantic Schema。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_HOSTS_OPTIMIZER_DOMAINS = [
    "api.themoviedb.org",
    "www.themoviedb.org",
    "image.tmdb.org",
    "api.tmdb.org",
    "themoviedb.org",
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
]
"""默认参与优选的域名。"""

DEFAULT_HOSTS_OPTIMIZER_INTERVAL_MINUTES = 360
"""自动模式默认间隔（6 小时）。"""

DEFAULT_HOSTS_OPTIMIZER_TOP_N = 2
"""每个域名默认保留的 IP 数量。"""


class HostsOptimizerConfig(BaseModel):
    """Hosts 优选配置。"""

    enabled: bool = Field(False, description="是否启用")
    mode: Literal["manual", "auto"] = Field("manual", description="manual 仅手动 / auto 定时")
    interval_minutes: int = Field(
        DEFAULT_HOSTS_OPTIMIZER_INTERVAL_MINUTES,
        ge=5,
        le=10080,
        description="自动模式运行间隔（分钟）",
    )
    top_n: int = Field(
        DEFAULT_HOSTS_OPTIMIZER_TOP_N,
        ge=1,
        le=5,
        description="每个域名保留的 IP 数量",
    )
    domains: list[str] = Field(
        default_factory=lambda: list(DEFAULT_HOSTS_OPTIMIZER_DOMAINS),
        description="参与优选的域名",
    )
    adguard_url: str | None = Field(None, max_length=500, description="AdGuard Home 管理地址")
    adguard_username: str = Field("admin", max_length=255, description="AdGuard 管理员用户名")
    adguard_password: str = Field(
        "", max_length=255, description="AdGuard 管理员密码（公开配置不回传明文）"
    )
    adguard_password_set: bool = Field(False, description="是否已保存 AdGuard 管理员密码")
    adguard_managed: bool = Field(
        False, description="AdGuard 是否由 OneTap 自动部署（只读）"
    )
    adguard_dns_ip: str | None = Field(
        None, description="应用加速使用的 AdGuard DNS 地址（只读）"
    )
    manage_configured: bool = Field(
        False, description="是否配置了配套管理端地址（只读）"
    )
    managed_rules: list[dict[str, Any]] = Field(
        default_factory=list, description="管理端下发的加速规则（只读）"
    )
    managed_rules_synced_at: str | None = Field(
        None, description="管理端规则最近同步时间（只读）"
    )


class HostsOptimizerConfigUpdate(HostsOptimizerConfig):
    """更新 Hosts 优选配置；密码留空表示沿用已保存值。"""


class HostsOptimizerTestRequest(BaseModel):
    """测试 AdGuard 连接。字段为空时使用已保存的配置。"""

    adguard_url: str | None = Field(None, max_length=500)
    adguard_username: str | None = Field(None, max_length=255)
    adguard_password: str | None = Field(None, max_length=255)


class HostsOptimizerTestResult(BaseModel):
    """AdGuard 连接测试结果。"""

    ok: bool
    version: str | None = None
    message: str = ""


class HostsOptimizerStatusOut(BaseModel):
    """Hosts 优选运行状态。"""

    running: bool = False
    next_run_at: str | None = None
    last_run: dict[str, Any] | None = None


class HostsOptimizerRunOut(BaseModel):
    """一次优选的结果。"""

    mode: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int = 0
    adguard: str = "ok"
    errors: list[str] = Field(default_factory=list)
    domains: dict[str, Any] = Field(default_factory=dict)


class ManagedDnsRule(BaseModel):
    """管理端下发的一条加速域名规则。"""

    domain: str
    answers: list[str] = Field(
        default_factory=list, description="管理端指定的 IP；为空时由客户端优选"
    )
    priority: int = 100
    description: str | None = None
    tier: str = Field("free", description="free 免费线路 / subscription 专属加速")


class ManagedDnsRulesOut(BaseModel):
    """管理端加速规则同步结果。"""

    configured: bool = Field(description="是否配置了管理端地址")
    rules: list[ManagedDnsRule] = Field(default_factory=list)
    synced_at: str | None = None
    error: str | None = None


class AccelerationToggleRequest(BaseModel):
    """开启或关闭单个应用的 DNS 加速。"""

    enabled: bool


class AccelerationToggleOut(BaseModel):
    """应用加速开关操作结果。"""

    instance_id: int
    enabled: bool
    dns_ip: str | None = None
    services: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str = ""


class AccelerationAppOut(BaseModel):
    """可配置加速的应用实例。"""

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
    status: str
    services: list[str] = Field(default_factory=list)
    acceleration_enabled: bool = False
    dns: list[str] = Field(default_factory=list)
    can_enable: bool = False
    reason: str | None = None


class AccelerationAdguardOut(BaseModel):
    """自动部署的 AdGuard Home 状态。"""

    configured: bool = False
    managed: bool = False
    running: bool = False
    url: str | None = None
    web_url: str | None = None
    dns_ip: str | None = None
    setup_port: int | None = None
    web_port: int | None = None
    version: str | None = None
    network: str = "onetap-accel"
    fallback_dns: list[str] = Field(default_factory=list)
    message: str = ""


class AccelerationOverviewOut(BaseModel):
    """应用加速页面所需的聚合数据。"""

    adguard: AccelerationAdguardOut
    managed_rules: ManagedDnsRulesOut
    apps: list[AccelerationAppOut] = Field(default_factory=list)
    network_effective: bool | None = False
    network_rule_count: int = 0
    network_applied_rule_count: int = 0
    network_app_count: int = 0
    network_message: str = ""


class AdguardDeployTaskOut(BaseModel):
    """自动部署 AdGuard Home 的任务响应。"""

    task_id: str
    instance_name: str | None = None
    status: str = "deploying"


class AdguardDeployRequest(BaseModel):
    """应用市场安装弹窗提交的 AdGuard 部署参数。"""

    instance_name: str | None = Field(None, max_length=100)
    config: dict[str, Any] | None = None


class AdguardRemoveOut(BaseModel):
    """删除 OneTap 托管 AdGuard Home 的结果。"""

    removed: bool
    disabled_apps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str = ""
