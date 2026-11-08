"""增加 Google Drive 挂载目录后处理任务表。

Revision ID: c7e4f1a2b9d0
Revises: a8c4e2f6b1d9
"""

from alembic import op
import sqlalchemy as sa


revision = "c7e4f1a2b9d0"
down_revision = "a8c4e2f6b1d9"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    """检查数据库中是否已存在指定表。"""
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    """检查数据库中是否已存在指定索引。"""
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names() and any(
        item["name"] == index_name for item in inspector.get_indexes(table_name)
    )


def upgrade() -> None:
    """创建 Google Drive 后处理任务表及查询索引。"""
    if not _table_exists("gdrivetask"):
        op.create_table(
            "gdrivetask",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("src_path", sa.Text(), nullable=False),
            sa.Column("archive_path", sa.Text(), nullable=True),
            sa.Column("strm_path", sa.Text(), nullable=True),
            sa.Column("source_size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("uploaded_size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("stage", sa.String(), nullable=False, server_default="pending"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("remove_source", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("media_type", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("transfer_history_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in (
        ("ix_gdrivetask_src_status", ["src_path", "status"]),
        ("ix_gdrivetask_status_updated", ["status", "updated_at"]),
        ("ix_gdrivetask_status", ["status"]),
        ("ix_gdrivetask_transfer_history_id", ["transfer_history_id"]),
    ):
        if not _index_exists("gdrivetask", index_name):
            op.create_index(index_name, "gdrivetask", columns)


def downgrade() -> None:
    """移除 Google Drive 后处理任务表。"""
    if _table_exists("gdrivetask"):
        op.drop_table("gdrivetask")
