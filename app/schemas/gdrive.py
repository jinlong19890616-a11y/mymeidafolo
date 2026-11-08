from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GdriveSettings(BaseModel):
    """Google Drive 挂载目录功能配置。"""

    enabled: bool = False
    mount_path: str = ""
    archive_path: str = ""
    strm_path_prefix: str = ""
    remove_source: bool = True
    version_suffix: str = Field(default="version", min_length=1, max_length=32)
    source_root: str = ""
    copy_chunk_size: int = Field(default=10 * 1024 * 1024, ge=1024 * 1024)


class GdriveSettingsUpdate(GdriveSettings):
    """Google Drive 挂载目录功能配置更新请求。"""


class GdriveTaskCreate(BaseModel):
    """手动创建 Google Drive 后处理任务请求。"""

    source_path: str = Field(..., min_length=1)
    archive_path: Optional[str] = None
    remove_source: Optional[bool] = None


class GdrivePreflightRequest(BaseModel):
    """Google Drive 后处理预检请求。"""

    source_path: str = Field(..., min_length=1)
    archive_path: Optional[str] = None


class GdrivePreflightCheck(BaseModel):
    """Google Drive 后处理预检项目。"""

    name: str
    passed: bool
    level: str
    message: str


class GdrivePreflightResult(BaseModel):
    """Google Drive 后处理预检结果。"""

    ready: bool
    source_path: str
    mount_path: str
    archive_path: Optional[str] = None
    strm_path: Optional[str] = None
    source_size: Optional[int] = None
    checks: list[GdrivePreflightCheck] = Field(default_factory=list)


class GdriveTaskCleanupRequest(BaseModel):
    """Google Drive 后处理任务清理请求。"""

    dry_run: bool = True


class GdriveTaskCleanupResult(BaseModel):
    """Google Drive 后处理任务清理结果。"""

    task_id: int
    status: Optional[str] = None
    dry_run: bool
    can_cleanup: bool
    message: str
    candidates: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    retained_paths: list[str] = Field(default_factory=list)


class GdriveTaskHistoryInfo(BaseModel):
    """Google Drive 后处理任务状态历史响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    from_status: Optional[str] = None
    to_status: str
    from_stage: Optional[str] = None
    to_stage: str
    event_type: str
    error_message: Optional[str] = None
    retry_count: int = 0
    detail: Optional[str] = None
    created_at: str


class GdriveTaskInfo(BaseModel):
    """Google Drive 后处理任务响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    src_path: str
    archive_path: Optional[str] = None
    strm_path: Optional[str] = None
    source_size: int = 0
    uploaded_size: int = 0
    status: str
    stage: str
    progress: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0
    remove_source: bool = True
    media_type: Optional[str] = None
    title: Optional[str] = None
    transfer_history_id: Optional[int] = None
    created_at: str
    updated_at: str
