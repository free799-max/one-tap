"""应用商店 ORM 模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class App(Base):
    """应用商店中的应用模型。

    代表一个可单独部署的应用，如 Jellyfin、MoviePilot、Nginx 等。
    每个应用包含自身的元数据、配置表单、默认值和 Compose YAML 模板。
    """

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 官方网站链接
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 开源社区/源码仓库链接
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 支持的架构列表，如 ["amd64", "arm64"]
    architectures: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 默认镜像名，如 jellyfin/jellyfin
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 默认镜像元数据：体积（字节）、推送时间、摘要
    image_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 默认端口列表，如 [{"port": 8096, "protocol": "tcp", "description": "Web UI"}]
    default_ports: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # 前端动态表单 JSON Schema
    config_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    # 单应用 Compose YAML 模板（Jinja2）
    yaml_template: Mapped[str] = mapped_column(Text, nullable=False)
    # Markdown 格式说明文档
    readme: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    # 模板类型：compose 多容器 / container 单容器
    type: Mapped[str] = mapped_column(String(20), default="compose")
    # 版本变更日志
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 默认需要备份的路径列表（相对于宿主机或容器内）
    backup_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 应用是否含内置收费（付费软件或付费功能）
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    instances: Mapped[list["AppInstance"]] = relationship(
        "AppInstance",
        back_populates="app",
        cascade="all, delete-orphan",
    )
