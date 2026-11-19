"""add notification agent notify mode

Revision ID: e7a1c4b9d2f0
Revises: b2c3d4e5f6a7
Create Date: 2026-11-17 00:00:00.000000

AI 技能安装/更新时要先问用户「通知交给 Agent 在对话里回复，还是外发到通知渠道」，
把选择记在全局通知设置里，技能版本变化后重新询问。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7a1c4b9d2f0"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增 AI 通知方式与确认时的技能版本。"""
    op.add_column(
        "notification_settings",
        sa.Column("notify_mode", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "notification_settings",
        sa.Column("notify_skill_version", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    """移除 AI 通知方式。"""
    op.drop_column("notification_settings", "notify_skill_version")
    op.drop_column("notification_settings", "notify_mode")
