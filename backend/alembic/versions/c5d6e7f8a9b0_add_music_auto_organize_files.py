"""add music auto organize files

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-24 12:00:00.000000

音乐自动化在 OneTap 内直接调用 Music Tag Web 完成刮削与整理，
本表记录每个源文件的处理状态，供自动调度去重、失败退避与界面展示。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建音乐自动整理文件状态表。"""
    op.create_table(
        "music_auto_organize_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mtime", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "group_id", "path", name="uq_music_auto_file_group_path"
        ),
    )
    op.create_index(
        "ix_music_auto_organize_files_group_id",
        "music_auto_organize_files",
        ["group_id"],
    )
    op.create_index(
        "ix_music_auto_file_group_status",
        "music_auto_organize_files",
        ["group_id", "status"],
    )


def downgrade() -> None:
    """删除音乐自动整理文件状态表。"""
    op.drop_index(
        "ix_music_auto_file_group_status",
        table_name="music_auto_organize_files",
    )
    op.drop_index(
        "ix_music_auto_organize_files_group_id",
        table_name="music_auto_organize_files",
    )
    op.drop_table("music_auto_organize_files")
