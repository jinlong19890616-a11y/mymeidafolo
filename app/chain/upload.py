import hashlib
import os
import threading
import time
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

from app.chain import ChainBase
from app.core.config import global_vars
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.gdrive_task_history_oper import GdriveTaskHistoryOper
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.thread import ThreadHelper
from app.helper.upload import UploadHelper
from app.helper.gdrive import GdriveHelper
from app.log import logger
from app.schemas.gdrive import GdriveUploadCreate, GdriveUploadSettings, GdriveUploadTarget
from app.schemas.message import Notification
from app.schemas.types import EventType, MessageChannel, NotificationType, SystemConfigKey


_QUEUE_LOCK = threading.RLock()
_RUNNING: dict[int, str] = {}
_CLOSING = False
_WORKER_ID = f"upload-{os.getpid()}-{uuid.uuid4().hex}"
_NOTIFICATION_LOCK = threading.Lock()
_REPORT_SECONDS = 24 * 60 * 60
_EVENT_LABELS = {"upload_verified": "☁️ 上传核验完成", "cloud_archived": "📦 媒体归档完成",
                 "strm_generated": "🎬 STRM 生成完成", "failed": "⚠️ 上传链路失败"}


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
        cls._initialize_report()
        cls.dispatch()

    @classmethod
    def _initialize_report(cls) -> None:
        if not (cls.get_settings().notifications_enabled or cls.get_settings().daily_report_enabled):
            return
        with _NOTIFICATION_LOCK:
            oper = SystemConfigOper()
            if not oper.get(SystemConfigKey.GdriveUploadNotificationState):
                oper.set(SystemConfigKey.GdriveUploadNotificationState, {"window_start": int(time.time())})

    @staticmethod
    def _display(value: object, limit: int = 220) -> str:
        return escape(str(value or "—")[:limit])

    @classmethod
    def _event_text(cls, event, task) -> str:
        target = task.upload_target or {}
        size = int(task.source_size or 0)
        lines = [f"📄 文件：{cls._display(Path(task.src_path).name)}",
                 f"📏 大小：{size / 1024 ** 2:.2f} MiB（{size} 字节）",
                 f"👤 账号：{cls._display(task.upload_account_id)}",
                 f"🔖 任务：#{task.id}", f"🕒 时间：{cls._display(event.created_at)}"]
        if event.event_type == "upload_verified":
            lines.append(f"📂 暂存：{cls._display(target.get('path'))}")
        elif event.event_type == "cloud_archived":
            lines.extend([f"🗂 分类：{cls._display(Path(task.archive_path or '').parent)}",
                          f"📂 归档：{cls._display(task.archive_path)}", "✅ 云端位置与文件身份已核验"])
        elif event.event_type == "strm_generated":
            lines.extend([f"📂 归档：{cls._display(task.archive_path)}",
                          f"🔗 STRM：{cls._display(task.strm_path)}", "✅ STRM 已写入；不代表媒体库已完成扫库或播放验收"])
        else:
            # 外部异常可能带 OAuth 或会话 URL，通知只携带可定位的阶段，不转发原始异常。
            lines.extend([f"⛔ 阶段：{cls._display(event.to_stage)}", "详情请查看任务历史；不会自动删除媒体源。"])
        return "\n".join(lines)

    def _deliver_notification(self, title: str, text: str, source: str) -> bool:
        message = Notification(channel=MessageChannel.Telegram, source=source or None,
                               mtype=NotificationType.Other, title=title, text=text, parse_mode="HTML")
        # 普通 post_message 不返回渠道回执；复用通知模块的确认发送接口，收到成功后才落回执。
        result = self.send_direct_message(message)
        if not result or result.success is not True:
            return False
        try:
            self.messageoper.add(**message.model_dump())
            self.eventmanager.send_event(EventType.NoticeMessage, self._build_notice_message_data(message))
        except Exception as err:
            logger.warning(f"Telegram已送达，通知中心记录失败：{type(err).__name__}")
        return True

    @classmethod
    def notify_pending(cls) -> None:
        """补发持久化阶段记录并发送固定二十四小时汇总，通知失败不改变媒体任务。"""
        config = cls.get_settings()
        if not (config.notifications_enabled or config.daily_report_enabled):
            return
        cls._initialize_report()
        if not _NOTIFICATION_LOCK.acquire(blocking=False):
            return
        try:
            chain = cls()
            history = GdriveTaskHistoryOper()
            if config.notifications_enabled:
                for event in history.list_notification_events(pending=True, limit=100):
                    task = GdriveTaskOper().get(event.task_id)
                    if not task or task.backend != "api":
                        continue
                    if not chain._deliver_notification(f"mymeidafolo · {_EVENT_LABELS[event.event_type]}",
                                                       cls._event_text(event, task), config.notification_source):
                        break
                    history.mark_notified(event)
            if config.daily_report_enabled:
                chain._send_daily_report(config)
        except Exception as err:
            logger.warning(f"上传通知暂未完成，将在下次调度重试：{type(err).__name__}")
        finally:
            _NOTIFICATION_LOCK.release()

    def _send_daily_report(self, config: GdriveUploadSettings) -> None:
        oper = SystemConfigOper()
        state = oper.get(SystemConfigKey.GdriveUploadNotificationState)
        start = float(state["window_start"])
        end = start + _REPORT_SECONDS
        if time.time() < end:
            return
        if not state.get("report"):
            begin_text = datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S")
            end_text = datetime.fromtimestamp(end).strftime("%Y-%m-%d %H:%M:%S")
            events = GdriveTaskHistoryOper().list_notification_events(start=begin_text, end=end_text)
            records = []
            counts = dict.fromkeys(_EVENT_LABELS, 0)
            total = 0
            for event in events:
                task = GdriveTaskOper().get(event.task_id)
                if not task or task.backend != "api":
                    continue
                counts[event.event_type] += 1
                if event.event_type == "upload_verified":
                    total += int(task.source_size or 0)
                path = task.strm_path if event.event_type == "strm_generated" else task.archive_path
                records.append(f"{_EVENT_LABELS[event.event_type]} · #{task.id} · {self._display(event.created_at)}\n"
                               f"{self._display(Path(task.src_path).name)} · {int(task.source_size or 0)} 字节\n"
                               f"{self._display(path)}")
            header = (f"🕒 统计窗口（应用本地时间，左闭右开）：\n{begin_text} → {end_text}\n"
                      f"☁️ 总上传量：{total / 1024 ** 3:.3f} GiB（{total} 字节）\n"
                      f"上传 {counts['upload_verified']} · 归档 {counts['cloud_archived']} · "
                      f"STRM {counts['strm_generated']} · 失败 {counts['failed']}\n"
                      "仅按任务首次核验成功计量，不计重试流量。\n")
            parts = [header]
            for record in records or ["本窗口没有上传、归档、STRM 或失败记录。"]:
                if len(parts[-1]) + len(record) + 2 > 1700:
                    parts.append("")
                parts[-1] += "\n\n" + record
            state["report"] = {"parts": parts, "next_part": 0, "source": config.notification_source}
            oper.set(SystemConfigKey.GdriveUploadNotificationState, state)
        report = state["report"]
        for number in range(report["next_part"], len(report["parts"])):
            if not self._deliver_notification(f"mymeidafolo · 📊 24小时上传报告 {number + 1}/{len(report['parts'])}",
                                              report["parts"][number], report["source"]):
                return
            report["next_part"] = number + 1
            oper.set(SystemConfigKey.GdriveUploadNotificationState, state)
        oper.set(SystemConfigKey.GdriveUploadNotificationState, {"window_start": end})

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
        cls._update_task(task_id, remote_file_id=remote["id"], uploaded_size=task.source_size,
                                 event_type="upload_verified", detail="云端上传文件大小及身份已核验",
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
            cls._update_task(task_id, strm_path=str(strm), event_type="strm_generated", detail="STRM已写入")
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
