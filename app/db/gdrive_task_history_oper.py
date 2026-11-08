from app.db import DbOper
from app.db.models.gdrive_task_history import GdriveTaskHistory


class GdriveTaskHistoryOper(DbOper):
    """Google Drive 任务状态记录的数据访问封装。"""

    def list_by_task(self, task_id: int, limit: int = 200) -> list[GdriveTaskHistory]:
        """查询指定任务的状态记录。"""
        return GdriveTaskHistory.list_by_task(self._db, task_id, limit=limit)
