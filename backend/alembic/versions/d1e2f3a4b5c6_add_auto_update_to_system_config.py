"""add auto_update_enabled to system_config

Revision ID: d1e2f3a4b5c6
Revises: c3e8f5a1b9d7
Create Date: 2026-10-29 23:00:00.000000

自动更新开关：开启后定时同步应用市场与自动化方案，仅授权版可用。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c3e8f5a1b9d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_config",
        sa.Column(
            "auto_update_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("auto_update_enabled")
