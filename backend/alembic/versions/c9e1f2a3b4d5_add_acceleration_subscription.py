"""add acceleration subscription to system config

Revision ID: c9e1f2a3b4d5
Revises: c3d4e5f6a7b8
Create Date: 2026-11-11 16:00:00.000000

网络加速改为管理端下发订阅：客户端只保存订阅地址、规则快照与同步时间，
节点与规则不再在本地维护。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c9e1f2a3b4d5"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "acceleration_subscription_url",
                sa.String(length=500),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column(
                "acceleration_managed_rules",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column("acceleration_rules_synced_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("acceleration_rules_synced_at")
        batch_op.drop_column("acceleration_managed_rules")
        batch_op.drop_column("acceleration_subscription_url")
