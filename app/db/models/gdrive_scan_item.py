from sqlalchemy import BigInteger, Column, Integer, String, Text

from app.db import Base, get_id_column


class GdriveScanItem(Base):
    """扫描结果及文件任务关联，用于展示过滤原因和执行历史"""

    id = get_id_column()
    scan_id = Column(Integer, nullable=False, index=True)
    source_path = Column(Text, nullable=False)
    archive_path = Column(Text)
    size = Column(BigInteger, nullable=False, default=0)
    fingerprint = Column(String)
    reason = Column(String, nullable=False)
    task_id = Column(Integer, index=True)
