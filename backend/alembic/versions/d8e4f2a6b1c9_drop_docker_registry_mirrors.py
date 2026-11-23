"""drop docker registry mirrors

Revision ID: d8e4f2a6b1c9
Revises: a6b8c9d0e1f2
Create Date: 2026-11-21 00:00:00.000000

移除镜像加速功能后，系统配置不再保存加速地址列表。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d8e4f2a6b1c9"
down_revision: Union[str, None] = "a6b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("docker_registry_mirrors")


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "docker_registry_mirrors",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )
