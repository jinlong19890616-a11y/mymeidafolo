"""增加 GDrive 任务跨进程租约字段。

Revision ID: d91b7e42a6c3
Revises: c7e4f1a2b9d0
"""

from alembic import op
import sqlalchemy as sa


revision = "d91b7e42a6c3"
down_revision = "c7e4f1a2b9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为任务增加租约所有者和超时字段。"""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("gdrivetask")}
    if "lease_owner" not in columns:
        op.add_column("gdrivetask", sa.Column("lease_owner", sa.String(), nullable=True))
    if "lease_until" not in columns:
        op.add_column("gdrivetask", sa.Column("lease_until", sa.String(), nullable=True))
    if "source_device" not in columns:
        op.add_column("gdrivetask", sa.Column("source_device", sa.BigInteger(), nullable=True))
    if "source_inode" not in columns:
        op.add_column("gdrivetask", sa.Column("source_inode", sa.BigInteger(), nullable=True))
    if "source_sha256" not in columns:
        op.add_column("gdrivetask", sa.Column("source_sha256", sa.String(), nullable=True))


def downgrade() -> None:
    """移除 GDrive 任务租约字段。"""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("gdrivetask")}
    if "lease_until" in columns:
        op.drop_column("gdrivetask", "lease_until")
    if "lease_owner" in columns:
        op.drop_column("gdrivetask", "lease_owner")
    if "source_inode" in columns:
        op.drop_column("gdrivetask", "source_inode")
    if "source_device" in columns:
        op.drop_column("gdrivetask", "source_device")
    if "source_sha256" in columns:
        op.drop_column("gdrivetask", "source_sha256")
