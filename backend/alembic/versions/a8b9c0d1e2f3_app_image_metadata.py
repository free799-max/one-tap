"""app image metadata

Revision ID: a8b9c0d1e2f3
Revises: d1e2f3a4b5c6
Create Date: 2026-10-29 16:05:00.000000

应用商城记录管理端同步来的默认镜像体积、推送时间与摘要。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.add_column(sa.Column("image_size", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("image_pushed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("image_digest", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.drop_column("image_digest")
        batch_op.drop_column("image_pushed_at")
        batch_op.drop_column("image_size")
