from typing import List, Optional

from app.db import DbOper
from app.db.models.gdrive_task import GdriveTask


class GdriveTaskOper(DbOper):
    """
    Google Drive 后处理任务的数据访问封装。
    """

    def add(self, **kwargs: object) -> GdriveTask:
        """新增任务并返回任务实体。"""
        task_id = GdriveTask.create_task(self._db, **kwargs)
        return self.get(task_id)

    def get(self, task_id: int) -> Optional[GdriveTask]:
        """按 ID 获取任务。"""
        return GdriveTask.get_task(self._db, task_id)

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[GdriveTask]:
        """查询任务列表。"""
        return GdriveTask.list_tasks(self._db, status=status, limit=limit)

    def get_active_by_source(self, src_path: str) -> Optional[GdriveTask]:
        """查询源文件已有的活动任务。"""
        return GdriveTask.find_active_by_source(self._db, src_path)

    def list_unfinished(self) -> List[GdriveTask]:
        """查询所有需要启动恢复的任务。"""
        return GdriveTask.list_unfinished(self._db)

    def claim(self, task_id: int, owner: str, now: str, lease_until: str) -> bool:
        """原子取得任务处理权。"""
        return GdriveTask.claim_task(self._db, task_id, owner, now, lease_until)

    def release(self, task_id: int, owner: str) -> bool:
        """释放指定工作进程持有的任务处理权。"""
        return GdriveTask.release_task(self._db, task_id, owner)

    def owns(self, task_id: int, owner: str) -> bool:
        """确认指定工作进程仍持有任务处理权。"""
        return GdriveTask.owns_task(self._db, task_id, owner)

    def update(self, task_id: int, **kwargs: object) -> bool:
        """更新任务字段。"""
        return GdriveTask.update_task(self._db, task_id, **kwargs)

    def update_with_history(self, task_id: int, event_type: str = "state_change",
                            detail: str = None, **kwargs: object) -> bool:
        """更新任务字段并记录状态变化。"""
        return GdriveTask.update_task_with_history(
            self._db, task_id, event_type=event_type, detail=detail, **kwargs
        )

    def release_expired_leases(self, now: str) -> int:
        """释放已过期的未完成任务租约。"""
        return GdriveTask.release_expired_leases(self._db, now)
