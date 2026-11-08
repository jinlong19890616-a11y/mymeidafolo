from sqlalchemy import Boolean, Column, Integer, JSON, String, Text

from app.db import Base, get_id_column


class GdriveScan(Base):
    """目录扫描批次，活动键保证同一目录任务只能存在一个运行批次"""

    id = get_id_column()
    directory_id = Column(Integer, nullable=False, index=True)
    directory_name = Column(String, nullable=False)
    config = Column(JSON, nullable=False)
    trigger = Column(String, nullable=False)
    dry_run = Column(Boolean, nullable=False)
    status = Column(String, nullable=False)
    active_key = Column(Integer, unique=True)
    scanned = Column(Integer, nullable=False, default=0)
    matched = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    created_at = Column(String, nullable=False)
    finished_at = Column(String)
