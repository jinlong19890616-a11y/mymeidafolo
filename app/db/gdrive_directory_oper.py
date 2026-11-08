from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import threading

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import SessionFactory
from app.db.models.gdrive_directory import GdriveDirectory
from app.db.systemconfig_oper import SystemConfigOper
from app.schemas.types import SystemConfigKey
from app.db.models.gdrive_scan import GdriveScan
from app.db.models.gdrive_scan_item import GdriveScanItem
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_media import GdriveMedia
from app.schemas.gdrive import (
    GdriveDirectoryConfig, GdriveDirectoryInfo, GdriveScanDetail,
    GdriveScanInfo, GdriveScanItemInfo,
    GdriveRuleConfig, GdriveRuleInfo,
)


_ACTIVE_STATUSES = ("pending", "uploading", "archiving", "generating_strm")
_RULE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _directory_info(row: GdriveDirectory) -> GdriveDirectoryInfo:
    return GdriveDirectoryInfo(
        **row.config, id=row.id, revision=row.revision, next_run_at=row.next_run_at,
        last_scan_at=row.last_scan_at, created_at=row.created_at, updated_at=row.updated_at,
    )


def _lock_directory(db: object, directory_id: int) -> Optional[GdriveDirectory]:
    # SQLite 不支持行级 FOR UPDATE，先执行无值变化的写入以串行化配置和批次事务。
    db.query(GdriveDirectory).filter(GdriveDirectory.id == directory_id).update(
        {GdriveDirectory.revision: GdriveDirectory.revision}, synchronize_session=False,
    )
    return db.get(GdriveDirectory, directory_id)


def _counts(db: object, scan_id: int) -> tuple[int, int, int]:
    query = db.query(GdriveScanItem).filter(GdriveScanItem.scan_id == scan_id)
    scanned = query.count()
    matched = query.filter(
        (GdriveScanItem.reason.in_(("matched", "queued"))) | GdriveScanItem.reason.like("error:%"),
    ).count()
    return scanned, matched, scanned - matched


