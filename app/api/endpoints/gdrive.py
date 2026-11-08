from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query

from app import schemas
from app.chain.gdrive import GdriveChain
from app.db.models import User
from app.db.gdrive_task_history_oper import GdriveTaskHistoryOper
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.user_oper import get_current_active_manage_user, get_current_active_superuser


router = APIRouter()


def _task_info(task: Any) -> schemas.GdriveTaskInfo:
    """将数据库任务转换为接口响应模型。"""
    return schemas.GdriveTaskInfo(**task.to_dict())


@router.get("/settings", response_model=schemas.GdriveSettings)
def get_settings(_: User = Depends(get_current_active_manage_user)) -> schemas.GdriveSettings:
    """获取 Google Drive 挂载目录配置。"""
    return GdriveChain.get_settings()


@router.put("/settings", response_model=schemas.Response)
def update_settings(
    value: schemas.GdriveSettingsUpdate,
    _: User = Depends(get_current_active_superuser),
) -> schemas.Response:
    """更新 Google Drive 挂载目录配置。"""
    GdriveChain.save_settings(value)
    return schemas.Response(success=True, data=value.model_dump())


@router.post("/preflight", response_model=schemas.GdrivePreflightResult)
def preflight(
    value: schemas.GdrivePreflightRequest,
    _: User = Depends(get_current_active_manage_user),
) -> schemas.GdrivePreflightResult:
    """只读检查 Google Drive 后处理配置和归档路径。"""
    return GdriveChain.preflight(value.source_path, value.archive_path)


@router.get("/tasks", response_model=List[schemas.GdriveTaskInfo])
def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(get_current_active_manage_user),
) -> List[schemas.GdriveTaskInfo]:
    """查询 Google Drive 后处理任务列表。"""
    return [_task_info(task) for task in GdriveTaskOper().list(status=status, limit=limit)]


@router.get("/tasks/{task_id}/history", response_model=List[schemas.GdriveTaskHistoryInfo])
def list_task_history(
    task_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    _: User = Depends(get_current_active_manage_user),
) -> List[schemas.GdriveTaskHistoryInfo]:
    """查询指定 Google Drive 任务的状态变化历史。"""
    return [
        schemas.GdriveTaskHistoryInfo(**record.to_dict())
        for record in GdriveTaskHistoryOper().list_by_task(task_id, limit=limit)
    ]


@router.post("/tasks", response_model=schemas.Response)
def create_task(
    value: schemas.GdriveTaskCreate,
    _: User = Depends(get_current_active_manage_user),
) -> schemas.Response:
    """手动创建并启动 Google Drive 后处理任务。"""
    task_id, error = GdriveChain.create_task(
        source_path=value.source_path,
        archive_path=value.archive_path,
        remove_source=value.remove_source,
    )
    if error:
        return schemas.Response(success=False, message=error)
    return schemas.Response(success=True, data={"id": task_id})


@router.post("/tasks/{task_id}/retry", response_model=schemas.Response)
def retry_task(task_id: int, _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """重试失败或取消的 Google Drive 后处理任务。"""
    success, error = GdriveChain.retry_task(task_id)
    return schemas.Response(success=success, message=error)


@router.post("/tasks/{task_id}/cancel", response_model=schemas.Response)
def cancel_task(task_id: int, _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """取消 Google Drive 后处理任务。"""
    return schemas.Response(success=GdriveChain.cancel_task(task_id))


@router.post("/tasks/{task_id}/cleanup", response_model=schemas.GdriveTaskCleanupResult)
def cleanup_task(
    task_id: int,
    value: Optional[schemas.GdriveTaskCleanupRequest] = None,
    _: User = Depends(get_current_active_superuser),
) -> schemas.GdriveTaskCleanupResult:
    """预览或清理指定 Google Drive 任务的临时文件。"""
    request = value or schemas.GdriveTaskCleanupRequest()
    return GdriveChain.cleanup_task(task_id, dry_run=request.dry_run)
