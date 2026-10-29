"""add reader auto sync books

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-24 14:00:00.000000

Reader 书架自动导出不再依赖改造镜像，改由 OneTap 后端定时执行。
本表记录每本书的导出状态、章节数与失败重试次数。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 Reader 自动同步书籍状态表。"""
    op.create_table(
        "reader_auto_sync_books",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("book_url", sa.String(length=2048), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("destination", sa.String(length=2048), nullable=True),
        sa.Column(
            "exported_total", sa.Integer(), nullable=False, server_default="-1"
        ),
        sa.Column(
            "total_chapters", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exported_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "group_id", "book_url", name="uq_reader_auto_book_group_url"
        ),
    )
    op.create_index(
        "ix_reader_auto_sync_books_group_id",
        "reader_auto_sync_books",
        ["group_id"],
    )
    op.create_index(
        "ix_reader_auto_book_group_status",
        "reader_auto_sync_books",
        ["group_id", "status"],
    )


def downgrade() -> None:
    """删除 Reader 自动同步书籍状态表。"""
    op.drop_index(
        "ix_reader_auto_book_group_status",
        table_name="reader_auto_sync_books",
    )
    op.drop_index(
        "ix_reader_auto_sync_books_group_id",
        table_name="reader_auto_sync_books",
    )
    op.drop_table("reader_auto_sync_books")
