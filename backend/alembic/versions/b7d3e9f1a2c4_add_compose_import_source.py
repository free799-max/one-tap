"""add compose import source and app listing flag

Revision ID: b7d3e9f1a2c4
Revises: d8e4f2a6b1c9
Create Date: 2026-11-24 10:00:00.000000

Compose 项目增加来源标记，用于区分 OneTap 创建、外部导入与自动发现；
应用增加 is_listed，导入外部项目时生成的占位应用不在应用市场展示。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7d3e9f1a2c4"
down_revision: Union[str, None] = "d8e4f2a6b1c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "docker_compose_projects",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="managed",
        ),
    )
    op.add_column(
        "apps",
        sa.Column(
            "is_listed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("apps", "is_listed")
    op.drop_column("docker_compose_projects", "source")
