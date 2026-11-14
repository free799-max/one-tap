"""add MCP aggregation tokens and audit logs

Revision ID: b7c9d2e4f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-11-14 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7c9d2e4f6a1"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_access_tokens_prefix", "mcp_access_tokens", ["prefix"])
    op.create_index("ix_mcp_access_tokens_token_hash", "mcp_access_tokens", ["token_hash"], unique=True)
    op.create_index("ix_mcp_access_tokens_user_id", "mcp_access_tokens", ["user_id"])

    op.create_table(
        "mcp_tool_call_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("mcp_access_tokens.id"), nullable=True),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("upstream_instance_id", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("error_message", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mcp_tool_call_logs_user_id", "mcp_tool_call_logs", ["user_id"])
    op.create_index("ix_mcp_tool_call_logs_token_id", "mcp_tool_call_logs", ["token_id"])
    op.create_index("ix_mcp_tool_call_logs_created_at", "mcp_tool_call_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("mcp_tool_call_logs")
    op.drop_table("mcp_access_tokens")
