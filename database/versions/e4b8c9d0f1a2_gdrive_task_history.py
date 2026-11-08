"""增加 Google Drive 任务状态历史表。

Revision ID: e4b8c9d0f1a2
Revises: d91b7e42a6c3
"""

from alembic import op
import sqlalchemy as sa


revision = "e4b8c9d0f1a2"
down_revision = "d91b7e42a6c3"
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
    """创建 Google Drive 任务状态历史表及查询索引。"""
    if not _table_exists("gdrivetaskhistory"):
        op.create_table(
            "gdrivetaskhistory",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("from_status", sa.String(), nullable=True),
            sa.Column("to_status", sa.String(), nullable=False),
            sa.Column("from_stage", sa.String(), nullable=True),
            sa.Column("to_stage", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False, server_default="state_change"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in (
        ("ix_gdrivetaskhistory_task_id", ["task_id"]),
        ("ix_gdrivetaskhistory_task_created", ["task_id", "created_at"]),
    ):
        if not _index_exists("gdrivetaskhistory", index_name):
            op.create_index(index_name, "gdrivetaskhistory", columns)


def downgrade() -> None:
    """移除 Google Drive 任务状态历史表。"""
    if _table_exists("gdrivetaskhistory"):
        op.drop_table("gdrivetaskhistory")
