"""通知中心与资源订阅台账模型。

OneTap 独立维护通知事件、站内收件箱和外部渠道投递记录；
``resource_subscriptions`` 记录订阅/下载请求的本地投影，
用于关联事件与收件人，不替代 MoviePilot / Suwayomi / Reader 的权威状态。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NotificationChannel(Base):
    """外部通知渠道配置。"""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    # feishu / dingtalk_app / wechatclawbot / qqbot
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    config: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    # Fernet 加密后的敏感字段 JSON，例如 {"app_secret": "..."}
    secrets: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class NotificationSetting(Base):
    """全局通知设置，单行记录。"""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 对外推送的事件白名单；空列表表示全部事件都推送（站内收件箱不受影响）
    event_types: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    # AI 通知方式：chat（只由 Agent 在对话里回复）/ push（外发到通知渠道）；
    # 为空表示还没问过，沿用外发（历史行为）
    notify_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 确认该方式时的技能版本，技能版本变化后重新询问
    notify_skill_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResourceSubscription(Base):
    """资源订阅/下载请求台账。

    ``source_of_truth`` 为 upstream 时是上游订阅的只读投影；
    为 local 时（音乐）表示 OneTap 自己保存的一次性下载请求。
    """

    __tablename__ = "resource_subscriptions"
    __table_args__ = (
        UniqueConstraint("domain", "subject_key", name="uq_resource_sub_domain_key"),
        Index("ix_resource_sub_domain_status", "domain", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # subscription / request
    kind: Mapped[str] = mapped_column(
        String(16), default="subscription", server_default="subscription", nullable=False
    )
    # upstream / local
    source_of_truth: Mapped[str] = mapped_column(
        String(16), default="upstream", server_default="upstream", nullable=False
    )
    upstream_ref: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(500), default="", server_default="", nullable=False
    )
    year: Mapped[str | None] = mapped_column(String(8), nullable=True)
    poster: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active / downloading / organizing / completed / failed / removed
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False
    )
    revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    notify_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    last_seen: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # mcp / web / sync
    origin: Mapped[str] = mapped_column(
        String(16), default="mcp", server_default="mcp", nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Notification(Base):
    """通知事件与站内收件箱记录。"""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_type_created", "type", "created_at"),
        Index("ix_notifications_read_at", "read_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(16), default="normal", server_default="normal", nullable=False
    )
    domain: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resource_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_proxy_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    group_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )


class NotificationDelivery(Base):
    """单个渠道的投递记录，支持独立重试。"""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel_id", name="uq_notification_delivery"
        ),
        Index("ix_notification_delivery_status", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="SET NULL"), nullable=True
    )
    # pending / sending / sent / failed / skipped
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class NotificationSyncState(Base):
    """同步游标，例如 MoviePilot 历史记录的水位。"""

    __tablename__ = "notification_sync_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
