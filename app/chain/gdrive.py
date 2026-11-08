import errno
import calendar
import hashlib
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from app.chain import ChainBase
from app.core.config import settings
from app.core.event import Event, eventmanager
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.systemconfig_oper import SystemConfigOper
from app.log import logger
from app.schemas.gdrive import (
    GdrivePreflightCheck,
    GdrivePreflightResult,
    GdriveSettings,
    GdriveTaskCleanupResult,
)
from app.schemas.types import EventType, GdriveTaskStatus, SystemConfigKey


_TASK_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_CLOSING = False
_TASK_LOCK = threading.RLock()
_ARCHIVE_LOCK = threading.RLock()
_ACTIVE_TASKS: set[int] = set()
_RECOVERY_TIMERS: dict[int, threading.Timer] = {}
_WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex}"
_LEASE_SECONDS = 120
_RECOVERY_RETRY_GRACE_SECONDS = 1.0
_HARD_LINK_FALLBACK_ERRNOS = {
    errno.EPERM,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.EXDEV,
}


class GdriveChain(ChainBase):
    """
    Google Drive 挂载目录后处理链。

    该链把整理完成的本地媒体文件复制到挂载目录，再归档到最终目录并生成文本
    STRM 文件。所有阶段都写入数据库，进程重启后可以从上一次阶段继续执行。
    """

    @staticmethod
    def get_settings() -> GdriveSettings:
        """读取 Google Drive 挂载目录配置。"""
        value = SystemConfigOper().get(SystemConfigKey.GdriveSettings)
        if value:
            return GdriveSettings(**value)
        return GdriveSettings(
            enabled=settings.GDRIVE_ENABLED,
            mount_path=settings.GDRIVE_MOUNT_PATH,
            archive_path=settings.GDRIVE_ARCHIVE_PATH,
            source_root=settings.GDRIVE_SOURCE_ROOT,
            strm_path_prefix=settings.GDRIVE_STRM_PATH_PREFIX,
            remove_source=settings.GDRIVE_REMOVE_SOURCE,
            version_suffix=settings.GDRIVE_VERSION_SUFFIX,
            copy_chunk_size=settings.GDRIVE_COPY_CHUNK_SIZE,
        )

    @classmethod
    def save_settings(cls, value: GdriveSettings) -> None:
        """保存 Google Drive 挂载目录配置。"""
        SystemConfigOper().set(SystemConfigKey.GdriveSettings, value.model_dump())

    @classmethod
    def preflight(
        cls, source_path: str, archive_path: Optional[str] = None
    ) -> GdrivePreflightResult:
        """
        只读检查归档配置、源文件和挂载路径。

        预检不会创建目录或写入测试文件；普通目录不会被识别为真实挂载点，
        因此可用于上线前发现配置问题，但不能替代现场挂载能力测试。
        """
        config = cls.get_settings()
        source = Path(source_path).expanduser().resolve()
        checks: list[GdrivePreflightCheck] = []
        mount_path = Path(config.mount_path).expanduser().resolve() if config.mount_path else None
        archive_result: Optional[Path] = None
        source_size: Optional[int] = None

        def add_check(name: str, passed: bool, level: str, message: str) -> None:
            checks.append(GdrivePreflightCheck(
                name=name, passed=passed, level=level, message=message
            ))

        add_check(
            "enabled",
            config.enabled,
            "error" if not config.enabled else "success",
            "Google Drive 后处理已启用" if config.enabled else "Google Drive 后处理未启用",
        )
        add_check(
            "source",
            source.is_file() and source.suffix.lower() != ".strm",
            "success" if source.is_file() and source.suffix.lower() != ".strm" else "error",
            f"源文件可用：{source}" if source.is_file() and source.suffix.lower() != ".strm"
            else f"源文件不存在或不能是 STRM：{source}",
        )
        if source.is_file() and source.suffix.lower() != ".strm":
            source_size = source.stat().st_size

        if mount_path is None:
            add_check("mount_directory", False, "error", "未配置 Google Drive 挂载目录")
        else:
            mount_is_directory = mount_path.is_dir()
            add_check(
                "mount_directory",
                mount_is_directory,
                "success" if mount_is_directory else "error",
                f"挂载目录可访问：{mount_path}" if mount_is_directory
                else f"挂载目录不可用：{mount_path}",
            )
            if mount_is_directory:
                is_mount = mount_path.is_mount()
                add_check(
                    "real_mount",
                    is_mount,
                    "success" if is_mount else "warning",
                    "路径是系统真实挂载点" if is_mount
                    else "路径是普通目录，不是真实挂载点；现场验证前不能视为 GDrive 可用",
                )
                writable = os.access(mount_path, os.W_OK)
                add_check(
                    "mount_writable",
                    writable,
                    "success" if writable else "error",
                    "挂载目录具备写权限" if writable else "挂载目录没有写权限",
                )

                if source.is_file() and source.suffix.lower() != ".strm":
                    preview_task = SimpleNamespace(archive_path=archive_path)
                    try:
                        archive_result = cls._get_archive_path(
                            preview_task, source, mount_path, config
                        )
                        parent_ok = archive_result.parent == mount_path or mount_path in archive_result.parent.parents
                        add_check(
                            "archive_path",
                            parent_ok,
                            "success" if parent_ok else "error",
                            f"归档路径安全：{archive_result}" if parent_ok
                            else "归档路径不在挂载目录内",
                        )
                        if archive_result.exists():
                            add_check(
                                "archive_conflict",
                                True,
                                "warning",
                                f"归档目标已存在，执行时会生成版本文件：{archive_result}",
                            )
                        else:
                            add_check("archive_conflict", True, "success", "归档目标当前不存在")
                    except (OSError, ValueError, RuntimeError) as err:
                        add_check("archive_path", False, "error", str(err))
            else:
                add_check("real_mount", False, "error", "挂载目录不存在，无法检查真实挂载点")

        strm_result = source.with_suffix(".strm") if source.suffix.lower() != ".strm" else None
        errors = [check for check in checks if not check.passed and check.level == "error"]
        warnings = [check for check in checks if not check.passed and check.level == "warning"]
        ready = not errors and not warnings
        return GdrivePreflightResult(
            ready=ready,
            source_path=source.as_posix(),
            mount_path=mount_path.as_posix() if mount_path else "",
            archive_path=archive_result.as_posix() if archive_result else None,
            strm_path=strm_result.as_posix() if strm_result else None,
            source_size=source_size,
            checks=checks,
        )

    @classmethod
    def resume_pending_tasks(cls) -> None:
        """恢复进程重启前未完成的任务。"""
        config = cls.get_settings()
        if not config.enabled:
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        oper = GdriveTaskOper()
        release_expired_leases = getattr(oper, "release_expired_leases", None)
        if release_expired_leases:
            release_expired_leases(now)
        for task in oper.list_unfinished():
            if task.status in {
                GdriveTaskStatus.PENDING.value,
                GdriveTaskStatus.UPLOADING.value,
                GdriveTaskStatus.ARCHIVING.value,
                GdriveTaskStatus.GENERATING_STRM.value,
            }:
                cls._update_task(
                    task.id,
                    event_type="recovered",
                    detail="服务启动时恢复未完成任务",
                )
                cls().start_task(task.id)

    @classmethod
    def create_task(
        cls,
        source_path: str,
        archive_path: Optional[str] = None,
        remove_source: Optional[bool] = None,
        transfer_history_id: Optional[int] = None,
        media_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> tuple[Optional[int], str]:
        """
        创建任务并异步启动处理。

        :param source_path: 整理完成的本地媒体文件路径
        :param archive_path: 可选的归档目标路径
        :param remove_source: 成功后是否删除本地源文件
        :param transfer_history_id: 关联的整理历史 ID
        :param media_type: 媒体类型
        :param title: 媒体标题
        :return: 任务 ID 与错误信息，失败时任务 ID 为 None
        """
        try:
            return cls._create_task(source_path, archive_path, remove_source,
                                    transfer_history_id, media_type, title), ""
        except (OSError, RuntimeError, ValueError) as err:
            logger.warning(f"创建 Google Drive 任务失败：{err}")
            return None, str(err)

    @classmethod
    def _create_task(
        cls, source_path: str, archive_path: Optional[str], remove_source: Optional[bool],
        transfer_history_id: Optional[int], media_type: Optional[str], title: Optional[str],
    ) -> int:
        """创建任务，错误交给公开接口转换为领域结果。"""
        config = cls.get_settings()
        if not config.enabled:
            raise RuntimeError("Google Drive 功能未启用")
        source = Path(source_path).expanduser().resolve()
        if source.suffix.lower() == ".strm":
            raise ValueError("源文件不能是 STRM 文件")
        if not source.is_file():
            raise FileNotFoundError(f"源文件不存在：{source}")
        source_stat = source.stat()
        source_sha256 = cls._file_digest(source)
        current_stat = source.stat()
        if (
            source_stat.st_dev != current_stat.st_dev
            or source_stat.st_ino != current_stat.st_ino
            or source_stat.st_size != current_stat.st_size
            or source_stat.st_mtime_ns != current_stat.st_mtime_ns
        ):
            raise IOError(f"源文件在创建任务时发生变化：{source}")
        existing = GdriveTaskOper().get_active_by_source(source.as_posix())
        if existing:
            cls().start_task(existing.id)
            return existing.id
        task = GdriveTaskOper().add(
            src_path=source.as_posix(),
            archive_path=archive_path,
            source_size=source_stat.st_size,
            source_device=source_stat.st_dev,
            source_inode=source_stat.st_ino,
            source_sha256=source_sha256,
            uploaded_size=0,
            status=GdriveTaskStatus.PENDING.value,
            stage=GdriveTaskStatus.PENDING.value,
            progress=0,
            remove_source=config.remove_source if remove_source is None else remove_source,
            media_type=media_type,
            title=title,
            transfer_history_id=transfer_history_id,
        )
        cls().start_task(task.id)
        return task.id

    @classmethod
    def start_task(cls, task_id: int) -> None:
        """将任务提交到后台线程，重复提交时保持幂等。"""
        global _TASK_EXECUTOR
        with _TASK_LOCK:
            if _EXECUTOR_CLOSING:
                raise RuntimeError("Google Drive 任务执行器正在关闭")
            recovery_timer = _RECOVERY_TIMERS.pop(task_id, None)
            if recovery_timer:
                recovery_timer.cancel()
            if task_id in _ACTIVE_TASKS:
                return
            if _TASK_EXECUTOR is None:
                _TASK_EXECUTOR = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="gdrive",
                )
            executor = _TASK_EXECUTOR
            _ACTIVE_TASKS.add(task_id)
            try:
                executor.submit(cls()._run_task, task_id)
            except Exception:
                _ACTIVE_TASKS.discard(task_id)
                raise

    @classmethod
    def _schedule_recovery(cls, task_id: int, delay: float) -> None:
        """在其他进程租约到期后重新提交任务。"""
        with _TASK_LOCK:
            if _EXECUTOR_CLOSING or task_id in _RECOVERY_TIMERS:
                return
            timer = threading.Timer(
                max(delay, 0.1), cls._run_recovery_timer, args=(task_id,)
            )
            timer.daemon = True
            _RECOVERY_TIMERS[task_id] = timer
            timer.start()

    @classmethod
    def _run_recovery_timer(cls, task_id: int) -> None:
        """执行租约到期后的任务恢复提交。"""
        with _TASK_LOCK:
            _RECOVERY_TIMERS.pop(task_id, None)
            if _EXECUTOR_CLOSING:
                return
        try:
            cls.start_task(task_id)
        except Exception as err:
            cls._update_task(
                task_id,
                event_type="recovery_failed",
                detail=f"租约到期后提交恢复任务失败：{err}",
            )
            logger.warning(f"Google Drive 任务 {task_id} 延迟恢复失败：{err}")

    @staticmethod
    def _recovery_delay(lease_until: Optional[str]) -> Optional[float]:
        """计算租约到期前的等待时间，兼容数据库中的 UTC 文本格式。"""
        if not lease_until:
            return None
        try:
            lease_epoch = calendar.timegm(time.strptime(lease_until, "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0.1, lease_epoch - time.time() + _RECOVERY_RETRY_GRACE_SECONDS)

    @classmethod
    def retry_task(cls, task_id: int) -> tuple[bool, str]:
        """重置失败或取消任务并重新执行。"""
        with _TASK_LOCK:
            if task_id in _ACTIVE_TASKS:
                return False, "任务仍在运行"
            task = GdriveTaskOper().get(task_id)
            if not task or task.status not in {
                GdriveTaskStatus.FAILED.value,
                GdriveTaskStatus.CANCELLED.value,
            }:
                return False, "任务不存在或不能重试"
            if _EXECUTOR_CLOSING:
                return False, "Google Drive 任务执行器正在关闭"
            task_stage = getattr(task, "stage", GdriveTaskStatus.PENDING.value)
            retry_stage = task_stage if task_stage in {
                GdriveTaskStatus.PENDING.value,
                GdriveTaskStatus.UPLOADING.value,
                GdriveTaskStatus.ARCHIVING.value,
                GdriveTaskStatus.GENERATING_STRM.value,
            } else GdriveTaskStatus.PENDING.value
            cls._update_task(
                task_id,
                status=retry_stage,
                stage=retry_stage,
                error_message=None,
                retry_count=(task.retry_count or 0) + 1,
                event_type="retry",
                detail=f"第 {(task.retry_count or 0) + 1} 次重试",
            )
            try:
                cls.start_task(task_id)
            except RuntimeError as err:
                cls._update_task(
                    task_id,
                    status=task.status,
                    error_message=str(err),
                    event_type="retry_failed",
                    detail="提交重试任务失败",
                )
                logger.warning(f"重试 Google Drive 任务失败：{err}")
                return False, str(err)
        return True, ""

    @classmethod
    def cancel_task(cls, task_id: int) -> bool:
        """取消等待中或运行中的任务。"""
        with _TASK_LOCK:
            task = GdriveTaskOper().get(task_id)
            if not task or task.status in {
                GdriveTaskStatus.COMPLETED.value,
                GdriveTaskStatus.CANCELLED.value,
            }:
                return False
            return cls._update_task(
                task_id,
                status=GdriveTaskStatus.CANCELLED.value,
                error_message="任务已取消",
                event_type="cancelled",
                detail="用户取消任务",
            )

    @classmethod
    def cleanup_task(cls, task_id: int, dry_run: bool = True) -> GdriveTaskCleanupResult:
        """
        预览或清理指定任务留下的临时文件和归属标记。

        只处理由任务 ID 和已持久化路径精确推导出的文件，不扫描目录，也不删除源文件、
        归档文件或有效 STRM 文件。失败任务始终保留临时文件以支持重试。
        """
        task = GdriveTaskOper().get(task_id)
        if not task:
            return GdriveTaskCleanupResult(
                task_id=task_id,
                dry_run=dry_run,
                can_cleanup=False,
                message="任务不存在",
            )

        config = cls.get_settings()
        candidates: list[Path] = []
        try:
            mount_root = cls._safe_directory(config.mount_path, "挂载目录")
            source = Path(task.src_path)
            candidates.append(
                mount_root / ".mymediafolo-uploading" / f"{task.id}-{source.name}.part"
            )
            archive_path = cls._cleanup_path_inside_mount(task.archive_path, mount_root)
            if archive_path:
                candidates.append(cls._archive_claim_path(task.id, archive_path))
            if task.strm_path:
                strm_path = Path(task.strm_path).expanduser().resolve()
                expected_strm_path = source.with_suffix(".strm").resolve()
                if strm_path != expected_strm_path:
                    raise ValueError(f"任务 STRM 路径不符合源文件映射：{strm_path}")
                candidates.append(cls._strm_claim_path(strm_path, task.id))
        except (OSError, RuntimeError, ValueError) as err:
            return GdriveTaskCleanupResult(
                task_id=task_id,
                status=task.status,
                dry_run=dry_run,
                can_cleanup=False,
                message=str(err),
            )

        existing = [path for path in candidates if path.is_file() or path.is_symlink()]
        status = task.status
        can_cleanup = status in {
            GdriveTaskStatus.CANCELLED.value,
            GdriveTaskStatus.COMPLETED.value,
        }
        if not can_cleanup:
            retained = existing
            message = "任务仍可重试或正在运行，保留临时文件"
        elif dry_run:
            retained = existing
            message = "预览完成；再次提交 dry_run=false 才会删除可安全清理的文件"
        else:
            retained = []
            for path in existing:
                if not cls._cleanup_candidate(task, path, status):
                    retained.append(path)
                    continue
                try:
                    path.unlink()
                except OSError as err:
                    logger.warning(f"清理 Google Drive 任务文件失败：{path}，{err}")
                    retained.append(path)
            message = "任务临时文件清理完成"

        return GdriveTaskCleanupResult(
            task_id=task_id,
            status=status,
            dry_run=dry_run,
            can_cleanup=can_cleanup,
            message=message,
            candidates=[path.as_posix() for path in existing],
            removed_paths=[] if dry_run else [
                path.as_posix() for path in existing if path not in retained
            ],
            retained_paths=[path.as_posix() for path in retained],
        )

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        """接收整理完成事件并为整理后的媒体文件创建上传任务。"""
        config = self.get_settings()
        if not config.enabled or not event:
            return
        data = event.event_data or {}
        transfer_info = data.get("transferinfo")
        if not transfer_info:
            return
        paths = list(getattr(transfer_info, "file_list_new", None) or [])
        if not paths and getattr(transfer_info, "target_item", None):
            paths = [transfer_info.target_item.path]
        for path in paths:
            try:
                _, error = self.create_task(
                    source_path=path,
                    transfer_history_id=data.get("transfer_history_id"),
                    media_type=getattr(data.get("mediainfo"), "type", None),
                    title=getattr(data.get("mediainfo"), "title", None),
                )
                if error:
                    logger.warning(f"整理完成后创建 Google Drive 任务失败：{error}")
            except (FileNotFoundError, OSError, RuntimeError, ValueError) as err:
                logger.warning(f"整理完成后创建 Google Drive 任务失败：{err}")

    def _run_task(self, task_id: int) -> None:
        """执行单个任务并把异常持久化为失败状态。"""
        claimed = False
        try:
            claimed = self._renew_lease(task_id)
            if not claimed:
                task = GdriveTaskOper().get(task_id)
                delay = self._recovery_delay(getattr(task, "lease_until", None)) if task else None
                if task and delay is not None and task.status in {
                    GdriveTaskStatus.PENDING.value,
                    GdriveTaskStatus.UPLOADING.value,
                    GdriveTaskStatus.ARCHIVING.value,
                    GdriveTaskStatus.GENERATING_STRM.value,
                }:
                    self._update_task(
                        task_id,
                        event_type="recovery_deferred",
                        detail="任务仍由其他进程持有租约，租约到期后自动重试",
                    )
                    self._schedule_recovery(task_id, delay)
                elif task and task.status in {
                    GdriveTaskStatus.PENDING.value,
                    GdriveTaskStatus.UPLOADING.value,
                    GdriveTaskStatus.ARCHIVING.value,
                    GdriveTaskStatus.GENERATING_STRM.value,
                }:
                    self._update_task(
                        task_id,
                        event_type="recovery_deferred",
                        detail="任务租约已释放，立即重试领取",
                    )
                    self._schedule_recovery(task_id, 0.1)
                logger.info(f"Google Drive 任务 {task_id} 已由其他进程持有")
                return
            task = GdriveTaskOper().get(task_id)
            if not task or task.status == GdriveTaskStatus.CANCELLED.value:
                return
            self._process_task(task_id)
        except Exception as err:
            logger.error(f"Google Drive 任务 {task_id} 失败：{err}")
            with _TASK_LOCK:
                task = GdriveTaskOper().get(task_id)
                owns_lease = bool(
                    claimed and task and GdriveTaskOper().owns(task_id, _WORKER_ID)
                )
                unfinished = task and task.status in {
                    GdriveTaskStatus.PENDING.value,
                    GdriveTaskStatus.UPLOADING.value,
                    GdriveTaskStatus.ARCHIVING.value,
                    GdriveTaskStatus.GENERATING_STRM.value,
                }
                if claimed and unfinished and not owns_lease:
                    self._update_task(
                        task_id,
                        event_type="recovery_deferred",
                        detail="任务租约丢失，停止当前文件操作并等待重新领取",
                    )
                    delay = self._recovery_delay(getattr(task, "lease_until", None))
                    self._schedule_recovery(task_id, delay if delay is not None else 0.1)
                elif claimed and task and task.status != GdriveTaskStatus.CANCELLED.value and owns_lease:
                    self._update_task(
                        task_id,
                        status=GdriveTaskStatus.FAILED.value,
                        error_message=str(err),
                        event_type="failed",
                        detail=f"{type(err).__name__}: {err}",
                    )
        finally:
            if claimed:
                GdriveTaskOper().release(task_id, _WORKER_ID)
            with _TASK_LOCK:
                _ACTIVE_TASKS.discard(task_id)

    @staticmethod
    def _renew_lease(task_id: int) -> bool:
        """原子续期任务租约；租约丢失时当前工作进程必须停止。"""
        now = time.time()
        return GdriveTaskOper().claim(
            task_id,
            _WORKER_ID,
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now + _LEASE_SECONDS)),
        )

    @staticmethod
    def _update_task(task_id: int, event_type: str = "state_change",
                     detail: Optional[str] = None, **kwargs: object) -> bool:
        """更新任务并记录状态、阶段、重试或恢复事件。"""
        oper = GdriveTaskOper()
        updater = getattr(oper, "update_with_history", None)
        if updater:
            return updater(task_id, event_type=event_type, detail=detail, **kwargs)
        update = getattr(oper, "update", None)
        if update:
            return update(task_id, **kwargs)
        return False

    def _process_task(self, task_id: int) -> None:
        """按上传、归档、STRM 和源文件清理顺序处理任务。"""
        config = self.get_settings()
        if not config.enabled:
            raise RuntimeError("Google Drive 功能未启用")
        mount_root = self._safe_directory(config.mount_path, "挂载目录")
        task = GdriveTaskOper().get(task_id)
        if not task:
            return
        source = Path(task.src_path)
        if not source.is_file() and task.stage not in {
            GdriveTaskStatus.GENERATING_STRM.value,
        }:
            raise FileNotFoundError(f"源文件不存在：{source}")
        source_size = source.stat().st_size if source.is_file() else task.source_size
        archive_path = self._get_archive_path(task, source, mount_root, config)
        if source.is_file() and archive_path == source.resolve():
            raise ValueError("归档目标不能与源文件相同")
        staging_path = mount_root / ".mymediafolo-uploading" / f"{task.id}-{source.name}.part"
        staging_path.parent.mkdir(parents=True, exist_ok=True)

        stage = task.stage
        if stage in {
            GdriveTaskStatus.PENDING.value,
            GdriveTaskStatus.UPLOADING.value,
        }:
            with _TASK_LOCK:
                if self._is_cancelled(task_id):
                    return
                self._update_task(
                    task_id,
                    status=GdriveTaskStatus.UPLOADING.value,
                    stage=GdriveTaskStatus.UPLOADING.value,
                    archive_path=archive_path.as_posix(),
                    source_size=source_size,
                )
            self._copy_resumable(task_id, source, staging_path, config.copy_chunk_size)
            if self._is_cancelled(task_id):
                return
            if not self._files_equal(source, staging_path, task_id=task_id):
                staging_path.unlink(missing_ok=True)
                raise IOError(f"上传临时文件内容校验失败：{staging_path}")
            stage = GdriveTaskStatus.ARCHIVING.value

        if stage == GdriveTaskStatus.ARCHIVING.value:
            with _TASK_LOCK:
                if self._is_cancelled(task_id):
                    return
                self._update_task(
                    task_id,
                    status=GdriveTaskStatus.ARCHIVING.value,
                    stage=GdriveTaskStatus.ARCHIVING.value,
                    progress=95,
                )
            with _ARCHIVE_LOCK:
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                if staging_path.exists():
                    while True:
                        self._ensure_lease(task_id)
                        if archive_path.exists():
                            if os.path.samefile(staging_path, archive_path):
                                break
                            if self._archive_claim_path(task_id, archive_path).is_file():
                                marker_path = self._archive_claim_path(task_id, archive_path)
                                staging_digest = self._file_digest(staging_path, task_id)
                                if marker_path.read_text(encoding="ascii") != staging_digest:
                                    self._archive_claim_path(task_id, archive_path).unlink()
                                    archive_path = self._versioned_path(
                                        archive_path, config.version_suffix
                                    )
                                    self._update_task(
                                        task_id,
                                        archive_path=archive_path.as_posix(),
                                        event_type="archive_versioned",
                                        detail="归档目标冲突，分配新版本路径",
                                    )
                                    continue
                                if self._file_digest(archive_path, task_id) == staging_digest:
                                    break
                                self._claim_archive_copy(task_id, staging_path, archive_path)
                                break
                            archive_path = self._versioned_path(archive_path, config.version_suffix)
                        self._update_task(task_id, archive_path=archive_path.as_posix())
                        try:
                            if self._publish_archive(task_id, staging_path, archive_path):
                                break
                        except FileExistsError:
                            continue
                else:
                    if not self._verify_archive_claim(task_id, archive_path):
                        raise IOError(f"无法验证归档文件归属，保留已有文件：{archive_path}")
                if source.is_file() and not self._files_equal(source, archive_path, task_id=task_id):
                    staging_path.unlink(missing_ok=True)
                    self._update_task(
                        task_id,
                        stage=GdriveTaskStatus.PENDING.value,
                        uploaded_size=0,
                        progress=0,
                    )
                    raise IOError(f"归档文件内容校验失败：{archive_path}")
                with _TASK_LOCK:
                    if self._is_cancelled(task_id):
                        return
                    self._update_task(
                        task_id,
                        status=GdriveTaskStatus.GENERATING_STRM.value,
                        stage=GdriveTaskStatus.GENERATING_STRM.value,
                        progress=96,
                    )
            if self._is_cancelled(task_id):
                return
            stage = GdriveTaskStatus.GENERATING_STRM.value

        if stage != GdriveTaskStatus.GENERATING_STRM.value:
            raise RuntimeError(f"未知的 Google Drive 任务阶段：{stage}")
        if not archive_path.is_file():
            raise FileNotFoundError(f"归档文件不存在：{archive_path}")
        if not self._verify_archive_ownership(task_id, staging_path, archive_path):
            raise IOError(f"无法验证归档文件归属，保留已有文件：{archive_path}")

        strm_path = Path(getattr(task, "strm_path", None) or source.with_suffix(".strm"))
        with _TASK_LOCK:
            if self._is_cancelled(task_id):
                return
            self._update_task(
                task_id,
                status=GdriveTaskStatus.GENERATING_STRM.value,
                stage=GdriveTaskStatus.GENERATING_STRM.value,
                strm_path=strm_path.as_posix(),
                progress=98,
            )
        self._ensure_lease(task_id)
        strm_path = self._write_strm(
            strm_path, archive_path, mount_root, config.strm_path_prefix, task_id=task_id
        )
        self._update_task(task_id, strm_path=strm_path.as_posix())
        with _TASK_LOCK:
            if self._is_cancelled(task_id):
                return
            task = GdriveTaskOper().get(task_id)
            if not task or task.status != GdriveTaskStatus.GENERATING_STRM.value:
                return
            if not self._verify_archive_ownership(task_id, staging_path, archive_path):
                raise IOError(f"无法验证归档文件归属，保留已有文件：{archive_path}")
            if source.is_file() and not self._files_equal(source, archive_path, task_id=task_id):
                self._update_task(
                    task_id,
                    stage=GdriveTaskStatus.PENDING.value,
                    uploaded_size=0,
                    progress=0,
                )
                raise IOError(f"源文件在归档期间发生变化：{source}")
            if task.remove_source and source.suffix.lower() != ".strm" and source.exists():
                self._remove_source_safely(task_id, source, archive_path, task)
            self._update_task(
                task_id,
                status=GdriveTaskStatus.COMPLETED.value,
                stage=GdriveTaskStatus.COMPLETED.value,
                uploaded_size=source_size,
                progress=100,
                error_message=None,
            )
            try:
                staging_path.unlink(missing_ok=True)
            except OSError as err:
                logger.warning(f"清理 Google Drive 任务临时文件失败：{err}")
            try:
                self._remove_archive_claim(task_id, archive_path)
            except OSError as err:
                logger.warning(f"清理 Google Drive 归档标记失败：{err}")
            try:
                self._strm_claim_path(strm_path, task_id).unlink(missing_ok=True)
            except OSError as err:
                logger.warning(f"清理 Google Drive STRM 标记失败：{err}")

    @staticmethod
    def _safe_directory(path_value: str, label: str) -> Path:
        """校验配置目录，避免把空配置误解析为当前工作目录。"""
        if not path_value:
            raise RuntimeError(f"未配置 Google Drive {label}")
        path = Path(path_value).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(f"Google Drive {label}不可用：{path}")
        return path

    @staticmethod
    def _cleanup_path_inside_mount(path_value: Optional[str], mount_root: Path) -> Optional[Path]:
        """校验任务持久化路径，防止清理操作越过挂载根目录。"""
        if not path_value:
            return None
        path = Path(path_value).expanduser().resolve()
        if path != mount_root and mount_root not in path.parents:
            raise ValueError(f"任务路径不在 Google Drive 挂载目录内：{path}")
        return path

    @classmethod
    def _cleanup_candidate(cls, task: Any, path: Path, status: str) -> bool:
        """判断任务临时文件是否可以在当前状态下安全删除。"""
        if path.name == f"{task.id}-{Path(task.src_path).name}.part":
            if status == GdriveTaskStatus.CANCELLED.value:
                return True
            archive_path = Path(task.archive_path) if task.archive_path else None
            return bool(
                status == GdriveTaskStatus.COMPLETED.value
                and archive_path
                and archive_path.is_file()
                and cls._verify_archive_ownership(task.id, path, archive_path)
            )
        if path.name.endswith(".mymediafolo.claim"):
            if status == GdriveTaskStatus.COMPLETED.value:
                return True
            if task.strm_path and path == cls._strm_claim_path(Path(task.strm_path), task.id):
                return not Path(task.strm_path).is_file()
            archive_path = Path(task.archive_path) if task.archive_path else None
            return not archive_path or not archive_path.is_file()
        return False

    @staticmethod
    def _get_archive_path(task: Any, source: Path, mount_root: Path, config: GdriveSettings) -> Path:
        """根据归档根目录和源目录映射生成挂载目录内的安全路径。"""
        if task.archive_path:
            archive_path = Path(task.archive_path).resolve()
        else:
            archive_root = Path(config.archive_path).expanduser() if config.archive_path else mount_root
            source_root = Path(config.source_root).expanduser().resolve() if config.source_root else None
            relative = source.name
            if source_root:
                try:
                    relative = source.resolve().relative_to(source_root)
                except ValueError:
                    relative = source.name
            archive_path = (archive_root / relative).resolve()
        if archive_path != mount_root and mount_root not in archive_path.parents:
            raise ValueError("归档路径必须位于 Google Drive 挂载目录内")
        # 新任务如果目标已存在，必须预先分配新版本；恢复已有阶段时保留已持久化的目标。
        return archive_path

    @staticmethod
    def _versioned_path(path: Path, suffix: str) -> Path:
        """为冲突文件生成不覆盖历史版本的目标路径。"""
        index = 2
        while True:
            candidate = path.with_name(f"{path.stem} ({suffix} {index}){path.suffix}")
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _write_strm(
        strm_path: Path, archive_path: Path, mount_root: Path, prefix: str,
        task_id: Optional[int] = None,
    ) -> Path:
        """以不覆盖方式发布文本 STRM，并按任务身份恢复中断写入。"""
        target = archive_path.as_posix()
        if prefix:
            target = prefix.rstrip("/") + "/" + archive_path.relative_to(mount_root).as_posix()
        strm_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = strm_path.with_name(f".{strm_path.name}.{task_id}.mymediafolo.tmp")
        marker_path = strm_path.with_name(f".{strm_path.name}.{task_id}.mymediafolo.claim")
        content = target + "\n"
        if marker_path.exists():
            marker_value = marker_path.read_text(encoding="utf-8")
            if marker_value != content:
                raise IOError(f"STRM 任务标记内容不匹配：{marker_path}")
        else:
            try:
                with marker_path.open("x", encoding="utf-8") as marker:
                    marker.write(content)
                    marker.flush()
                    os.fsync(marker.fileno())
            except FileExistsError:
                if marker_path.read_text(encoding="utf-8") != content:
                    raise IOError(f"STRM 路径正被其他任务占用：{strm_path}")
        if (strm_path.exists() and marker_path.exists()
                and strm_path.read_text(encoding="utf-8") == content):
            return strm_path
        if not strm_path.exists():
            try:
                os.link(marker_path, strm_path)
            except FileExistsError:
                pass
            except OSError as error:
                if error.errno not in _HARD_LINK_FALLBACK_ERRNOS:
                    raise
                try:
                    with strm_path.open("x", encoding="utf-8") as output:
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                except FileExistsError:
                    pass
        if strm_path.exists() and not (
            marker_path.exists() and strm_path.read_text(encoding="utf-8") == content
        ):
            marker_path.unlink(missing_ok=True)
            while True:
                candidate = GdriveChain._versioned_path(strm_path, "version")
                strm_path = candidate
                marker_path = GdriveChain._strm_claim_path(strm_path, task_id)
                temp_path = strm_path.with_name(f".{strm_path.name}.{task_id}.mymediafolo.tmp")
                if marker_path.exists():
                    if marker_path.read_text(encoding="utf-8") == content:
                        if strm_path.is_file():
                            if strm_path.read_text(encoding="utf-8") == content:
                                break
                            continue
                        try:
                            with strm_path.open("x", encoding="utf-8") as output:
                                output.write(content)
                                output.flush()
                                os.fsync(output.fileno())
                            break
                        except FileExistsError:
                            continue
                    raise IOError(f"STRM 任务标记内容不匹配：{marker_path}")
                try:
                    with marker_path.open("x", encoding="utf-8") as marker:
                        marker.write(content)
                        marker.flush()
                        os.fsync(marker.fileno())
                    with strm_path.open("x", encoding="utf-8") as output:
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    break
                except FileExistsError:
                    continue
        elif not strm_path.exists():
            raise IOError(f"STRM 文件创建失败：{strm_path}")
        return strm_path

    @staticmethod
    def _strm_claim_path(strm_path: Path, task_id: int) -> Path:
        """返回 STRM 发布对应的任务专属归属标记路径。"""
        return strm_path.with_name(f".{strm_path.name}.{task_id}.mymediafolo.claim")

    @staticmethod
    def _archive_claim_path(task_id: int, archive_path: Path) -> Path:
        """返回归档副作用旁边的任务归属标记路径。"""
        return archive_path.with_name(f".{archive_path.name}.{task_id}.mymediafolo.claim")

    @classmethod
    def _claim_archive_copy(cls, task_id: int, staging_path: Path, archive_path: Path) -> None:
        """在不支持硬链接的挂载上，用独占标记和独占文件创建归档副本。"""
        marker_path = cls._archive_claim_path(task_id, archive_path)
        digest = cls._file_digest(staging_path, task_id)
        cls._ensure_lease(task_id)
        if marker_path.exists():
            if marker_path.read_text(encoding="ascii") != digest:
                raise IOError(f"归档任务标记内容不匹配：{marker_path}")
        else:
            try:
                with marker_path.open("x", encoding="ascii") as marker:
                    marker.write(digest)
                    marker.flush()
                    os.fsync(marker.fileno())
            except FileExistsError:
                if marker_path.read_text(encoding="ascii") != digest:
                    raise IOError(f"归档路径正被其他任务占用：{archive_path}")
        if archive_path.exists():
            if not marker_path.exists() or not cls._is_prefix(archive_path, staging_path, task_id):
                raise FileExistsError(f"归档路径已存在且不属于当前任务：{archive_path}")
            if cls._file_digest(archive_path, task_id) == digest:
                return
            mode = "ab"
        else:
            mode = "xb"
        try:
            with staging_path.open("rb") as source, archive_path.open(mode) as target:
                source.seek(archive_path.stat().st_size if mode == "ab" else 0)
                while block := source.read(8 * 1024 * 1024):
                    target.write(block)
                    cls._ensure_lease(task_id)
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError:
            if not cls._verify_archive_claim(task_id, archive_path):
                raise

    @classmethod
    def _publish_archive(cls, task_id: int, staging_path: Path, archive_path: Path) -> bool:
        """优先以硬链接发布归档，仅在文件系统明确不支持时回退复制。"""
        cls._ensure_lease(task_id)
        try:
            os.link(staging_path, archive_path)
        except FileExistsError:
            raise
        except OSError as error:
            if error.errno not in _HARD_LINK_FALLBACK_ERRNOS:
                raise
            cls._claim_archive_copy(task_id, staging_path, archive_path)
        return True

    @classmethod
    def _verify_archive_claim(cls, task_id: int, archive_path: Path) -> bool:
        """校验排他归档标记和归档内容一致。"""
        marker_path = cls._archive_claim_path(task_id, archive_path)
        return marker_path.is_file() and archive_path.is_file() and (
            marker_path.read_text(encoding="ascii") == cls._file_digest(archive_path, task_id)
        )

    @classmethod
    def _verify_archive_ownership(
        cls, task_id: int, staging_path: Path, archive_path: Path
    ) -> bool:
        """使用硬链接身份或任务专属内容标记验证归档归属。"""
        if staging_path.is_file() and archive_path.is_file():
            try:
                if os.path.samefile(staging_path, archive_path):
                    return True
            except OSError:
                pass
        return cls._verify_archive_claim(task_id, archive_path)

    @staticmethod
    def _remove_archive_claim(task_id: int, archive_path: Path) -> None:
        """清理任务专属归档标记。"""
        GdriveChain._archive_claim_path(task_id, archive_path).unlink(missing_ok=True)

    @classmethod
    def _remove_source_safely(
        cls, task_id: int, source: Path, archive_path: Path, task: Any
    ) -> None:
        """先隔离源文件，再复核内容，避免外部替换后误删不同文件。"""
        cls._ensure_lease(task_id)
        quarantine = source.with_name(f".{source.name}.{task_id}.mymediafolo.delete")
        if source.exists() and not quarantine.exists():
            try:
                os.replace(source, quarantine)
            except FileNotFoundError:
                pass
        if not quarantine.exists():
            return
        try:
            quarantined_stat = quarantine.stat()
            if (
                getattr(task, "source_device", None) != quarantined_stat.st_dev
                or getattr(task, "source_inode", None) != quarantined_stat.st_ino
                or not getattr(task, "source_sha256", None)
                or getattr(task, "source_sha256") != cls._file_digest(quarantine, task_id)
            ):
                if not source.exists():
                    os.replace(quarantine, source)
                raise IOError(f"无法验证隔离源文件身份，保留源文件：{source}")
            if not cls._files_equal(quarantine, archive_path, task_id=task_id):
                if source.exists():
                    raise IOError(f"源文件已变化且原路径被重新占用，隔离文件保留在：{quarantine}")
                os.replace(quarantine, source)
                raise IOError(f"源文件在删除隔离期间发生变化，已恢复：{source}")
            cls._ensure_lease(task_id)
            quarantine.unlink()
        except Exception:
            if quarantine.exists() and not source.exists():
                try:
                    os.replace(quarantine, source)
                except OSError:
                    pass
            raise

    @staticmethod
    def _file_digest(path: Path, task_id: Optional[int] = None) -> str:
        """计算文件 SHA-256 摘要。"""
        digest = hashlib.sha256()
        if task_id is not None:
            GdriveChain._ensure_lease(task_id)
        with path.open("rb") as file_obj:
            while block := file_obj.read(8 * 1024 * 1024):
                digest.update(block)
                if task_id is not None:
                    GdriveChain._ensure_lease(task_id)
        return digest.hexdigest()

    @staticmethod
    def _is_prefix(candidate: Path, source: Path, task_id: int) -> bool:
        """验证中断归档为源文件前缀，避免续写不属于当前任务的数据。"""
        if candidate.stat().st_size > source.stat().st_size:
            return False
        with candidate.open("rb") as candidate_file, source.open("rb") as source_file:
            while block := candidate_file.read(8 * 1024 * 1024):
                GdriveChain._ensure_lease(task_id)
                if source_file.read(len(block)) != block:
                    return False
        return True

    @staticmethod
    def _is_cancelled(task_id: int) -> bool:
        """查询任务是否已被用户取消。"""
        task = GdriveTaskOper().get(task_id)
        return bool(task and task.status == GdriveTaskStatus.CANCELLED.value)

    @staticmethod
    def _ensure_lease(task_id: int) -> None:
        """在不可逆文件操作前确认任务租约仍由本进程持有。"""
        if not GdriveChain._renew_lease(task_id):
            raise RuntimeError("Google Drive 任务租约已丢失，停止文件操作")

    @staticmethod
    def _files_equal(left: Path, right: Path, task_id: Optional[int] = None) -> bool:
        """比较两个文件的大小和 SHA-256，避免同大小的错误文件被误判为完成。"""
        if task_id is not None:
            GdriveChain._ensure_lease(task_id)
        if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
            return False
        left_hash = hashlib.sha256()
        right_hash = hashlib.sha256()
        with left.open("rb") as left_file, right.open("rb") as right_file:
            while True:
                left_block = left_file.read(8 * 1024 * 1024)
                right_block = right_file.read(8 * 1024 * 1024)
                if not left_block and not right_block:
                    break
                left_hash.update(left_block)
                right_hash.update(right_block)
                if task_id is not None:
                    GdriveChain._ensure_lease(task_id)
        return left_hash.digest() == right_hash.digest()

    def _copy_resumable(self, task_id: int, source: Path, target: Path, chunk_size: int) -> None:
        """以临时文件为目标执行可恢复复制并持久化进度。"""
        source_size = source.stat().st_size
        offset = target.stat().st_size if target.exists() else 0
        if offset > source_size:
            target.unlink()
            offset = 0
        mode = "r+b" if target.exists() else "wb"
        with source.open("rb") as source_file, target.open(mode) as target_file:
            source_file.seek(offset)
            target_file.seek(offset)
            try:
                while offset < source_size:
                    if self._is_cancelled(task_id):
                        return
                    self._ensure_lease(task_id)
                    block = source_file.read(max(chunk_size, 1024 * 1024))
                    if not block:
                        break
                    target_file.write(block)
                    target_file.flush()
                    offset += len(block)
                    progress = min(94, int(offset * 94 / source_size)) if source_size else 94
                    with _TASK_LOCK:
                        if self._is_cancelled(task_id):
                            return
                        self._update_task(
                            task_id,
                            uploaded_size=offset,
                            progress=progress,
                            status=GdriveTaskStatus.UPLOADING.value,
                            stage=GdriveTaskStatus.UPLOADING.value,
                        )
            finally:
                target_file.flush()
                os.fsync(target_file.fileno())
        if target.stat().st_size != source_size:
            raise IOError(f"上传临时文件大小校验失败：{target}")

    @staticmethod
    def shutdown() -> None:
        """等待 Google Drive 后处理线程完成，避免进程退出时丢失任务状态。"""
        global _TASK_EXECUTOR, _EXECUTOR_CLOSING
        with _TASK_LOCK:
            _EXECUTOR_CLOSING = True
            executor = _TASK_EXECUTOR
            _TASK_EXECUTOR = None
            recovery_timers = list(_RECOVERY_TIMERS.values())
            _RECOVERY_TIMERS.clear()
            for timer in recovery_timers:
                timer.cancel()
        try:
            if executor:
                executor.shutdown(wait=True, cancel_futures=False)
        finally:
            with _TASK_LOCK:
                _EXECUTOR_CLOSING = False
