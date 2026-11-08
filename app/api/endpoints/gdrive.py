from typing import Any, List, Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.chain.gdrive import GdriveChain
from app.chain.upload import UploadChain
from app.db.models import User
from app.db.gdrive_task_history_oper import GdriveTaskHistoryOper
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.gdrive_directory_oper import GdriveDirectoryOper
from app.db.user_oper import get_current_active_manage_user, get_current_active_superuser
from app.helper.gdrive import GdriveHelper
from app.helper.upload import UploadHelper
from app.schemas.gdrive import (
    GdriveCategoryImport, GdriveUploadCreate, GdriveUploadCredential, GdriveUploadSettings, GdriveUploadTarget,
)


router = APIRouter()


@router.get("/upload/settings", response_model=schemas.Response)
def get_upload_settings(_: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """读取独立上传配置及各账号凭据状态，永不返回 Token"""
    value = UploadChain.get_settings()
    return schemas.Response(success=True, data={
        **value.model_dump(),
        "credential_status": {account.id: UploadHelper.has_credential(account.id) for account in value.accounts},
    })


@router.put("/upload/settings", response_model=schemas.Response)
def update_upload_settings(value: GdriveUploadSettings,
                           _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """更新账号列表和上传并发，凭据通过独立只写接口管理"""
    UploadChain.save_settings(value)
    return schemas.Response(success=True)


@router.put("/upload/accounts/{account_id}/credential", response_model=schemas.Response)
def update_upload_credential(account_id: str, value: GdriveUploadCredential,
                             _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """写入指定账号的私有 OAuth 凭据且不回显"""
    if account_id not in {account.id for account in UploadChain.get_settings().accounts}:
        raise HTTPException(status_code=404, detail="上传账号不存在")
    try:
        UploadHelper.save_credential(account_id, value)
    except (ValueError, OSError):
        return schemas.Response(success=False, message="凭据保存失败，请检查 Token JSON 和私有目录权限")
    return schemas.Response(success=True)


@router.post("/upload/check", response_model=schemas.Response)
def check_upload_target(value: GdriveUploadTarget,
                        _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """显式执行各所选账号的共享盘权限只读检查"""
    accounts = {account.id for account in UploadChain.get_settings().accounts if account.enabled}
    if not set(value.account_ids).issubset(accounts):
        return schemas.Response(success=False, message="所选上传账号无效或已停用")
    results = []
    for account_id in value.account_ids:
        try:
            helper = UploadHelper(account_id)
            helper.check_target(value)
            if value.archive_folder_id:
                helper.check_target(value.model_copy(update={"folder_id": value.archive_folder_id}))
            results.append({"id": account_id, "success": True, "message": "上传及所选归档目录可写"})
        except (ValueError, RuntimeError, OSError) as err:
            results.append({"id": account_id, "success": False, "message": str(err)})
    return schemas.Response(success=all(item["success"] for item in results), data=results)


@router.post("/upload/tasks", response_model=schemas.Response)
def create_upload_task(value: GdriveUploadCreate,
                       _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """创建直接上传任务，独立于挂载复制开关"""
    task_id, error = UploadChain.create_task(value)
    return schemas.Response(success=task_id is not None, message=error, data={"id": task_id} if task_id else None)


@router.post("/categories/preview", response_model=schemas.Response)
def preview_category(value: schemas.GdriveCategoryPreview,
                     _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """离线预览分类和相对目录，MoviePilot 策略只读载入"""
    try:
        config = value.config
        policy = GdriveChain.category_snapshot(config)
        config = config.model_copy(update={"category_policy": schemas.GdriveCategoryPolicy(**policy)})
        rule = next((rule for rule in GdriveDirectoryOper.rules() if rule.id == config.rule_id), None)
        if config.rule_id and rule is None:
            raise ValueError("归档规则不存在，请重新选择")
        path = Path(config.source_path) / Path(value.filename).name
        result = GdriveHelper.directory_relative(config, rule, path, value.media)
        if config.classification_mode == "inherit" and rule and rule.category_policy is not None:
            policy = rule.category_policy.model_dump()
        return schemas.Response(success=True, data={"path": result.as_posix(), "policy": policy})
    except (OSError, ValueError) as err:
        return schemas.Response(success=False, message=str(err))


@router.post("/categories/import", response_model=schemas.Response)
def import_categories(value: GdriveCategoryImport,
                      _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """只解析分类 YAML 并返回有序草稿及诊断，不保存配置或读取媒体"""
    try:
        policy = GdriveHelper.parse_category_policy(value.content, value.policy)
        warnings = []
        for media_type in ("movie", "tv"):
            rules = getattr(policy, media_type)
            for index, rule in enumerate(rules):
                if not rule.conditions and index < len(rules) - 1:
                    warnings.append(f"{media_type}：{rule.name} 为兜底分类，其后的分类不会命中")
        if any(key.lstrip("?").startswith("series_") for rule in policy.movie + policy.tv for key in rule.conditions):
            warnings.append("系列字段只生成子目录；仅有系列字段时须命中系列。组合条件中的 ? 条件组须至少命中一个")
        warnings.append("只使用已有本地信息；缺少标签、演员或导演时不会在线补查，缺失字段不满足排除条件")
        return schemas.Response(success=True, data={"policy": policy.model_dump(), "warnings": warnings})
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.get("/rules", response_model=List[schemas.GdriveRuleInfo])
def list_rules(_: User = Depends(get_current_active_manage_user)) -> List[schemas.GdriveRuleInfo]:
    """读取电影、剧集归档规则"""
    return GdriveDirectoryOper.rules()


@router.post("/rules/preview", response_model=schemas.Response)
def preview_rule(value: schemas.GdriveRulePreview,
                 _: User = Depends(get_current_active_manage_user)) -> schemas.Response:
    """只解析样例文件名并预览相对路径，不查询外部服务或写文件"""
    try:
        filename = Path(Path(value.filename).name)
        config = schemas.GdriveDirectoryConfig(name="规则预览", source_path="/preview", archive_path="/preview-target")
        path = GdriveHelper.directory_relative(config, value.rule, Path("/preview") / filename, value.media)
        return schemas.Response(success=True, data={"path": path.as_posix()})
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.post("/rules", response_model=schemas.Response)
def create_rule(value: schemas.GdriveRuleConfig,
                _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """新增可复用媒体归档规则"""
    result = GdriveDirectoryOper.save_rule(value)
    return schemas.Response(success=True, data=result.model_dump())


@router.put("/rules/{rule_id}", response_model=schemas.Response)
def update_rule(rule_id: int, value: schemas.GdriveRuleConfig,
                _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """修改规则，已运行批次保持其命名快照"""
    try:
        result = GdriveDirectoryOper.save_rule(value, rule_id)
        return schemas.Response(success=True, data=result.model_dump())
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.delete("/rules/{rule_id}", response_model=schemas.Response)
def delete_rule(rule_id: int, _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """删除未被目录引用的命名规则"""
    try:
        return schemas.Response(success=GdriveDirectoryOper.delete_rule(rule_id))
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.get("/directories", response_model=List[schemas.GdriveDirectoryInfo])
def list_directories(_: User = Depends(get_current_active_manage_user)) -> List[schemas.GdriveDirectoryInfo]:
    """查询已保存的目录归档任务"""
    return GdriveDirectoryOper.list()


@router.post("/directories", response_model=schemas.Response)
def create_directory(value: schemas.GdriveDirectoryConfig,
                     _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """保存目录任务，缺省停用且不会自动扫描"""
    return _save_directory(value)


@router.put("/directories/{directory_id}", response_model=schemas.Response)
def update_directory(directory_id: int, value: schemas.GdriveDirectoryConfig,
                     _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """修改目录任务与过滤、定时配置"""
    return _save_directory(value, directory_id)


def _save_directory(value: schemas.GdriveDirectoryConfig, directory_id: Optional[int] = None) -> schemas.Response:
    try:
        # 停用时允许先保存目录，启用前再验证实际挂载范围。
        if value.enabled:
            _, error = GdriveChain.directory_paths(value)
            if error:
                return schemas.Response(success=False, message=error)
        result = GdriveDirectoryOper.save(value, directory_id)
        return schemas.Response(success=True, data=result.model_dump())
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.delete("/directories/{directory_id}", response_model=schemas.Response)
def delete_directory(directory_id: int,
                     _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """删除停用目录任务并保留扫描历史，不删除媒体文件"""
    try:
        return schemas.Response(success=GdriveDirectoryOper.delete(directory_id))
    except ValueError as err:
        return schemas.Response(success=False, message=str(err))


@router.post("/directories/{directory_id}/scan", response_model=schemas.Response)
def scan_directory(directory_id: int, value: schemas.GdriveScanRequest,
                   _: User = Depends(get_current_active_superuser)) -> schemas.Response:
    """手动扫描，默认只读预览；显式关闭预览才会启动文件处理"""
    scan_id, error = GdriveChain.scan_directory(directory_id, value.dry_run)
    return schemas.Response(success=scan_id is not None, message=error, data={"id": scan_id})


@router.get("/scans", response_model=List[schemas.GdriveScanInfo])
def list_scans(directory_id: Optional[int] = None,
               limit: int = Query(default=100, ge=1, le=500),
               _: User = Depends(get_current_active_manage_user)) -> List[schemas.GdriveScanInfo]:
    """读取手动与定时扫描批次和执行汇总"""
    GdriveDirectoryOper.reconcile()
    return GdriveDirectoryOper.list_scans(directory_id, limit)


@router.get("/scans/{scan_id}", response_model=schemas.GdriveScanDetail)
def get_scan(scan_id: int, _: User = Depends(get_current_active_manage_user)) -> schemas.GdriveScanDetail:
    """读取扫描批次的文件清单、过滤原因和执行错误"""
    GdriveDirectoryOper.reconcile()
    result = GdriveDirectoryOper.detail(scan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="扫描批次不存在")
    return result


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


@router.get("/tasks/{task_id}", response_model=schemas.GdriveTaskInfo)
def get_task(task_id: int, _: User = Depends(get_current_active_manage_user)) -> schemas.GdriveTaskInfo:
    """按编号读取文件执行状态，支持从较早扫描历史追踪流程"""
    task = GdriveTaskOper().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="文件任务不存在")
    return _task_info(task)


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
    task = GdriveTaskOper().get(task_id)
    success, error = (UploadChain.retry_task(task_id) if task and task.backend == "api"
                      else GdriveChain.retry_task(task_id))
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
