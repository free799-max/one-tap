"""系统全局配置模型。

用于存储 OneTap 的全局设置，如代理、端口分配等。
整个系统只使用一条记录（id=1）来保存配置。
"""

from datetime import datetime

from sqlalchemy import Boolean, JSON, DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemConfig(Base):
    """系统全局配置模型。

    Attributes:
        id: 配置记录唯一标识，固定为 1
        proxy_enabled: 是否启用代理
        http_proxy: HTTP 代理地址
        https_proxy: HTTPS 代理地址
        no_proxy: 不走代理的地址列表
        service_port_start: 服务端口顺序分配的起始端口
        default_admin_username: 部署服务时自动配置的统一管理员用户名
        default_admin_password: 部署服务时自动配置的统一管理员密码（空则每次随机）
        default_email_domain: 需要邮箱登录的应用使用的邮箱后缀（不含 @）
        auto_update_enabled: 是否开启自动更新（定时同步应用市场与自动化方案，仅授权版可用）
        docker_registry_mirrors: Docker 镜像加速地址列表，按优先级排序
        hosts_optimizer_enabled: 是否启用 Hosts 优选
        hosts_optimizer_mode: 运行模式，manual（仅手动）/ auto（定时）
        hosts_optimizer_interval_minutes: 自动模式下的运行间隔（分钟）
        hosts_optimizer_top_n: 每个域名保留的优选 IP 数量
        hosts_optimizer_domains: 参与优选的域名列表
        hosts_optimizer_adguard_url: AdGuard Home 管理地址
        hosts_optimizer_adguard_username: AdGuard Home 管理员用户名
        hosts_optimizer_adguard_password: AdGuard Home 管理员密码
        hosts_optimizer_status: 最近一次优选结果（只由服务端维护）
        created_at: 记录创建时间
        updated_at: 记录最后更新时间
    """

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    http_proxy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    https_proxy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    no_proxy: Mapped[str | None] = mapped_column(String(500), nullable=True)
    service_port_start: Mapped[int] = mapped_column(
        Integer, default=10000, server_default="10000", nullable=False
    )
    default_admin_username: Mapped[str] = mapped_column(
        String(255), default="admin", server_default="admin", nullable=False
    )
    default_admin_password: Mapped[str] = mapped_column(
        String(255), default="", server_default="", nullable=False
    )
    default_email_domain: Mapped[str] = mapped_column(
        String(255), default="", server_default="", nullable=False
    )
    auto_update_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    docker_registry_mirrors: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    hosts_optimizer_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    hosts_optimizer_mode: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual", nullable=False
    )
    hosts_optimizer_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=360, server_default="360", nullable=False
    )
    hosts_optimizer_top_n: Mapped[int] = mapped_column(
        Integer, default=2, server_default="2", nullable=False
    )
    hosts_optimizer_domains: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    hosts_optimizer_adguard_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    hosts_optimizer_adguard_username: Mapped[str] = mapped_column(
        String(255), default="admin", server_default="admin", nullable=False
    )
    hosts_optimizer_adguard_password: Mapped[str] = mapped_column(
        String(255), default="", server_default="", nullable=False
    )
    hosts_optimizer_status: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
