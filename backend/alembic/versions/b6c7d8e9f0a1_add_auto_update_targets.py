"""add auto update targets to system_config

Revision ID: b6c7d8e9f0a1
Revises: f7b8c9d0e1f2
Create Date: 2026-11-08 23:30:00.000000

自动更新只处理用户明确勾选的服务，避免升级复杂应用时只替换主镜像。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "f7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_update_targets",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("auto_update_targets")
