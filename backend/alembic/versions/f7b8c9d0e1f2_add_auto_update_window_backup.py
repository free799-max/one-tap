"""add auto update window and backup settings to system_config

Revision ID: f7b8c9d0e1f2
Revises: e5f6a7b8c9d1
Create Date: 2026-11-08 22:00:00.000000

自动更新只在夜间时段运行，并可在升级前自动备份应用数据目录。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7b8c9d0e1f2"
down_revision: Union[str, None] = "e5f6a7b8c9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_update_window_start",
                sa.Integer(),
                nullable=False,
                server_default="2",
            )
        )
        batch_op.add_column(
            sa.Column(
                "auto_update_window_end",
                sa.Integer(),
                nullable=False,
                server_default="6",
            )
        )
        batch_op.add_column(
            sa.Column(
                "auto_backup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "auto_backup_keep",
                sa.Integer(),
                nullable=False,
                server_default="3",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("auto_backup_keep")
        batch_op.drop_column("auto_backup_enabled")
        batch_op.drop_column("auto_update_window_end")
        batch_op.drop_column("auto_update_window_start")
