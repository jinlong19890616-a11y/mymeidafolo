from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, JSON, String, Text, or_

from app.db import Base, db_query, db_update, get_id_column
from app.db.models.gdrive_task_history import GdriveTaskHistory


class GdriveTask(Base):
    """
    Google Drive 挂载目录后处理任务。

    任务保存源文件、最终归档文件和 STRM 路径，进程重启后可根据阶段继续处理，
    不依赖内存队列中的临时对象。
    """

    id = get_id_column()
    src_path = Column(Text, nullable=False)
    archive_path = Column(Text)
    strm_path = Column(Text)
    strm_root = Column(Text)
    archive_root = Column(Text)
    backend = Column(String, nullable=False, default="mount", server_default="mount")
    upload_target = Column(JSON)
    upload_account_id = Column(String)
    remote_file_id = Column(String)
    source_size = Column(BigInteger, nullable=False, default=0)
    source_device = Column(BigInteger)
    source_inode = Column(BigInteger)
    source_sha256 = Column(String)
    uploaded_size = Column(BigInteger, nullable=False, default=0)
    status = Column(String, nullable=False, default="pending", index=True)
    stage = Column(String, nullable=False, default="pending")
    progress = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, default=0)
    remove_source = Column(Boolean, nullable=False, default=True)
    media_type = Column(String)
    title = Column(String)
    transfer_history_id = Column(Integer, index=True)
    dedupe_key = Column(String, unique=True)
    lease_owner = Column(String)
    lease_until = Column(String)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_gdrivetask_src_status", "src_path", "status"),
        Index("ix_gdrivetask_status_updated", "status", "updated_at"),
    )

    @classmethod
    @db_update
    def create_task(cls, db, **kwargs: object) -> int:
        """创建一个持久化的 Google Drive 后处理任务并返回其 ID。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = {"created_at": now, "updated_at": now, **kwargs}
        task = cls(**values)
        db.add(task)
        db.flush()
        db.add(GdriveTaskHistory(
            task_id=task.id,
            to_status=task.status,
            to_stage=task.stage,
            event_type="created",
            error_message=task.error_message,
            retry_count=task.retry_count or 0,
            created_at=now,
        ))
        return task.id

    @classmethod
    @db_query
    def get_task(cls, db, task_id: int) -> Optional["GdriveTask"]:
        """按任务 ID 获取 Google Drive 后处理任务。"""
        return db.query(cls).filter(cls.id == task_id).first()

    @classmethod
    @db_query
    def list_tasks(cls, db, status: Optional[str] = None, limit: int = 100) -> list["GdriveTask"]:
        """按状态查询最近的 Google Drive 后处理任务。"""
        query = db.query(cls)
        if status:
            query = query.filter(cls.status == status)
        return query.order_by(cls.updated_at.desc(), cls.id.desc()).limit(limit).all()

    @classmethod
    @db_query
    def find_active_by_source(cls, db, src_path: str) -> Optional["GdriveTask"]:
        """查询源文件已有的未完成任务，避免整理事件重复入队。"""
        return db.query(cls).filter(
            cls.src_path == src_path,
            cls.status.in_(("pending", "uploading", "archiving", "generating_strm")),
        ).order_by(cls.id.desc()).first()

    @classmethod
    @db_query
    def list_unfinished(cls, db) -> list["GdriveTask"]:
        """查询所有可自动恢复的任务，不受管理列表分页限制。"""
        return db.query(cls).filter(
            cls.status.in_(("pending", "uploading", "archiving", "generating_strm"))
        ).order_by(cls.id).all()

    @classmethod
    @db_update
    def claim_task(cls, db, task_id: int, owner: str, now: str, lease_until: str) -> bool:
        """通过数据库条件更新取得任务租约，避免多个进程同时处理同一任务。"""
        result = db.query(cls).filter(
            cls.id == task_id,
            cls.status.in_(("pending", "uploading", "archiving", "generating_strm")),
            or_(cls.lease_until.is_(None), cls.lease_until < now, cls.lease_owner == owner),
        ).update({cls.lease_owner: owner, cls.lease_until: lease_until}, synchronize_session=False)
        return bool(result)

    @classmethod
    @db_update
    def release_task(cls, db, task_id: int, owner: str) -> bool:
        """只释放当前工作进程持有的租约。"""
        result = db.query(cls).filter(cls.id == task_id, cls.lease_owner == owner).update(
            {cls.lease_owner: None, cls.lease_until: None}, synchronize_session=False
        )
        return bool(result)

    @classmethod
    @db_query
    def owns_task(cls, db, task_id: int, owner: str) -> bool:
        """确认任务租约仍归指定工作进程持有。"""
        return db.query(cls.id).filter(cls.id == task_id, cls.lease_owner == owner).first() is not None

    @classmethod
    @db_update
    def update_task(cls, db, task_id: int, **kwargs: object) -> bool:
        """更新任务阶段、进度和错误信息。"""
        task = db.query(cls).filter(cls.id == task_id).first()
        if not task:
            return False
        kwargs["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for key, value in kwargs.items():
            setattr(task, key, value)
        return True

    @classmethod
    @db_update
    def update_task_with_history(cls, db, task_id: int, event_type: str = "state_change",
                                 detail: Optional[str] = None, **kwargs: object) -> bool:
        """更新任务并在状态或阶段变化时写入状态记录。"""
        task = db.query(cls).filter(cls.id == task_id).first()
        if not task:
            return False
        old_status = task.status
        old_stage = task.stage
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kwargs["updated_at"] = now
        for key, value in kwargs.items():
            setattr(task, key, value)
        if task.status != old_status or task.stage != old_stage or event_type != "state_change":
            db.add(GdriveTaskHistory(
                task_id=task.id,
                from_status=old_status,
                to_status=task.status,
                from_stage=old_stage,
                to_stage=task.stage,
                event_type=event_type,
                error_message=task.error_message,
                retry_count=task.retry_count or 0,
                detail=detail,
                created_at=now,
            ))
        return True

    @classmethod
    @db_update
    def release_expired_leases(cls, db, now: str) -> int:
        """释放已过期的未完成任务租约，允许服务重启后重新领取。"""
        return db.query(cls).filter(
            cls.status.in_(("pending", "uploading", "archiving", "generating_strm")),
            cls.lease_until.is_not(None),
            cls.lease_until < now,
        ).update({cls.lease_owner: None, cls.lease_until: None}, synchronize_session=False)
