"""add hosts acceleration

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-08 10:00:00.000000

新增应用级 DNS 加速能力：应用实例记录加速开关，系统配置保存
OneTap 自动部署的 AdGuard Home 信息与从管理端同步的加速规则。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增应用加速与自动部署 AdGuard 所需字段。"""
    with op.batch_alter_table("app_instances") as batch_op:
        batch_op.add_column(
            sa.Column(
                "acceleration_enabled",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )

    with op.batch_alter_table("system_config") as batch_op:
        batch_op.add_column(
            sa.Column(
                "hosts_optimizer_adguard_managed",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("hosts_optimizer_adguard_dns_ip", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("hosts_optimizer_adguard_web_port", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("hosts_optimizer_adguard_setup_port", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "hosts_optimizer_managed_rules",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "hosts_optimizer_managed_rules_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    """移除应用加速与自动部署 AdGuard 字段。"""
    with op.batch_alter_table("system_config") as batch_op:
        batch_op.drop_column("hosts_optimizer_managed_rules_synced_at")
        batch_op.drop_column("hosts_optimizer_managed_rules")
        batch_op.drop_column("hosts_optimizer_adguard_setup_port")
        batch_op.drop_column("hosts_optimizer_adguard_web_port")
        batch_op.drop_column("hosts_optimizer_adguard_dns_ip")
        batch_op.drop_column("hosts_optimizer_adguard_managed")

    with op.batch_alter_table("app_instances") as batch_op:
        batch_op.drop_column("acceleration_enabled")
