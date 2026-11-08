"""上传阶段通知、持久化去重和二十四小时汇总的离线回归。"""
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.chain.upload import UploadChain
from app.db.gdrive_task_history_oper import GdriveTaskHistoryOper
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_task_history import GdriveTaskHistory
from app.modules.telegram.telegram import Telegram
from app.schemas.gdrive import GdriveUploadSettings
from app.schemas.types import SystemConfigKey


@pytest.fixture
def notifications(tmp_path, monkeypatch):
    """隔离数据库、时钟、通知发送和运行配置。"""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/notifications.db")
    GdriveTask.__table__.create(engine)
    GdriveTaskHistory.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    tasks = GdriveTaskOper(db=session)
    history = GdriveTaskHistoryOper(db=session)
    data = {}
    def get(key):
        return copy.deepcopy(data.get(key))
    def set_value(key, value):
        data[key] = copy.deepcopy(value)
        return True
    config = GdriveUploadSettings(notifications_enabled=True, daily_report_enabled=True, notification_source="test")
    monkeypatch.setattr(UploadChain, "get_settings", staticmethod(lambda: config))
    monkeypatch.setattr("app.chain.upload.SystemConfigOper", lambda: SimpleNamespace(get=get, set=set_value))
    monkeypatch.setattr("app.chain.upload.GdriveTaskOper", lambda: tasks)
    monkeypatch.setattr("app.chain.upload.GdriveTaskHistoryOper", lambda: history)
    chain = object.__new__(UploadChain)
    chain.messageoper = Mock()
    chain.eventmanager = Mock()
    sender = Mock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(UploadChain, "send_direct_message", lambda self, message: sender(message))
    monkeypatch.setattr(UploadChain, "__init__", lambda self: None)
    monkeypatch.setattr(UploadChain, "_deliver_notification", lambda self, title, text, source: sender(title, text, source).success)
    clock = [1800000000.0]
    monkeypatch.setattr("app.chain.upload.time.time", lambda: clock[0])
    yield SimpleNamespace(tasks=tasks, history=history, session=session, data=data, sender=sender,
                          config=config, clock=clock, chain=chain)
    session.close()
    engine.dispose()


def _event(env, kind="upload_verified", when="2027-01-15 08:00:00", task=None):
    task = task or env.tasks.add(src_path="/media/<电影>&.mkv", archive_path="电影/大陆/<电影>&.mkv",
                                backend="api", source_size=1048576, status="completed", stage="completed",
                                upload_target={"path": "暂存/movie.mkv"}, strm_path="/strm/电影/movie.strm")
    GdriveTaskHistory.create_record(env.session, task_id=task.id, event_type=kind,
                                   to_status="completed", to_stage="completed", created_at=when)
    return task


def test_first_success_is_counted_once_across_windows(notifications):
    """跨窗口的重试不重复计量，左闭右开排除结束边界。"""
    env = notifications
    task = _event(env, when="2027-01-15 00:00:00")
    _event(env, when="2027-01-16 00:00:00", task=task)
    _event(env, when="2027-01-16 00:00:00")
    rows = env.history.list_notification_events(start="2027-01-15 00:00:00", end="2027-01-16 00:00:00")
    assert len(rows) == 1 and rows[0].task_id == task.id
    later = env.history.list_notification_events(start="2027-01-16 00:00:00", end="2027-01-17 00:00:00")
    assert len(later) == 1 and later[0].task_id != task.id


def test_stage_receipts_survive_new_oper_and_failed_delivery(notifications):
    """失败不记成功回执，新 Oper 读取持久化回执后不会重发。"""
    env = notifications
    task = _event(env)
    for kind in ["cloud_archived", "strm_generated", "cloud_archived"]:
        _event(env, kind, task=task)
    env.sender.return_value = SimpleNamespace(success=False)
    UploadChain.notify_pending()
    assert len(env.history.list_notification_events(pending=True)) == 3
    env.sender.return_value = SimpleNamespace(success=True)
    UploadChain.notify_pending()
    calls = env.sender.call_count
    UploadChain.notify_pending()
    assert env.sender.call_count == calls
    assert not GdriveTaskHistoryOper(db=env.session).list_notification_events(pending=True)


