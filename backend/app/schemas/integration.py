"""应用集成相关的 Pydantic Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


IntegrationKind = Literal["vaultwarden", "network"]
NetworkProvider = Literal["lucky", "npm"]
TestableProvider = Literal["lucky", "npm", "ddnsgo"]


class IntegrationStatusOut(BaseModel):
    """最近一次连接测试或同步结果。"""

    ok: bool | None = None
    message: str = ""
    version: str | None = None
    checked_at: datetime | None = None
    last_synced_at: datetime | None = None


class IntegrationSummaryOut(BaseModel):
    """集成入口摘要。"""

    kind: IntegrationKind
    enabled: bool
    configured: bool
    status: IntegrationStatusOut = Field(default_factory=IntegrationStatusOut)


class VaultwardenConfigOut(BaseModel):
    """Vaultwarden 集成配置（凭据不回传明文密码）。"""

    kind: Literal["vaultwarden"] = "vaultwarden"
    enabled: bool = False
    configured: bool = False
    managed: bool = False
    running: bool = False
    url: str = ""
    web_url: str | None = None
    web_port: int | None = None
    email: str = ""
    password_set: bool = False
    synced_count: int = 0
    status: IntegrationStatusOut = Field(default_factory=IntegrationStatusOut)


class VaultwardenDeployRequest(BaseModel):
    """部署 OneTap 托管实例；空凭据沿用已保存配置。"""

    email: str | None = None
    password: str | None = None
    web_port: int | None = Field(default=None, ge=1, le=65535)
    # 部署弹窗里的应用配置（存储、环境变量、特权模式）
    config: dict | None = None


class VaultwardenDeployTaskOut(BaseModel):
    task_id: str
    instance_name: str
    deploying: bool = True


class VaultwardenRemoveOut(BaseModel):
    removed: bool
    message: str


class VaultwardenUpdate(BaseModel):
    """保存 Vaultwarden 配置；password 为空表示沿用已保存密码。"""

    enabled: bool = False
    url: str = ""
    email: str = ""
    password: str | None = None


class VaultwardenTestRequest(BaseModel):
    """测试 Vaultwarden 连接。未提供的字段回退到已保存配置。"""

    url: str | None = None
    email: str | None = None
    password: str | None = None


class IntegrationTestResult(BaseModel):
    """连接测试结果。"""

    ok: bool
    message: str
    version: str | None = None


class IntegrationAppOut(BaseModel):
    """已部署应用及其可供集成使用的凭据摘要。"""

    instance_id: int
    app_name: str
    display_name: str
    instance_name: str
    status: str
    url: str | None = None
    icon: str | None = None
    username: str | None = None
    password_set: bool = False
    api_key_set: bool = False
    has_credentials: bool = False


class VaultwardenAppOut(IntegrationAppOut):
    """Vaultwarden 同步列表中的应用。"""

    synced: bool = False


class VaultwardenSyncRequest(BaseModel):
    """同步指定应用；为空时同步全部有凭据的应用。"""

    instance_ids: list[int] | None = None


class IntegrationSyncItem(BaseModel):
    """单个应用的同步结果。"""

    instance_id: int
    display_name: str
    ok: bool
    message: str


class IntegrationSyncResult(BaseModel):
    """批量同步结果。"""

    ok: bool
    message: str
    items: list[IntegrationSyncItem] = []


class NetworkDdnsFamilyOut(BaseModel):
    """单个 IP 协议族的 DDNS 配置（密钥不回传明文）。"""

    enabled: bool = False
    provider: str = "cloudflare"
    access_id: str = ""
    secret_set: bool = False
    domains: list[str] = []
    manual_domains: list[str] = []
    auto_domains: list[str] = []
    interval: int = 300
    get_type: Literal["url", "netInterface"] = "url"
    url: str = ""
    net_interface: str = ""
    ip_reg: str = ""


class NetworkDdnsOut(BaseModel):
    """网络集成的 DDNS 配置；IPv4 与 IPv6 各维护一套。"""

    enabled: bool = False
    ipv4: NetworkDdnsFamilyOut = Field(default_factory=NetworkDdnsFamilyOut)
    ipv6: NetworkDdnsFamilyOut = Field(default_factory=NetworkDdnsFamilyOut)


class LuckyConnectionOut(BaseModel):
    """Lucky 连接与反向代理监听配置。"""

    url: str = ""
    username: str = ""
    password_set: bool = False
    listen_port: int = 443
    enable_tls: bool = False
    # OneTap 托管部署时才有意义：是否托管、容器是否运行、管理端口
    managed: bool = False
    running: bool = False
    external: bool = False
    external_name: str | None = None
    web_port: int | None = None
    web_url: str | None = None


class NetworkLuckyDeployRequest(BaseModel):
    """部署 OneTap 托管的 Lucky；空凭据沿用已保存配置。"""

    username: str | None = None
    password: str | None = None
    web_port: int | None = Field(default=None, ge=1, le=65535)
    # 部署弹窗里的应用配置（存储、环境变量、特权模式）
    config: dict | None = None


class NetworkLuckyDeployTaskOut(BaseModel):
    task_id: str
    instance_name: str
    deploying: bool = True


class NetworkLuckyRemoveOut(BaseModel):
    removed: bool
    message: str


class NpmConnectionOut(BaseModel):
    """Nginx Proxy Manager 连接与证书配置。"""

    url: str = ""
    email: str = ""
    password_set: bool = False
    certificate_id: int | Literal["new"] = 0
    ssl_forced: bool = True


class DdnsGoConnectionOut(BaseModel):
    """DDNS-Go 连接配置。"""

    url: str = ""
    username: str = ""
    password_set: bool = False


class NetworkConfigOut(BaseModel):
    """网络集成配置。

    同一时间只启用一个反代方案：Lucky 自带 DDNS，
    或 Nginx Proxy Manager 搭配 DDNS-Go。
    """

    kind: Literal["network"] = "network"
    enabled: bool = False
    provider: NetworkProvider = "lucky"
    configured: bool = False
    target_host: str = ""
    lucky: LuckyConnectionOut = Field(default_factory=LuckyConnectionOut)
    npm: NpmConnectionOut = Field(default_factory=NpmConnectionOut)
    ddnsgo: DdnsGoConnectionOut = Field(default_factory=DdnsGoConnectionOut)
    status: IntegrationStatusOut = Field(default_factory=IntegrationStatusOut)
    ddns: NetworkDdnsOut = Field(default_factory=NetworkDdnsOut)


class LuckyConnectionUpdate(BaseModel):
    """保存 Lucky 连接；password 为空表示沿用已保存密码。"""

    url: str = ""
    username: str = ""
    password: str | None = None
    listen_port: int = Field(443, ge=1, le=65535)
    enable_tls: bool = False


class NpmConnectionUpdate(BaseModel):
    """保存 Nginx Proxy Manager 连接；password 为空表示沿用已保存密码。"""

    url: str = ""
    email: str = ""
    password: str | None = None
    certificate_id: int | Literal["new"] = 0
    ssl_forced: bool = True


class DdnsGoConnectionUpdate(BaseModel):
    """保存 DDNS-Go 连接；password 为空表示沿用已保存密码。"""

    url: str = ""
    username: str = ""
    password: str | None = None


class NetworkUpdate(BaseModel):
    """保存网络集成配置。三种连接配置都保留，切换方案不会丢配置。"""

    enabled: bool = False
    provider: NetworkProvider = "lucky"
    target_host: str = ""
    lucky: LuckyConnectionUpdate = Field(default_factory=LuckyConnectionUpdate)
    npm: NpmConnectionUpdate = Field(default_factory=NpmConnectionUpdate)
    ddnsgo: DdnsGoConnectionUpdate = Field(default_factory=DdnsGoConnectionUpdate)


class NetworkTestRequest(BaseModel):
    """测试网络集成连接。未提供的字段回退到已保存配置。"""

    provider: TestableProvider
    url: str | None = None
    username: str | None = None
    email: str | None = None
    password: str | None = None


class NetworkHostOut(BaseModel):
    """可作为反向代理后端的内网地址候选。"""

    value: str
    label: str
    kind: Literal["current", "lan"]


class NetworkHostsOut(BaseModel):
    """内网地址候选；当前访问地址排在第一位。"""

    current: str
    candidates: list[NetworkHostOut] = []


class NetworkAppOut(IntegrationAppOut):
    """网络集成反向代理列表中的应用或自定义后端。"""

    domain: str | None = None
    domains: list[str] = []
    target_url: str | None = None
    proxy_enabled: bool = False
    is_custom: bool = False
    listen_port: int | None = None
    enable_tls: bool = False
    network: Literal["tcp", "tcp4", "tcp6"] = "tcp"
    certificate_id: int = 0
    ssl_forced: bool = True


class NetworkProxyRequest(BaseModel):
    """为应用或自定义后端创建/更新反向代理。

    app_instance_id 为 0 时创建自定义代理，负数时编辑已有自定义代理。
    一个应用一条配置，可同时绑定多个公网/内网域名；Lucky 支持指定
    监听协议和规则级 HTTPS，NPM 支持指定证书与强制 HTTPS。
    """

    app_instance_id: int
    custom_name: str | None = Field(None, max_length=100)
    domains: list[str] = Field(..., min_length=1, max_length=50)
    target_url: str | None = None
    listen_port: int | None = Field(None, ge=1, le=65535)
    enable_tls: bool = False
    network: Literal["tcp", "tcp4", "tcp6"] = "tcp"
    certificate_id: int = Field(0, ge=0)
    ssl_forced: bool = True


class NetworkCertificateOut(BaseModel):
    """外部反代系统里可选的证书。"""

    id: str
    name: str
    domains: list[str] = []
    expires_on: str | None = None
    status: Literal["valid", "pending", "failed"] = "valid"
    error: str | None = None


class NetworkCertificateRequest(BaseModel):
    """自动添加证书。

    mode 为 auto 时：内网域名生成自签名证书，公网域名走 Let's Encrypt。
    """

    domains: list[str] = Field(..., min_length=1, max_length=50)
    mode: Literal["auto", "selfsigned", "acme"] = "auto"


class NetworkCertificateResult(BaseModel):
    """自动添加证书的结果；ACME 申请时证书可能是 pending。"""

    ok: bool
    message: str
    certificate: NetworkCertificateOut | None = None


class NetworkProxyResult(BaseModel):
    """反向代理操作结果。"""

    ok: bool
    message: str


class SecretRevealRequest(BaseModel):
    """首次验证当前账号密码，之后使用三小时内有效的查看授权。"""

    password: str | None = Field(default=None, min_length=1)
    reveal_token: str | None = Field(default=None, min_length=1)


class NetworkSecretRequest(SecretRevealRequest):
    provider: Literal["lucky", "npm", "ddnsgo", "ddns"]


class IntegrationSecretOut(BaseModel):
    """已保存的密码明文及本次查看授权；明文不参与缓存。"""

    password: str
    reveal_token: str
    expires_at: datetime


class NetworkDdnsTaskOut(BaseModel):
    """外部系统中已存在的 DDNS 任务及其最近一次同步结果。"""

    task_key: str
    task_name: str
    task_type: str
    domains: list[str] = []
    enabled: bool = False
    status: Literal["ok", "unchanged", "error"] | None = None
    message: str = ""
    address: str = ""
    last_sync: str | None = None
    next_sync: str | None = None


class NetworkDdnsStateOut(NetworkDdnsOut):
    """DDNS 配置与外部系统中的任务列表。"""

    tasks: list[NetworkDdnsTaskOut] = []
    message: str = ""


class NetworkDdnsInterfaceOut(BaseModel):
    """可用来获取 IP 的网卡。"""

    name: str
    hardware_addr: str = ""
    addresses: list[str] = []


class NetworkDdnsInterfacesOut(BaseModel):
    """Lucky 所在主机上的网卡列表，按协议族分开。"""

    ipv4: list[NetworkDdnsInterfaceOut] = []
    ipv6: list[NetworkDdnsInterfaceOut] = []


class NetworkDdnsUpdateFamily(BaseModel):
    """单个 IP 协议族的保存请求；secret 为空表示沿用已保存密钥。"""

    enabled: bool = False
    provider: str = "cloudflare"
    access_id: str = ""
    secret: str | None = None
    domains: list[str] = []
    interval: int = Field(300, ge=30, le=3600)
    get_type: Literal["url", "netInterface"] = "url"
    url: str = ""
    net_interface: str = ""
    ip_reg: str = ""


class NetworkDdnsUpdate(BaseModel):
    """保存 DDNS 配置并同步到当前网络方案。"""

    enabled: bool = False
    ipv4: NetworkDdnsUpdateFamily = Field(default_factory=NetworkDdnsUpdateFamily)
    ipv6: NetworkDdnsUpdateFamily = Field(default_factory=NetworkDdnsUpdateFamily)


class NpmCertificateOut(BaseModel):
    """Nginx Proxy Manager 中可用的证书。"""

    id: int
    nice_name: str = ""
    domain_names: list[str] = []
    expires_on: str | None = None

