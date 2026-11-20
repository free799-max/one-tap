"""remove dingtalk webhook channels

Revision ID: a6b8c9d0e1f2
Revises: e7a1c4b9d2f0
Create Date: 2026-11-19 00:00:00.000000

移除钉钉自定义机器人 Webhook 渠道，保留企业内部应用机器人。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a6b8c9d0e1f2"
down_revision: Union[str, None] = "e7a1c4b9d2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_channels WHERE type = 'dingtalk'"))


def downgrade() -> None:
    pass
