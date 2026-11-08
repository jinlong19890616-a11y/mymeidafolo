from app.db import DbOper
from app.db.models.gdrive_task_history import GdriveTaskHistory


class GdriveTaskHistoryOper(DbOper):
    """Google Drive 任务状态记录的数据访问封装。"""

    def list_by_task(self, task_id: int, limit: int = 200) -> list[GdriveTaskHistory]:
        """查询指定任务的状态记录。"""
        return GdriveTaskHistory.list_by_task(self._db, task_id, limit=limit)

    def list_notification_events(self, start: str = None, end: str = None,
                                 pending: bool = False, limit: int = None) -> list[GdriveTaskHistory]:
        """读取阶段通知或时间窗口内的首次核验记录。"""
        return GdriveTaskHistory.list_notification_events(self._db, start, end, pending, limit)

    def mark_notified(self, event: GdriveTaskHistory) -> int:
        """仅在 Telegram 确认成功后持久化阶段通知回执。"""
        return GdriveTaskHistory.create_record(self._db, task_id=event.task_id,
            event_type="telegram_sent", to_status=event.to_status, to_stage=event.to_stage,
            detail=str(event.id))
