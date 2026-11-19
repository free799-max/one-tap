"""add encrypted value to MCP access tokens

Revision ID: c9d0e1f2a3b4
Revises: b7c9d2e4f6a1
Create Date: 2026-11-14 21:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c9d2e4f6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_access_tokens",
        sa.Column("token_value", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_access_tokens", "token_value")
