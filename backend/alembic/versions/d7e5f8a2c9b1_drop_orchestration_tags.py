"""drop orchestration tags

方案标签已由管理端移除，OneTap 不再接收或展示，删除该列。

Revision ID: d7e5f8a2c9b1
Revises: b3e4f5a6c7d8
Create Date: 2026-10-23 11:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d7e5f8a2c9b1"
down_revision: Union[str, None] = "b3e4f5a6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("app_orchestrations") as batch_op:
        batch_op.drop_column("tags")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_orchestrations") as batch_op:
        batch_op.add_column(sa.Column("tags", sa.JSON(), nullable=True))
