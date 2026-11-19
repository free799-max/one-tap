"""drop notification quiet hours

Revision ID: b2c3d4e5f6a7
Revises: f2a3b4c5d6e7
Create Date: 2026-11-15 00:00:00.000000

通知不再限制发送时段，删除静默时段的全局配置。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除静默时段相关列。"""
    op.drop_column("notification_settings", "quiet_hours_enabled")
    op.drop_column("notification_settings", "quiet_start")
    op.drop_column("notification_settings", "quiet_end")


def downgrade() -> None:
    """恢复静默时段相关列。"""
    op.add_column(
        "notification_settings",
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "quiet_start", sa.String(length=5), nullable=False, server_default="23:00"
        ),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "quiet_end", sa.String(length=5), nullable=False, server_default="08:00"
        ),
    )
