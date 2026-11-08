from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, Index, String, Text

from app.db import Base, db_query, db_update, get_id_column


class GdriveTaskHistory(Base):
    """
    Google Drive 任务状态变化记录。

    该表保存状态和阶段的每次持久化变化，使失败原因、重试和重启恢复过程可追溯。
    """

    id = get_id_column()
    task_id = Column(Integer, nullable=False, index=True)
    from_status = Column(String)
    to_status = Column(String, nullable=False)
    from_stage = Column(String)
    to_stage = Column(String, nullable=False)
    event_type = Column(String, nullable=False, default="state_change")
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    detail = Column(Text)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_gdrivetaskhistory_task_created", "task_id", "created_at"),
    )

    @classmethod
    @db_update
    def create_record(cls, db, **kwargs: object) -> int:
        """创建 Google Drive 任务状态记录并返回记录 ID。"""
        values = {"created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **kwargs}
        record = cls(**values)
        db.add(record)
        db.flush()
        return record.id

    @classmethod
    @db_query
    def list_by_task(cls, db, task_id: int, limit: int = 200) -> list["GdriveTaskHistory"]:
        """按时间顺序查询指定任务的状态记录。"""
        return db.query(cls).filter(cls.task_id == task_id).order_by(
            cls.created_at.asc(), cls.id.asc()
        ).limit(limit).all()
