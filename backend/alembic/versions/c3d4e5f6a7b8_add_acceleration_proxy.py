"""add acceleration proxy tables

Revision ID: c3d4e5f6a7b8
Revises: b6c7d8e9f0a1
Create Date: 2026-11-11 10:00:00.000000

新增 VLESS Reality 代理加速能力：节点、订阅、规则版本、客户端状态、
节点配置版本与操作记录，并写入一批默认域名规则。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_RULES = [
    ("domain-suffix", "bangumi.tv", "Bangumi 番剧"),
    ("domain-suffix", "bgm.tv", "Bangumi 主站与 API"),
    ("domain-suffix", "musicbrainz.org", "MusicBrainz"),
    ("domain-suffix", "coverartarchive.org", "MusicBrainz 封面"),
    ("domain", "api.themoviedb.org", "TMDB API"),
    ("domain", "image.tmdb.org", "TMDB 图片"),
    ("domain-suffix", "github.com", "GitHub"),
    ("domain", "api.github.com", "GitHub API"),
    ("domain", "raw.githubusercontent.com", "GitHub Raw"),
    ("domain", "objects.githubusercontent.com", "GitHub 资源"),
    ("domain", "codeload.github.com", "GitHub 打包下载"),
    ("domain-suffix", "archive.org", "archive.org"),
]


def upgrade() -> None:
    """创建加速相关表并写入默认规则。"""
    op.create_table(
        "accel_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("vless_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("api_port", sa.Integer(), nullable=False, server_default="8787"),
        sa.Column("api_scheme", sa.String(length=8), nullable=False, server_default="http"),
        sa.Column(
            "sni", sa.String(length=255), nullable=False, server_default="www.microsoft.com"
        ),
        sa.Column(
            "reality_public_key", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column("short_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "fingerprint", sa.String(length=32), nullable=False, server_default="chrome"
        ),
        sa.Column(
            "flow", sa.String(length=32), nullable=False, server_default="xtls-rprx-vision"
        ),
        sa.Column("agent_token", sa.String(length=128), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accel_rulesets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("rules", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accel_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="domain-suffix",
        ),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accel_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(), nullable=True),
        sa.Column("last_pull_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("pull_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accel_subscription_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("accel_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("accel_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "subscription_id", "node_id", name="uq_accel_subscription_node"
        ),
    )

    op.create_table(
        "accel_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("accel_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="nas"),
        sa.Column("pulled_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column(
            "last_sync_status", sa.String(length=16), nullable=False, server_default="unknown"
        ),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column(
            "moviepilot_status",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("moviepilot_detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "accel_node_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("accel_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("note", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("node_id", "revision", name="uq_accel_node_revision"),
    )

    op.create_table(
        "accel_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    rules_table = sa.table(
        "accel_rules",
        sa.column("kind", sa.String),
        sa.column("value", sa.String),
        sa.column("note", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        rules_table,
        [
            {
                "kind": kind,
                "value": value,
                "note": note,
                "sort_order": index * 10,
            }
            for index, (kind, value, note) in enumerate(DEFAULT_RULES)
        ],
    )


def downgrade() -> None:
    """删除加速相关表。"""
    op.drop_table("accel_audit_logs")
    op.drop_table("accel_node_configs")
    op.drop_table("accel_clients")
    op.drop_table("accel_subscription_nodes")
    op.drop_table("accel_subscriptions")
    op.drop_table("accel_rules")
    op.drop_table("accel_rulesets")
    op.drop_table("accel_nodes")
