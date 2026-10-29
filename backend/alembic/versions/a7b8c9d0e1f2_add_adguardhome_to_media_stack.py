"""add adguardhome to media-stack composition

Revision ID: a7b8c9d0e1f2
Revises: f9c1d2e3a4b5
Create Date: 2026-10-24 10:00:00.000000

内置 media-stack（影视自动化）与从管理端同步的 movie-auto 是同一套方案
的历史副本。管理端方案 1.1.0 起包含可选的 AdGuard Home，这里同步补齐，
避免页面上的两个「movie-auto」模板内容不一致。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f9c1d2e3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORCHESTRATION_NAME = "media-stack"
_PREVIOUS_VERSION = "1.0.3"
_NEW_VERSION = "1.1.0"
_ADGUARD_ENTRY = {
    "app_name": "adguardhome",
    "relation": "optional",
    "group": "网络优化",
    # 固定到上游稳定版，镜像 tag 与 GitHub release tag 一致（v0.107.x）
    "image": "adguard/adguardhome",
    "tag": "v0.107.79",
}
# 与管理端 movie 方案保持一致，各组件固定稳定版本
_STABLE_PINS = {
    "moviepilot": ("jxxghp/moviepilot-v3", "v3.1.9"),
    "qbittorrent": ("linuxserver/qbittorrent", "5.3.0_v2.1.2-ls484"),
    "jellyfin": ("jellyfin/jellyfin", "12.3"),
    "emby": ("emby/embyserver", "4.10.2.0"),
}
_CHANGELOG = (
    "新增可选组件 AdGuard Home（网络优化），各组件固定稳定版本："
    "MoviePilot v3.1.9、qBittorrent 5.3.0、Jellyfin 12.3、Emby 4.10.2.0、"
    "AdGuard Home v0.107.79。部署后可在 AdGuard 的 DNS 重写中维护 "
    "TMDB、PT 站点等域名解析，OneTap「系统设置 → Hosts 优选」可手动或"
    "定时更新这些重写。"
)


def _fetchone(bind, stmt):
    """兼容 Alembic Connection 与裸 Engine 的查询。"""
    if isinstance(bind, sa.Engine):
        with bind.connect() as conn:
            row = conn.execute(stmt).fetchone()
            conn.commit()
            return row
    return bind.execute(stmt).fetchone()


def _execute(bind, stmt):
    """兼容 Alembic Connection 与裸 Engine 的 DML。"""
    if isinstance(bind, sa.Engine):
        with bind.connect() as conn:
            conn.execute(stmt)
            conn.commit()
    else:
        bind.execute(stmt)


def _orchestrations_table():
    return sa.table(
        "app_orchestrations",
        sa.column("name", sa.String),
        sa.column("app_composition", sa.JSON),
        sa.column("version", sa.String),
        sa.column("changelog", sa.Text),
    )


def upgrade() -> None:
    bind = op.get_bind()
    orchestrations = _orchestrations_table()

    row = _fetchone(
        bind,
        sa.select(orchestrations.c.app_composition).where(
            orchestrations.c.name == _ORCHESTRATION_NAME
        ),
    )
    composition = row[0] if row else None
    if not composition:
        return

    changed = False
    for item in composition:
        if not isinstance(item, dict):
            continue
        pin = _STABLE_PINS.get(item.get("app_name"))
        if pin is not None and (item.get("image"), item.get("tag")) != pin:
            item["image"], item["tag"] = pin
            changed = True

    if not any(
        isinstance(item, dict) and item.get("app_name") == "adguardhome"
        for item in composition
    ):
        insert_at = next(
            (
                index
                for index, item in enumerate(composition)
                if isinstance(item, dict) and item.get("relation") == "optional"
            ),
            len(composition),
        )
        composition.insert(insert_at, dict(_ADGUARD_ENTRY))
        changed = True

    if not changed:
        return

    _execute(
        bind,
        sa.update(orchestrations)
        .where(orchestrations.c.name == _ORCHESTRATION_NAME)
        .values(
            app_composition=composition,
            version=_NEW_VERSION,
            changelog=_CHANGELOG,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    orchestrations = _orchestrations_table()

    row = _fetchone(
        bind,
        sa.select(orchestrations.c.app_composition).where(
            orchestrations.c.name == _ORCHESTRATION_NAME
        ),
    )
    composition = row[0] if row else None
    if not composition:
        return

    remaining = [
        item
        for item in composition
        if not (isinstance(item, dict) and item.get("app_name") == "adguardhome")
    ]
    if len(remaining) == len(composition):
        return

    _execute(
        bind,
        sa.update(orchestrations)
        .where(orchestrations.c.name == _ORCHESTRATION_NAME)
        .values(
            app_composition=remaining,
            version=_PREVIOUS_VERSION,
            changelog=None,
        ),
    )
