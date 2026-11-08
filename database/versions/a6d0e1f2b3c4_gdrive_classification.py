"""增加离线媒体快照和独立 STRM 输出映射

Revision ID: a6d0e1f2b3c4
Revises: f5c9d0e1a2b3
"""

from alembic import op
import sqlalchemy as sa


revision = "a6d0e1f2b3c4"
down_revision = "f5c9d0e1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留既有任务并增加输出根目录和媒体信息表"""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("gdrivetask")}
    for name in ("strm_root", "archive_root"):
        if name not in columns:
            op.add_column("gdrivetask", sa.Column(name, sa.Text(), nullable=True))
    if "gdrivemedia" not in inspector.get_table_names():
        op.create_table(
            "gdrivemedia", sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_path", sa.Text(), nullable=False, unique=True),
            sa.Column("signature", sa.String(), nullable=False),
            sa.Column("media", sa.JSON(), nullable=False),
        )


def downgrade() -> None:
    """仅撤销本次增加的数据结构"""
    op.drop_table("gdrivemedia")
    with op.batch_alter_table("gdrivetask") as batch:
        batch.drop_column("archive_root")
        batch.drop_column("strm_root")
