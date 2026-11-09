"""add hosts acceleration preferences

Revision ID: e5f6a7b8c9d1
Revises: c2d3e4f5a6b7
Create Date: 2026-11-08 12:00:00.000000

记录应用级 DNS 加速偏好，供自动化方案修复/重新部署后自动恢复。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d1"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增加速偏好列表。"""
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "hosts_optimizer_accel_instances",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    """移除加速偏好列表。"""
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("hosts_optimizer_accel_instances")
