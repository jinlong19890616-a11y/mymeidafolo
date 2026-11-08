import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.chain import ChainBase
from app.core.config import global_vars
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.thread import ThreadHelper
from app.helper.upload import UploadHelper
from app.helper.gdrive import GdriveHelper
from app.log import logger
from app.schemas.gdrive import GdriveUploadCreate, GdriveUploadSettings, GdriveUploadTarget
from app.schemas.types import SystemConfigKey


_QUEUE_LOCK = threading.RLock()
_RUNNING: dict[int, str] = {}
_CLOSING = False
_WORKER_ID = f"upload-{os.getpid()}-{uuid.uuid4().hex}"


class UploadChain(ChainBase):
    """独立管理多账号直接上传队列，上传确认后才执行 STRM 和源文件策略"""

    @staticmethod
    def get_settings() -> GdriveUploadSettings:
        """读取独立于挂载功能的上传设置"""
        return GdriveUploadSettings(**(SystemConfigOper().get(SystemConfigKey.GdriveUploadSettings) or {}))

    @classmethod
    def save_settings(cls, value: GdriveUploadSettings) -> None:
        """保存公开账号和并发配置，立即调度满足新限制的等待任务"""
        SystemConfigOper().set(SystemConfigKey.GdriveUploadSettings, value.model_dump())
        cls.dispatch()

    @classmethod
    def create_task(cls, value: GdriveUploadCreate, dedupe_key: Optional[str] = None) -> tuple[Optional[int], str]:
        """校验本地源文件并持久化上传快照，排队阶段不会读取网盘或写入挂载"""
        try:
            config = cls.get_settings()
            if not config.enabled:
                raise ValueError("直接上传尚未启用，请先配置上传账号并启用")
            accounts = {account.id for account in config.accounts if account.enabled and UploadHelper.has_credential(account.id)}
            if not set(value.target.account_ids).issubset(accounts):
                raise ValueError("所选账号已停用或尚未配置 Token")
            source = Path(value.source_path).expanduser()
            if source.resolve() != source or not source.is_file() or source.suffix.lower() == ".strm":
                raise ValueError("源文件必须是规范化的本地文件路径，不能是符号链接或 STRM")
            target = value.target.model_copy(deep=True)
            if not target.path:
                target = GdriveUploadTarget(**{**target.model_dump(), "path": source.name})
            if target.archive_folder_id:
                target = GdriveUploadTarget(**{**target.model_dump(), "archive_path": target.archive_path or target.path})
                if target.folder_id == target.archive_folder_id:
                    raise ValueError("上传暂存目录和云端归档根目录必须不同")
            if value.strm_root:
                root = Path(value.strm_root)
                if not root.is_absolute() or root.resolve() != root or root == source.parent or root in source.parents:
                    raise ValueError("STRM 根目录必须是独立且规范化的绝对路径")
                if not target.strm_prefix:
                    raise ValueError("生成 STRM 需要配置可播放的路径或地址前缀")
            elif target.metadata_enabled or target.refresh_server:
                raise ValueError("STRM 元数据和扫库需要配置独立 STRM 根目录")
            previous = GdriveTaskOper().get_active_by_source(str(source))
            if previous:
                if (previous.backend != "api"
                        or GdriveUploadTarget(**previous.upload_target).model_dump() != target.model_dump()):
                    raise ValueError("源文件已有其他活动任务，请等待完成")
                return previous.id, ""
            stat = source.stat()
            if stat.st_size == 0:
                raise ValueError("源文件为空，无法创建媒体上传任务")
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                while block := stream.read(8 * 1024 * 1024):
                    digest.update(block)
            current = source.stat()
            if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (
                current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns,
            ):
                raise ValueError("建立任务期间源文件发生变化")
            task = GdriveTaskOper().add(
                src_path=str(source), source_size=stat.st_size, source_device=stat.st_dev, source_inode=stat.st_ino,
                source_sha256=digest.hexdigest(), backend="api", upload_target=target.model_dump(),
                archive_path=target.archive_path or target.path, strm_root=value.strm_root, remove_source=value.remove_source,
                dedupe_key=dedupe_key, status="pending", stage="pending", uploaded_size=0, progress=0,
            )
            cls.dispatch()
            return task.id, ""
        except (OSError, ValueError) as err:
            logger.warning(f"创建直接上传任务失败：{err}")
            return None, str(err)

    @classmethod
    def dispatch(cls) -> None:
        """按总并发及每账号并发原子分配等待任务，账号选择采用当前最小负载"""
        with _QUEUE_LOCK:
            config = cls.get_settings()
            if _CLOSING or not config.enabled or global_vars.is_system_stopped:
                return
            accounts = {account.id: account for account in config.accounts if account.enabled}
            running_targets = set()
            for task_id in _RUNNING:
                running = GdriveTaskOper().get(task_id)
                if running:
                    running_targets.update(cls._target_keys(GdriveUploadTarget(**running.upload_target)))
            for task in GdriveTaskOper().list_unfinished():
                if task.backend != "api" or task.id in _RUNNING:
                    continue
                if len(_RUNNING) >= config.concurrency:
                    break
                target = GdriveUploadTarget(**task.upload_target)
                target_keys = cls._target_keys(target)
                if target_keys & running_targets:
                    continue
                counts = {key: list(_RUNNING.values()).count(key) for key in accounts}
                allowed = [key for key in target.account_ids if key in accounts and counts[key] < accounts[key].concurrency]
                if task.upload_account_id:
                    allowed = [key for key in allowed if key == task.upload_account_id]
                if not allowed:
                    continue
                account_id = min(allowed, key=lambda key: counts[key])
                if not cls._renew_lease(task.id):
                    continue
                _RUNNING[task.id] = account_id
                running_targets.update(target_keys)
                cls._update_task(task.id, upload_account_id=account_id)
                try:
                    ThreadHelper().submit(cls._run_task, task.id, account_id, config.chunk_size_mb * 1024 * 1024)
                except Exception:
                    _RUNNING.pop(task.id, None)
                    GdriveTaskOper().release(task.id, _WORKER_ID)
                    raise

    @classmethod
    def _run_task(cls, task_id: int, account_id: str, chunk_size: int) -> None:
        try:
            cls._process_task(task_id, account_id, chunk_size)
        except Exception as err:
            task = GdriveTaskOper().get(task_id)
            if task and task.status != "cancelled" and GdriveTaskOper().owns(task_id, _WORKER_ID):
                if not _CLOSING and not global_vars.is_system_stopped:
                    cls._update_task(task_id, status="failed", error_message=str(err), event_type="failed")
            logger.error(f"直接上传任务 {task_id} 失败：{err}")
        finally:
            GdriveTaskOper().release(task_id, _WORKER_ID)
            with _QUEUE_LOCK:
                _RUNNING.pop(task_id, None)
            cls.dispatch()

    @staticmethod
    def _check(task_id: int) -> None:
        if global_vars.is_system_stopped or _CLOSING:
            raise RuntimeError("服务正在关闭，上传会话将在重启后恢复")
        task = GdriveTaskOper().get(task_id)
        if not task or task.status == "cancelled":
            raise RuntimeError("上传任务已取消")
        if not UploadChain._renew_lease(task_id):
            raise RuntimeError("上传任务租约已丢失")

    @staticmethod
    def _renew_lease(task_id: int) -> bool:
        now = time.time()
        return GdriveTaskOper().claim(task_id, _WORKER_ID,
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now + 120)))

    @staticmethod
    def _target_keys(target: GdriveUploadTarget) -> set[tuple[str, str, str]]:
        keys = {(target.drive_id, target.folder_id, target.path)}
        if target.archive_folder_id:
            keys.add((target.drive_id, target.archive_folder_id, target.archive_path or target.path))
        return keys

    @staticmethod
    def _update_task(task_id: int, **kwargs) -> bool:
        with _QUEUE_LOCK:
            task = GdriveTaskOper().get(task_id)
            if task and task.status == "cancelled" and kwargs.get("event_type") != "retry":
                raise RuntimeError("上传任务已取消")
            return GdriveTaskOper().update_with_history(task_id, **kwargs)

    @classmethod
    def _process_task(cls, task_id: int, account_id: str, chunk_size: int) -> None:
        cls._check(task_id)
        task = GdriveTaskOper().get(task_id)
        source = Path(task.src_path)
        target = GdriveUploadTarget(**task.upload_target)
        final_path = target.archive_path or target.path
        helper = UploadHelper(account_id, check=lambda: cls._check(task_id))
        if task.stage == "deleting_source":
            if target.archive_folder_id:
                helper.verify_archived(task.id, target, task.remote_file_id)
            else:
                helper.verify_uploaded(task.id, target, task.remote_file_id)
            if task.strm_root:
                output = Path(task.strm_path or "")
                content = target.strm_prefix.rstrip("/") + "/" + final_path + "\n"
                if (not output.is_absolute() or output.resolve() != output or not output.is_file()
                        or output.read_text(encoding="utf-8") != content):
                    raise ValueError("源文件删除恢复前 STRM 校验失败，保留源文件")
            cls._remove_source(task, source)
            cls._update_task(task_id, status="completed", stage="completed", progress=100, error_message=None)
            return
        cls._verify_source(task, source)
        cls._update_task(task_id, status="uploading", stage="uploading", error_message=None)

        def progress(offset: int) -> None:
            cls._check(task_id)
            cls._update_task(task_id, uploaded_size=offset,
                                     progress=min(95, int(offset * 95 / max(task.source_size, 1))))

        remote = helper.upload(task_id, source, target, chunk_size, progress)
        cls._check(task_id)
        cls._verify_source(task, source)
        if not task.remote_file_id:
            cls._update_task(task_id, event_type="upload_verified",
                             detail=f"已核验云端上传文件 {remote['id']}，父目录 {','.join(remote.get('parents', []))}")
        cls._update_task(task_id, remote_file_id=remote["id"], uploaded_size=task.source_size,
                                 status="archiving", stage="archiving", progress=96)
        if target.archive_folder_id:
            helper.archive_uploaded(task_id, target, remote["id"])
            cls._check(task_id)
            cls._verify_source(task, source)
            cls._update_task(task_id, event_type="cloud_archived", detail="已核验云端分类归档位置及文件身份")
        if task.strm_root:
            root = Path(task.strm_root)
            output = root / Path(final_path).with_suffix(".strm")
            if root.resolve() != root or output.resolve() != output or root not in output.parents:
                raise ValueError("STRM 输出路径发生变化或越界")
            cls._update_task(task_id, status="generating_strm", stage="generating_strm", progress=98)
            cls._check(task_id)
            strm = UploadHelper.write_strm(task_id, output, target.strm_prefix.rstrip("/") + "/" + final_path)
            cls._update_task(task_id, strm_path=str(strm))
            if target.metadata_enabled:
                cls._update_task(task_id, stage="scraping_metadata", progress=99)
                paths = GdriveHelper.prepare_strm_metadata(source, strm, root)
                cls._update_task(task_id, event_type="metadata_prepared", detail=f"已生成 {len(paths)} 个本地元数据文件")
            if target.refresh_server:
                cls._check(task_id)
                cls._update_task(task_id, stage="refreshing_library", progress=99)
                result = cls().run_module("mediaserver_refresh_library", server=target.refresh_server,
                                          library_id=target.refresh_library_id)
                if result is not True:
                    raise RuntimeError("STRM 已生成，媒体库刷新未确认，请重试任务；源文件保留")
                cls._update_task(task_id, event_type="library_refresh_requested", detail="媒体库已接受定向扫描请求")
        cls._check(task_id)
        if task.remove_source:
            cls._update_task(task_id, stage="deleting_source")
            cls._remove_source(task, source)
        cls._update_task(task_id, status="completed", stage="completed", progress=100, error_message=None)

    @classmethod
    def _verify_source(cls, task, source: Path) -> None:
        if source.is_symlink() or source.resolve() != source or not source.is_file():
            raise ValueError("源文件不存在、被替换或包含符号链接，停止上传")
        stat = source.stat()
        if (stat.st_dev, stat.st_ino, stat.st_size) != (task.source_device, task.source_inode, task.source_size):
            raise ValueError("源文件身份或大小发生变化，保留源文件")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while block := stream.read(8 * 1024 * 1024):
                cls._check(task.id)
                digest.update(block)
        if digest.hexdigest() != task.source_sha256:
            raise ValueError("源文件内容发生变化，保留源文件")

    @classmethod
    def _remove_source(cls, task, source: Path) -> None:
        cls._check(task.id)
        quarantine = source.with_name(f".{source.name}.{task.id}.gdrive-upload.delete")
        if quarantine.exists() and source.exists():
            raise ValueError("源文件及隔离记录同时存在，请检查后重试")
        if source.exists():
            cls._verify_source(task, source)
            os.rename(source, quarantine)
        if not quarantine.exists():
            return
        try:
            cls._verify_source(task, quarantine)
            with _QUEUE_LOCK:
                cls._check(task.id)
                quarantine.unlink()
        except Exception:
            if not source.exists():
                os.rename(quarantine, source)
            raise

    @classmethod
    def retry_task(cls, task_id: int) -> tuple[bool, str]:
        """保留账号及会话恢复失败任务，不与仍在退出的工作线程重叠"""
        with _QUEUE_LOCK:
            task = GdriveTaskOper().get(task_id)
            if task_id in _RUNNING or not task or task.backend != "api" or task.status not in {"failed", "cancelled"}:
                return False, "任务仍在运行或不能重试"
            stage = task.stage if task.stage == "deleting_source" else "pending"
            cls._update_task(task_id, status="pending", stage=stage, error_message=None,
                                     retry_count=task.retry_count + 1, event_type="retry")
            cls.dispatch()
        return True, ""

    @classmethod
    def cancel_task(cls, task_id: int) -> bool:
        """与上传状态写入共用调度锁，取消结果不会被后续进度覆盖"""
        with _QUEUE_LOCK:
            task = GdriveTaskOper().get(task_id)
            if not task or task.backend != "api" or task.status in {"completed", "cancelled"}:
                return False
            return cls._update_task(task_id, status="cancelled", error_message="任务已取消", event_type="cancelled")

    @staticmethod
    def shutdown() -> None:
        """阻止新上传调度，运行任务在下一分块边界保存会话并退出"""
        global _CLOSING
        with _QUEUE_LOCK:
            _CLOSING = True
