"""应用集成配置模型。

每个集成类型只保留一条记录，通过 ``kind`` 区分：
- vaultwarden：认证集成，保存连接信息与同步结果映射
- network：网络集成，保存反代方案（Lucky / Nginx Proxy Manager）、
  域名映射与 DDNS 配置
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String, func, text
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntegrationConfig(Base):
    """应用集成配置。

    Attributes:
        id: 主键
        kind: 集成类型（vaultwarden / network）
        enabled: 是否启用该集成
        config: 集成配置，包含连接凭据、域名映射、DDNS 设置等
        status: 最近一次连接测试或同步结果，只由服务端维护
        created_at: 创建时间
        updated_at: 最后更新时间
    """

    __tablename__ = "integration_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    config: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    status: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
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