def test_failure_is_separate_per_retry_without_leaking_error(notifications):
    """每次失败各有记录，但原始异常中的凭据不发送。"""
    env = notifications
    task = _event(env, "failed")
    _event(env, "failed", task=task)
    env.tasks.update(task.id, error_message="token=private-secret")
    UploadChain.notify_pending()
    assert env.sender.call_count == 2
    assert "private-secret" not in str(env.sender.call_args_list)


def test_template_escapes_titles_paths_and_separates_scan_status(notifications):
    """所有动态字符串转义，STRM 成功不谎报播放或扫库完成。"""
    env = notifications
    _event(env, "strm_generated")
    UploadChain.notify_pending()
    text = env.sender.call_args.args[1]
    assert "&lt;电影&gt;&amp;" in text and "<电影>" not in text
    assert "不代表" in text


def test_report_boundary_zero_and_restart(notifications):
    """未满二十四小时不发，零上传也发，窗口推进后不重复。"""
    env = notifications
    env.config.notifications_enabled = False
    UploadChain.notify_pending()
    env.clock[0] += 86399
    UploadChain.notify_pending()
    assert not env.sender.called
    env.clock[0] += 1
    UploadChain.notify_pending()
    assert env.sender.call_count == 1
    assert "0 字节" in env.sender.call_args.args[1]
    UploadChain.notify_pending()
    assert env.sender.call_count == 1


def test_report_splits_and_resumes_after_failed_part(notifications):
    """分段发送失败只重试未确认分段，全部成功后才推进窗口。"""
    env = notifications
    env.config.notifications_enabled = False
    UploadChain.notify_pending()
    from datetime import datetime
    when = datetime.fromtimestamp(env.clock[0]).strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(45):
        _event(env, when=when)
    env.clock[0] += 86400
    env.sender.side_effect = [SimpleNamespace(success=True), SimpleNamespace(success=False)]
    UploadChain.notify_pending()
    state = env.data[SystemConfigKey.GdriveUploadNotificationState]
    assert state["report"]["next_part"] == 1
    assert len(state["report"]["parts"]) > 1
    assert "47185920 字节" in state["report"]["parts"][0]
    env.sender.side_effect = None
    env.sender.reset_mock()
    UploadChain.notify_pending()
    assert "2/" in env.sender.call_args_list[0].args[0]
    assert "report" not in env.data[SystemConfigKey.GdriveUploadNotificationState]
    assert all(len(call.args[1]) <= 2800 for call in env.sender.call_args_list)


def test_send_only_telegram_does_not_poll_or_change_bot_menu(monkeypatch):
    """仅发送实例不读取更新、不获取机器人资料、不注册命令。"""
    bot = Mock()
    monkeypatch.setitem(Telegram.__init__.__globals__, "TeleBot", lambda *args, **kwargs: bot)
    client = Telegram(TELEGRAM_TOKEN="fake", TELEGRAM_CHAT_ID="fake", TELEGRAM_RECEIVE_ENABLED=False)
    assert client.bot is bot and client._polling_thread is None
    bot.get_me.assert_not_called()
    bot.infinity_polling.assert_not_called()


def test_notifications_disabled_does_not_send_or_initialize(notifications):
    """关闭通知及汇总时不建立报告窗口，也不发送。"""
    env = notifications
    env.config.notifications_enabled = False
    env.config.daily_report_enabled = False
    _event(env)
    UploadChain.notify_pending()
    assert not env.sender.called and not env.data


def test_confirmed_delivery_is_recorded_without_second_send(monkeypatch):
    """确认接口成功后写通知历史及事件，不再调用排队发送接口。"""
    chain = object.__new__(UploadChain)
    chain.messageoper = Mock()
    chain.eventmanager = Mock()
    sender = Mock(return_value=SimpleNamespace(success=True))
    monkeypatch.setattr(UploadChain, "send_direct_message", lambda self, message: sender(message))
    assert chain._deliver_notification("阶段测试", "安全正文", "test")
    chain.messageoper.add.assert_called_once()
    chain.eventmanager.send_event.assert_called_once()
    sender.assert_called_once()
    sender.return_value = None
    chain.messageoper.reset_mock()
    assert not chain._deliver_notification("阶段测试", "安全正文", "test")
    chain.messageoper.add.assert_not_called()
