from datetime import datetime, timedelta
import time
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.chain.gdrive import GdriveChain
from app.db import Base
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_task_history import GdriveTaskHistory
from app.schemas.gdrive import GdriveSettings
from app.schemas.types import GdriveTaskStatus


def _task_values(source_path: str) -> dict:
    """构造数据库任务所需的最小字段。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "src_path": source_path,
        "source_size": 10,
        "uploaded_size": 0,
        "status": GdriveTaskStatus.PENDING.value,
        "stage": GdriveTaskStatus.PENDING.value,
        "progress": 0,
        "retry_count": 0,
        "remove_source": False,
        "created_at": now,
        "updated_at": now,
    }


def test_task_state_history_records_creation_stage_failure_and_retry(tmp_path):
    """任务创建、阶段变化、失败和重试事件应持久化为可追溯历史。"""
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[GdriveTask.__table__, GdriveTaskHistory.__table__])
    session = sessionmaker(bind=engine)()
    try:
        task_id = GdriveTask.create_task(session, **_task_values(str(tmp_path / "movie.mkv")))
        assert GdriveTask.update_task_with_history(
            session,
            task_id,
            status=GdriveTaskStatus.UPLOADING.value,
            stage=GdriveTaskStatus.UPLOADING.value,
            progress=25,
        )
        assert GdriveTask.update_task_with_history(
            session,
            task_id,
            status=GdriveTaskStatus.FAILED.value,
            error_message="挂载返回 EIO",
            event_type="failed",
            detail="OSError: 挂载返回 EIO",
        )
        assert GdriveTask.update_task_with_history(
            session,
            task_id,
            status=GdriveTaskStatus.UPLOADING.value,
            stage=GdriveTaskStatus.UPLOADING.value,
            error_message=None,
            retry_count=1,
            event_type="retry",
            detail="第 1 次重试",
        )
        records = GdriveTaskHistory.list_by_task(session, task_id)
        assert [record.event_type for record in records] == [
            "created", "state_change", "failed", "retry"
        ]
        assert records[2].error_message == "挂载返回 EIO"
        assert records[3].from_status == GdriveTaskStatus.FAILED.value
        assert records[3].to_status == GdriveTaskStatus.UPLOADING.value
    finally:
        session.close()
        engine.dispose()


def test_resume_releases_expired_leases_before_submitting_all_tasks(monkeypatch):
    """服务重启恢复前应释放过期租约，并重新提交全部未完成任务。"""
    tasks = [
        SimpleNamespace(id=1, status=GdriveTaskStatus.UPLOADING.value, stage="uploading"),
        SimpleNamespace(id=2, status=GdriveTaskStatus.ARCHIVING.value, stage="archiving"),
    ]
    released = []
    submitted = []

    class FakeOper:
        def release_expired_leases(self, now):
            released.append(now)
            return 1

        def list_unfinished(self):
            return tasks

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True)
    ))
    monkeypatch.setattr(GdriveChain, "_update_task", staticmethod(
        lambda task_id, **kwargs: True
    ))
    monkeypatch.setattr(GdriveChain, "start_task", classmethod(
        lambda cls, task_id: submitted.append(task_id)
    ))
    monkeypatch.setattr(GdriveChain, "__init__", lambda self: None)

    GdriveChain.resume_pending_tasks()

    assert released
    assert submitted == [1, 2]


def test_run_task_defers_until_existing_lease_expires(monkeypatch):
    """重启进程遇到有效旧租约时不抢占，并应安排租约到期后的恢复。"""
    lease_until = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 30))
    task = SimpleNamespace(
        id=7,
        status=GdriveTaskStatus.UPLOADING.value,
        lease_until=lease_until,
    )
    scheduled = []

    class FakeOper:
        def get(self, task_id):
            assert task_id == task.id
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "_renew_lease", staticmethod(lambda task_id: False))
    monkeypatch.setattr(GdriveChain, "_update_task", staticmethod(
        lambda task_id, **kwargs: True
    ))
    monkeypatch.setattr(GdriveChain, "_schedule_recovery", classmethod(
        lambda cls, task_id, delay: scheduled.append((task_id, delay))
    ))

    GdriveChain()._run_task(task.id)

    assert scheduled[0][0] == task.id
    assert 0 < scheduled[0][1] <= 32


def test_recovery_timer_resubmits_task_after_lease_expiry(monkeypatch):
    """租约到期后的恢复定时器应重新提交任务。"""
    submitted = []
    monkeypatch.setattr(GdriveChain, "start_task", classmethod(
        lambda cls, task_id: submitted.append(task_id)
    ))

    GdriveChain._run_recovery_timer(8)

    assert submitted == [8]


def test_run_task_retries_when_lease_was_released_during_claim(monkeypatch):
    """领取失败后若旧租约刚被释放，仍应安排立即重试而不是遗留任务。"""
    task = SimpleNamespace(
        id=10,
        status=GdriveTaskStatus.UPLOADING.value,
        lease_until=None,
    )
    scheduled = []

    class FakeOper:
        def get(self, task_id):
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "_renew_lease", staticmethod(lambda task_id: False))
    monkeypatch.setattr(GdriveChain, "_update_task", staticmethod(lambda task_id, **kwargs: True))
    monkeypatch.setattr(GdriveChain, "_schedule_recovery", classmethod(
        lambda cls, task_id, delay: scheduled.append((task_id, delay))
    ))

    GdriveChain()._run_task(task.id)

    assert scheduled == [(task.id, 0.1)]


def test_recovery_timer_records_submission_failure(monkeypatch):
    """租约到期后的恢复提交失败应留下可追踪历史。"""
    updates = []

    def fail_start(cls, task_id):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(GdriveChain, "start_task", classmethod(fail_start))
    monkeypatch.setattr(GdriveChain, "_update_task", staticmethod(
        lambda task_id, **kwargs: updates.append((task_id, kwargs)) or True
    ))

    GdriveChain._run_recovery_timer(9)

    assert updates == [(9, {
        "event_type": "recovery_failed",
        "detail": "租约到期后提交恢复任务失败：executor unavailable",
    })]


def test_run_task_schedules_recovery_after_lease_loss(monkeypatch):
    """执行中丢失租约时应保留未完成阶段并安排后续恢复。"""
    task = SimpleNamespace(
        id=11,
        status=GdriveTaskStatus.UPLOADING.value,
        stage=GdriveTaskStatus.UPLOADING.value,
        lease_until=None,
    )
    scheduled = []
    updates = []

    class FakeOper:
        def get(self, task_id):
            return task

        def owns(self, task_id, owner):
            return False

        def release(self, task_id, owner):
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "_renew_lease", staticmethod(lambda task_id: True))
    monkeypatch.setattr(GdriveChain, "_process_task", lambda self, task_id: (_ for _ in ()).throw(
        RuntimeError("lease lost")
    ))
    monkeypatch.setattr(GdriveChain, "_update_task", staticmethod(
        lambda task_id, **kwargs: updates.append((task_id, kwargs)) or True
    ))
    monkeypatch.setattr(GdriveChain, "_schedule_recovery", classmethod(
        lambda cls, task_id, delay: scheduled.append((task_id, delay))
    ))

    GdriveChain()._run_task(task.id)

    assert scheduled == [(task.id, 0.1)]
    assert updates == [(task.id, {
        "event_type": "recovery_deferred",
        "detail": "任务租约丢失，停止当前文件操作并等待重新领取",
    })]
