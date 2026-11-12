"""drop local acceleration tables

Revision ID: d7e8f9a0b1c2
Revises: c9e1f2a3b4d5
Create Date: 2026-11-11 18:00:00.000000

网络加速改为管理端下发订阅后，客户端不再本地维护节点、订阅与规则，
清理这些已无代码引用的表。历史数据不保留，如需回滚请从备份恢复。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c9e1f2a3b4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = (
    "accel_audit_logs",
    "accel_clients",
    "accel_node_configs",
    "accel_subscription_nodes",
    "accel_subscriptions",
    "accel_rulesets",
    "accel_rules",
    "accel_nodes",
)


def upgrade() -> None:
    """按依赖顺序删除客户端本地的加速表。"""
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    """表结构不再保留；需要恢复请使用备份。"""
    pass
