from sqlalchemy import Boolean, Column, Integer, JSON, String

from app.db import Base, get_id_column


class GdriveDirectory(Base):
    """长期保存的目录归档任务，配置与文件执行记录分开存储"""

    id = get_id_column()
    config = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    next_run_at = Column(String, index=True)
    last_scan_at = Column(String)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
