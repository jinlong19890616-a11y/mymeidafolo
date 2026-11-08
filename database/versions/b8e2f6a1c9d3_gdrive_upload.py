"""增加独立 API 上传任务快照，保留既有挂载任务

Revision ID: b8e2f6a1c9d3
Revises: a6d0e1f2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "b8e2f6a1c9d3"
down_revision = "a6d0e1f2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为旧任务保留挂载后端并新增上传目标和账号字段"""
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("gdrivetask")}
    for column in (
        sa.Column("backend", sa.String(), nullable=False, server_default="mount"),
        sa.Column("upload_target", sa.JSON(), nullable=True),
        sa.Column("upload_account_id", sa.String(), nullable=True),
        sa.Column("remote_file_id", sa.String(), nullable=True),
    ):
        if column.name not in columns:
            op.add_column("gdrivetask", column)


def downgrade() -> None:
    """撤销本次上传字段"""
    with op.batch_alter_table("gdrivetask") as batch:
        for name in ("remote_file_id", "upload_account_id", "upload_target", "backend"):
            batch.drop_column(name)
