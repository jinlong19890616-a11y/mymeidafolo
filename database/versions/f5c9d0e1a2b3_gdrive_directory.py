"""增加目录归档任务和扫描历史

Revision ID: f5c9d0e1a2b3
Revises: e4b8c9d0f1a2
"""

from alembic import op
import sqlalchemy as sa


revision = "f5c9d0e1a2b3"
down_revision = "e4b8c9d0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建目录任务、批次和文件结果表并保留原任务数据"""
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "gdrivedirectory" not in tables:
        op.create_table(
            "gdrivedirectory",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("next_run_at", sa.String()),
            sa.Column("last_scan_at", sa.String()),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
        )
        op.create_index("ix_gdrivedirectory_next_run_at", "gdrivedirectory", ["next_run_at"])
    if "gdrivescan" not in tables:
        op.create_table(
            "gdrivescan",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("directory_id", sa.Integer(), nullable=False),
            sa.Column("directory_name", sa.String(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("trigger", sa.String(), nullable=False),
            sa.Column("dry_run", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("active_key", sa.Integer(), unique=True),
            sa.Column("scanned", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("matched", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("finished_at", sa.String()),
        )
        op.create_index("ix_gdrivescan_directory_id", "gdrivescan", ["directory_id"])
    if "gdrivescanitem" not in tables:
        op.create_table(
            "gdrivescanitem",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scan_id", sa.Integer(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=False),
            sa.Column("archive_path", sa.Text()),
            sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("fingerprint", sa.String()),
            sa.Column("reason", sa.String(), nullable=False),
            sa.Column("task_id", sa.Integer()),
        )
        op.create_index("ix_gdrivescanitem_scan_id", "gdrivescanitem", ["scan_id"])
        op.create_index("ix_gdrivescanitem_task_id", "gdrivescanitem", ["task_id"])
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("gdrivetask")}
    if "dedupe_key" not in columns:
        op.add_column("gdrivetask", sa.Column("dedupe_key", sa.String()))
        op.create_index("ix_gdrivetask_dedupe_key", "gdrivetask", ["dedupe_key"], unique=True)


def downgrade() -> None:
    """移除目录任务与扫描表，不删除原文件任务记录"""
    with op.batch_alter_table("gdrivetask") as batch:
        batch.drop_index("ix_gdrivetask_dedupe_key")
        batch.drop_column("dedupe_key")
    for name in ("gdrivescanitem", "gdrivescan", "gdrivedirectory"):
        op.drop_table(name)
