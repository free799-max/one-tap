"""add app proxy acceleration flag

Revision ID: f3a4b5c6d7e8
Revises: d7e8f9a0b1c2
Create Date: 2026-11-12 11:00:00.000000

应用实例增加 proxy_enabled，用于网络加速（Mihomo 代理）按应用接入。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_instances",
        sa.Column("proxy_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("app_instances", "proxy_enabled")
