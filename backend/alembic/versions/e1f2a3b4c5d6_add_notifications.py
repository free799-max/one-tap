"""add notification center and resource subscription ledger

Revision ID: e1f2a3b4c5d6
Revises: c9d0e1f2a3b4
Create Date: 2026-11-15 00:00:00.000000

新增 OneTap 独立通知中心：渠道配置、事件/站内收件箱、投递重试、
资源订阅台账与同步游标。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建通知与资源订阅相关表。"""
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "event_types",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("secrets", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "quiet_start", sa.String(length=5), nullable=False, server_default="23:00"
        ),
        sa.Column(
            "quiet_end", sa.String(length=5), nullable=False, server_default="08:00"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "resource_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=512), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="subscription"
        ),
        sa.Column(
            "source_of_truth",
            sa.String(length=16),
            nullable=False,
            server_default="upstream",
        ),
        sa.Column(
            "upstream_ref", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "title", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column("year", sa.String(length=8), nullable=True),
        sa.Column("poster", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "notify_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_seen", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="mcp"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "domain", "subject_key", name="uq_resource_sub_domain_key"
        ),
    )
    op.create_index(
        "ix_resource_sub_domain_status",
        "resource_subscriptions",
        ["domain", "status"],
    )
    op.create_index(
        "ix_resource_subscriptions_created_by",
        "resource_subscriptions",
        ["created_by"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "severity", sa.String(length=16), nullable=False, server_default="normal"
        ),
        sa.Column("domain", sa.String(length=16), nullable=True),
        sa.Column("subject_key", sa.String(length=512), nullable=True),
        sa.Column(
            "resource_subscription_id",
            sa.Integer(),
            sa.ForeignKey("resource_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False, unique=True),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("image_proxy_path", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("group_key", sa.String(length=256), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_notifications_type_created", "notifications", ["type", "created_at"]
    )
    op.create_index("ix_notifications_read_at", "notifications", ["read_at"])
    op.create_index(
        "ix_notifications_resource_subscription_id",
        "notifications",
        ["resource_subscription_id"],
    )
    op.create_index(
        "ix_notifications_target_user_id", "notifications", ["target_user_id"]
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Integer(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("notification_channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "notification_id", "channel_id", name="uq_notification_delivery"
        ),
    )
    op.create_index(
        "ix_notification_delivery_status",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "notification_sync_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """删除通知与资源订阅相关表。"""
    op.drop_table("notification_sync_states")
    op.drop_index(
        "ix_notification_delivery_status",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notifications_target_user_id", table_name="notifications")
    op.drop_index(
        "ix_notifications_resource_subscription_id", table_name="notifications"
    )
    op.drop_index("ix_notifications_read_at", table_name="notifications")
    op.drop_index("ix_notifications_type_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(
        "ix_resource_subscriptions_created_by", table_name="resource_subscriptions"
    )
    op.drop_index(
        "ix_resource_sub_domain_status", table_name="resource_subscriptions"
    )
    op.drop_table("resource_subscriptions")
    op.drop_table("notification_settings")
    op.drop_table("notification_channels")
