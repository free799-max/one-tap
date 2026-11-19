"""move notification event types to global settings

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-15 00:00:00.000000

通知事件开关改为全局配置：渠道只负责投递，不再逐个渠道选事件。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """全局增加事件白名单，删除渠道级事件字段。"""
    op.add_column(
        "notification_settings",
        sa.Column(
            "event_types",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.drop_column("notification_channels", "event_types")


def downgrade() -> None:
    """恢复渠道级事件字段，删除全局事件白名单。"""
    op.add_column(
        "notification_channels",
        sa.Column(
            "event_types",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.drop_column("notification_settings", "event_types")