class GdriveDirectoryOper:
    """目录任务和扫描历史的数据访问，事务负责隔离重复触发"""

    @staticmethod
    def rules() -> list[GdriveRuleInfo]:
        """读取可复用的归档规则"""
        return [GdriveRuleInfo(**value) for value in SystemConfigOper().get(SystemConfigKey.GdriveRules) or []]

    @staticmethod
    def save_rule(value: GdriveRuleConfig, rule_id: Optional[int] = None) -> GdriveRuleInfo:
        """保存规则；已建立批次继续使用其原始快照"""
        with _RULE_LOCK:
            rules = GdriveDirectoryOper.rules()
            row = next((rule for rule in rules if rule.id == rule_id), None)
            if rule_id and row is None:
                raise ValueError("归档规则不存在")
            result = GdriveRuleInfo(**value.model_dump(),
                                    id=row.id if row else SystemConfigOper().increment(SystemConfigKey.GdriveRuleSequence),
                                    revision=row.revision + 1 if row else 1, updated_at=_now())
            SystemConfigOper().set(SystemConfigKey.GdriveRules,
                                   [result.model_dump()] + [rule.model_dump() for rule in rules if rule.id != result.id])
            return result

    @staticmethod
    def delete_rule(rule_id: int) -> bool:
        """仅删除未被目录引用的规则，历史快照继续保留"""
        with _RULE_LOCK, SessionFactory() as db:
            rules = GdriveDirectoryOper.rules()
            if not any(rule.id == rule_id for rule in rules):
                return False
            if any(item.config.get("rule_id") == rule_id for item in db.query(GdriveDirectory).all()):
                raise ValueError("规则仍被目录任务引用，请先修改对应任务")
            SystemConfigOper().set(SystemConfigKey.GdriveRules,
                                   [rule.model_dump() for rule in rules if rule.id != rule_id])
            return True

    @staticmethod
    def list() -> list[GdriveDirectoryInfo]:
        """读取所有保存的目录任务"""
        with SessionFactory() as db:
            return [_directory_info(row) for row in db.query(GdriveDirectory).order_by(GdriveDirectory.id.desc()).all()]

    @staticmethod
    def get(directory_id: int) -> Optional[GdriveDirectoryInfo]:
        """按编号读取目录任务"""
        with SessionFactory() as db:
            row = db.get(GdriveDirectory, directory_id)
            return _directory_info(row) if row else None

    @staticmethod
    def save(value: GdriveDirectoryConfig, directory_id: Optional[int] = None) -> GdriveDirectoryInfo:
        """保存目录配置，有活动批次时拒绝修改"""
        with _RULE_LOCK, SessionFactory.begin() as db:
            row = _lock_directory(db, directory_id) if directory_id else None
            if directory_id and not row:
                raise ValueError("目录任务不存在")
            if directory_id and db.query(GdriveScan.id).filter(GdriveScan.active_key == directory_id).first():
                raise ValueError("任务正在扫描或执行，请等待完成后修改")
            if directory_id and db.query(GdriveScan.id).filter(
                GdriveScan.directory_id == directory_id, GdriveScan.finished_at.is_(None),
            ).first():
                raise ValueError("任务仍有待执行或活动批次，请等待完成后修改")
            if value.enabled:
                source = Path(value.source_path)
                for other in db.query(GdriveDirectory).filter(GdriveDirectory.enabled.is_(True)).all():
                    other_source = Path(other.config["source_path"])
                    if other.id != directory_id and (source == other_source or source in other_source.parents or other_source in source.parents):
                        raise ValueError("启用的目录任务不能监控相同或相互嵌套的源目录")
            if value.rule_id and not any(rule.id == value.rule_id for rule in GdriveDirectoryOper.rules()):
                raise ValueError("归档规则不存在，请重新选择")
            now = _now()
            if row is None:
                row = GdriveDirectory(created_at=now, revision=0)
                db.add(row)
            row.config = value.model_dump()
            row.enabled = value.enabled
            row.revision += 1
            row.updated_at = now
            row.next_run_at = (
                (datetime.now(timezone.utc) + timedelta(minutes=value.interval_minutes)).strftime("%Y-%m-%d %H:%M:%S")
                if value.enabled and value.schedule_enabled else None
            )
            db.flush()
            return _directory_info(row)

    @staticmethod
    def delete(directory_id: int) -> bool:
        """删除停用任务，保留已产生的执行历史"""
        with SessionFactory.begin() as db:
            row = _lock_directory(db, directory_id)
            if not row:
                return False
            if row.enabled or db.query(GdriveScan.id).filter(
                GdriveScan.directory_id == directory_id, GdriveScan.finished_at.is_(None),
            ).first() or db.query(GdriveScan.id).filter(GdriveScan.active_key == directory_id).first():
                raise ValueError("请先停用任务，并等待当前执行结束")
            db.delete(row)
            return True

    @staticmethod
    def due() -> list[int]:
        """查询到期且启用的目录任务"""
        with SessionFactory() as db:
            return [row.id for row in db.query(GdriveDirectory.id).filter(
                GdriveDirectory.enabled.is_(True), GdriveDirectory.next_run_at <= _now(),
            ).all()]

    @staticmethod
    def begin_scan(directory_id: int, dry_run: bool, trigger: str,
                   policy: Optional[dict] = None, event_paths: Optional[list[str]] = None,
                   event_media: Optional[dict] = None, expected_revision: Optional[int] = None) -> tuple[Optional[int], str]:
        """原子建立扫描批次并推进下次定时，重复触发返回领域错误"""
        try:
            with _RULE_LOCK, SessionFactory.begin() as db:
                row = _lock_directory(db, directory_id)
                if not row:
                    return None, "目录任务不存在"
                if expected_revision is not None and row.revision != expected_revision:
                    return None, "目录配置已改变，请重新触发"
                if not dry_run and not row.enabled:
                    return None, "目录任务尚未启用"
                now = _now()
                if trigger == "scheduled" and (not row.next_run_at or row.next_run_at > now):
                    return None, "尚未到达扫描时间"
                snapshot = dict(row.config)
                if policy is not None:
                    snapshot["category_policy"] = policy
                if event_paths is not None:
                    snapshot["event_paths"] = event_paths
                    snapshot["event_media"] = event_media or {}
                if snapshot.get("rule_id"):
                    rule = next((rule for rule in GdriveDirectoryOper.rules() if rule.id == snapshot["rule_id"]), None)
                    if rule is None:
                        return None, "归档规则不存在，请重新选择"
                    snapshot["rule_snapshot"] = rule.model_dump()
                scan = GdriveScan(
                    directory_id=directory_id, directory_name=row.config["name"], config=snapshot,
                    trigger=trigger, dry_run=dry_run, status="waiting" if trigger == "transfer" else "scanning",
                    active_key=None if trigger == "transfer" else directory_id,
                    scanned=0, matched=0, skipped=0, created_at=now,
                )
                db.add(scan)
                db.flush()
                row.last_scan_at = now
                if not dry_run and trigger != "transfer" and row.config["schedule_enabled"]:
                    row.next_run_at = (datetime.now(timezone.utc) + timedelta(
                        minutes=row.config["interval_minutes"],
                    )).strftime("%Y-%m-%d %H:%M:%S")
                return scan.id, ""
        except IntegrityError:
            return None, "该目录任务已有活动批次"

    @staticmethod
    def save_media(source: str, signature: str, media: dict) -> None:
        """按源路径更新离线快照，签名防止替换文件复用旧分类信息"""
        with _RULE_LOCK, SessionFactory.begin() as db:
            row = db.query(GdriveMedia).filter(GdriveMedia.source_path == source).first()
            if row is None:
                row = GdriveMedia(source_path=source)
                db.add(row)
            row.signature = signature
            row.media = media

    @staticmethod
    def get_media(source: str, signature: str) -> dict:
        """只返回仍匹配源文件版本的整理事件信息"""
        with SessionFactory() as db:
            row = db.query(GdriveMedia).filter(GdriveMedia.source_path == source, GdriveMedia.signature == signature).first()
            return dict(row.media) if row else {}

    @staticmethod
    def event_paths(scan_id: int) -> Optional[list[str]]:
        """返回整理事件批次的限定文件清单"""
        with SessionFactory() as db:
            return db.get(GdriveScan, scan_id).config.get("event_paths")

    @staticmethod
    def event_media(scan_id: int) -> dict:
        """读取事件批次冻结的媒体信息与源版本签名"""
        with SessionFactory() as db:
            return db.get(GdriveScan, scan_id).config.get("event_media") or {}

    @staticmethod
    def claim_event(scan_id: int) -> bool:
        """原子领取持久化事件批次，防止轮询与即时提交重复执行"""
        with SessionFactory.begin() as db:
            return bool(db.query(GdriveScan).filter(
                GdriveScan.id == scan_id, GdriveScan.status == "waiting",
            ).update({GdriveScan.status: "scanning"}, synchronize_session=False))

    @staticmethod
    def pending_events() -> list[int]:
        """读取服务重启或提交中断后仍待执行的整理事件"""
        with SessionFactory() as db:
            return [row.id for row in db.query(GdriveScan.id).filter(
                GdriveScan.trigger == "transfer", GdriveScan.status == "waiting",
            ).all()]

    @staticmethod
    def get_config(scan_id: int) -> GdriveDirectoryConfig:
        """读取扫描时保存的配置快照"""
        with SessionFactory() as db:
            return GdriveDirectoryConfig(**db.get(GdriveScan, scan_id).config)

    @staticmethod
    def get_rule_snapshot(scan_id: int) -> Optional[GdriveRuleInfo]:
        """读取扫描建立时冻结的命名规则"""
        with SessionFactory() as db:
            value = db.get(GdriveScan, scan_id).config.get("rule_snapshot")
            return GdriveRuleInfo(**value) if value else None

    @staticmethod
    def add_items(scan_id: int, values: list[dict]) -> None:
        """分批写入文件预览和过滤原因"""
        with SessionFactory.begin() as db:
            db.add_all([GdriveScanItem(scan_id=scan_id, **value) for value in values])

    @staticmethod
    def link_task(item_id: int, task_id: int) -> None:
        """将扫描项关联到持久化文件执行记录"""
        with SessionFactory.begin() as db:
            row = db.get(GdriveScanItem, item_id)
            row.task_id = task_id
            row.reason = "queued"

    @staticmethod
    def fail_item(item_id: int, error: str) -> None:
        """记录文件未能入队的原因"""
        with SessionFactory.begin() as db:
            db.get(GdriveScanItem, item_id).reason = f"error: {error}"

    @staticmethod
    def candidates(scan_id: int) -> list[dict]:
        """返回扫描匹配的文件项供后续逐个入队"""
        with SessionFactory() as db:
            return [row.to_dict() for row in db.query(GdriveScanItem).filter(
                GdriveScanItem.scan_id == scan_id, GdriveScanItem.reason == "matched",
            ).order_by(GdriveScanItem.id).all()]

    @staticmethod
    def find_task(dedupe_key: str) -> Optional[int]:
        """查找同一源版本已有的文件任务，失败和取消也不自动重建"""
        with SessionFactory() as db:
            row = db.query(GdriveTask.id).filter(GdriveTask.dedupe_key == dedupe_key).first()
            return row.id if row else None

    @staticmethod
    def finish_scan(scan_id: int, error: Optional[str] = None) -> None:
        """结束目录遍历，执行批次持续保留活动键直到文件任务结束"""
        with SessionFactory.begin() as db:
            row = db.get(GdriveScan, scan_id)
            row.scanned, row.matched, row.skipped = _counts(db, scan_id)
            row.error_message = error
            row.status = "failed" if error else ("previewed" if row.dry_run else "running")
            active = db.query(GdriveTask.id).join(
                GdriveScanItem, GdriveScanItem.task_id == GdriveTask.id,
            ).filter(GdriveScanItem.scan_id == scan_id, GdriveTask.status.in_(_ACTIVE_STATUSES)).first()
            if row.dry_run or (error and not active):
                row.active_key = None
                row.finished_at = _now()

    @classmethod
    def reconcile(cls, interrupted: bool = False) -> None:
        """汇总文件状态，服务重启时把未完成遍历标为中断而不重复提交"""
        with SessionFactory.begin() as db:
            for row in db.query(GdriveScan).filter(
                (GdriveScan.active_key.is_not(None)) | (
                    (GdriveScan.trigger == "transfer") & GdriveScan.finished_at.is_(None)
                    & (GdriveScan.status != "waiting")
                ),
            ).all():
                if row.status == "scanning":
                    if interrupted:
                        if row.trigger == "transfer":
                            # 事件仅包含明确文件，重启可安全重放，指纹继续防止重复任务。
                            row.status = "waiting"
                            db.query(GdriveScanItem).filter(GdriveScanItem.scan_id == row.id).delete()
                            continue
                        row.status = "interrupted"
                        row.error_message = "服务在扫描中重启；已有文件任务仍可恢复，请查看明细后重新扫描"
                        # 文件任务提交后、关联扫描项前崩溃时，按持久化指纹补回关联。
                        for item in db.query(GdriveScanItem).filter(
                            GdriveScanItem.scan_id == row.id, GdriveScanItem.task_id.is_(None),
                            GdriveScanItem.reason == "matched", GdriveScanItem.fingerprint.is_not(None),
                        ).all():
                            task = db.query(GdriveTask).filter(GdriveTask.dedupe_key == item.fingerprint).first()
                            if task:
                                item.task_id = task.id
                                item.reason = "queued"
                        db.flush()
                        row.scanned, row.matched, row.skipped = _counts(db, row.id)
                    else:
                        continue
                statuses = [value[0] for value in db.query(GdriveTask.status).join(
                    GdriveScanItem, GdriveScanItem.task_id == GdriveTask.id,
                ).filter(GdriveScanItem.scan_id == row.id).all()]
                if any(value in _ACTIVE_STATUSES for value in statuses):
                    continue
                errors = db.query(GdriveScanItem.id).filter(
                    GdriveScanItem.scan_id == row.id, GdriveScanItem.reason.like("error:%"),
                ).first()
                if row.status not in ("failed", "interrupted"):
                    row.status = "failed" if errors or any(value != "completed" for value in statuses) else "completed"
                row.active_key = None
                row.finished_at = _now()

    @staticmethod
    def list_scans(directory_id: Optional[int] = None, limit: int = 100,
                   scan_id: Optional[int] = None) -> list[GdriveScanInfo]:
        """读取最近扫描及文件任务汇总，最多五百条"""
        with SessionFactory() as db:
            query = db.query(GdriveScan)
            if directory_id is not None:
                query = query.filter(GdriveScan.directory_id == directory_id)
            if scan_id is not None:
                query = query.filter(GdriveScan.id == scan_id)
            result = []
            for row in query.order_by(GdriveScan.id.desc()).limit(limit).all():
                statuses = [value[0] for value in db.query(GdriveTask.status).join(
                    GdriveScanItem, GdriveScanItem.task_id == GdriveTask.id,
                ).filter(GdriveScanItem.scan_id == row.id).all()]
                submission_errors = db.query(func.count(GdriveScanItem.id)).filter(
                    GdriveScanItem.scan_id == row.id, GdriveScanItem.reason.like("error:%"),
                ).scalar()
                result.append(GdriveScanInfo(
                    **{key: value for key, value in row.to_dict().items() if key not in {"config", "active_key"}},
                    queued=len(statuses), completed=statuses.count("completed"),
                    failed=submission_errors + sum(value in ("failed", "cancelled") for value in statuses),
                    active=sum(value in _ACTIVE_STATUSES for value in statuses),
                ))
            return result

    @classmethod
    def detail(cls, scan_id: int) -> Optional[GdriveScanDetail]:
        """读取扫描详情和最近五百项，返回真实清单总数"""
        with SessionFactory() as db:
            row = db.get(GdriveScan, scan_id)
            if not row:
                return None
            scan = cls.list_scans(scan_id=scan_id)[0]
            items = []
            query = db.query(GdriveScanItem, GdriveTask).outerjoin(
                GdriveTask, GdriveScanItem.task_id == GdriveTask.id,
            ).filter(GdriveScanItem.scan_id == scan_id)
            total = query.count()
            for item, task in query.order_by(GdriveScanItem.id).limit(500).all():
                items.append(GdriveScanItemInfo(
                    **{key: value for key, value in item.to_dict().items() if key not in {"scan_id", "fingerprint"}},
                    status=task.status if task else None, error_message=task.error_message if task else None,
                    stage=task.stage if task else None, progress=task.progress or 0 if task else 0,
                ))
            rule = row.config.get("rule_snapshot")
            return GdriveScanDetail(scan=scan, items=items, total_items=total,
                                    config=GdriveDirectoryConfig(**row.config),
                                    rule=GdriveRuleInfo(**rule) if rule else None)
