import hashlib
import importlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.api.endpoints import gdrive as endpoint
from app.chain.gdrive import GdriveChain
from app.chain.upload import UploadChain
from app.db.gdrive_task_oper import GdriveTaskOper
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_task_history import GdriveTaskHistory
from app.db.user_oper import get_current_active_manage_user, get_current_active_superuser
from app.helper.upload import UploadHelper
from app.schemas.gdrive import (
    GdriveSettings, GdriveUploadCreate, GdriveUploadCredential, GdriveUploadSettings, GdriveUploadTarget,
)


@pytest.fixture
def upload(tmp_path, monkeypatch):
    """所有凭据、任务及协议模拟均在私有临时目录内完成"""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/upload.db")
    GdriveTask.__table__.create(engine)
    GdriveTaskHistory.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.helper.upload.settings", SimpleNamespace(CONFIG_PATH=tmp_path, PROXY=None))
    monkeypatch.setattr("app.chain.upload.global_vars", SimpleNamespace(is_system_stopped=False))
    monkeypatch.setattr("app.chain.upload._RUNNING", {})
    monkeypatch.setattr("app.chain.upload._CLOSING", False)
    oper = GdriveTaskOper(db=factory())
    monkeypatch.setattr("app.chain.upload.GdriveTaskOper", lambda: oper)
    config = GdriveUploadSettings(enabled=True, concurrency=4, accounts=[
        {"id": str(number), "name": f"账号 {number}", "concurrency": 1} for number in range(4)
    ])
    monkeypatch.setattr(UploadChain, "get_settings", staticmethod(lambda: config))
    jobs = []
    monkeypatch.setattr("app.chain.upload.ThreadHelper", lambda: SimpleNamespace(submit=lambda *args: jobs.append(args)))
    for account in config.accounts:
        UploadHelper.save_credential(account.id, GdriveUploadCredential(token='{"access_token":"private-test-token"}'))
    target = GdriveUploadTarget(account_ids=["0", "1", "2", "3"], drive_id="drive", folder_id="folder", path="电影/movie.mkv")
    yield SimpleNamespace(root=tmp_path, oper=oper, config=config, jobs=jobs, target=target, factory=factory)
    oper._db.close()
    engine.dispose()


def _create(upload, name="movie.mkv", **kwargs):
    source = upload.root / name
    source.write_bytes(b"media-content")
    target = upload.target.model_copy(update={"path": Path(upload.target.path).with_name(name).as_posix()})
    task_id, error = UploadChain.create_task(GdriveUploadCreate(source_path=str(source), target=target, **kwargs))
    assert not error
    return task_id, source


def _response(code=200, payload=None, headers=None):
    return SimpleNamespace(status_code=code, headers=headers or {}, json=lambda: payload or {}, close=lambda: None)


def _remote(task_id, data=b"media-content"):
    return {"id": "remote-file", "name": "movie.mkv", "size": str(len(data)), "md5Checksum": hashlib.md5(data).hexdigest(),
            "parents": ["folder"], "driveId": "drive", "appProperties": {"moviepilot_upload": str(task_id)}}


def test_account_selection_and_global_per_account_concurrency(upload):
    """四账号分别取得一个任务，其余保留排队；降低限制不再启动新任务"""
    for number in range(6):
        _create(upload, f"{number}.mkv")
    assert len(upload.jobs) == 4
    selected = [upload.oper.get(number).upload_account_id for number in range(1, 5)]
    assert set(selected) == {"0", "1", "2", "3"}
    assert upload.oper.get(5).upload_account_id is None
    upload.config.concurrency = 2
    UploadChain.dispatch()
    assert len(upload.jobs) == 4


def test_only_selected_account_is_used_and_waits_at_its_limit(upload):
    """选择两个账号时只使用所选账号，账号并发上限独立生效"""
    upload.target.account_ids = ["1", "3"]
    for number in range(3):
        _create(upload, f"selected-{number}.mkv")
    assert len(upload.jobs) == 2
    assert {upload.oper.get(number).upload_account_id for number in (1, 2)} == {"1", "3"}


def test_same_cloud_target_waits_even_with_different_accounts(upload):
    """同名目标必须串行核对云端状态，避免不同账号同时创建重名成品"""
    first, _ = _create(upload)
    source = upload.root / "other.mkv"
    source.write_bytes(b"other")
    second, error = UploadChain.create_task(GdriveUploadCreate(source_path=str(source), target=upload.target))
    assert second and not error and len(upload.jobs) == 1
    assert upload.oper.get(first).upload_account_id == "0"
    assert upload.oper.get(second).upload_account_id is None


def test_cancel_cannot_be_overwritten_by_late_progress(upload):
    """取消之后到达的进度和完成更新必须失败，重试仍可显式恢复"""
    task_id, _ = _create(upload)
    assert UploadChain.cancel_task(task_id)
    with pytest.raises(RuntimeError, match="已取消"):
        UploadChain._update_task(task_id, status="completed", progress=100)
    assert upload.oper.get(task_id).status == "cancelled"


def test_empty_source_is_rejected_before_network(upload):
    """媒体空文件在入队前拒绝，不创建 Google 上传会话"""
    source = upload.root / "empty.mkv"
    source.touch()
    task_id, error = UploadChain.create_task(GdriveUploadCreate(source_path=str(source), target=upload.target))
    assert task_id is None and "为空" in error and not upload.jobs


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize("status", [None, 503])
def test_uncertain_write_is_not_replayed(upload, monkeypatch, method, status):
    """写入响应不确定时只发送一次，下次任务重试必须先核对云端状态"""
    calls = []
    def request(self, *args, **kwargs):
        calls.append(kwargs)
        return None if status is None else _response(status)
    monkeypatch.setattr("app.helper.upload.RequestUtils.request", request)
    with pytest.raises(RuntimeError, match="结果未确认"):
        UploadHelper("0")._request(method, "https://www.googleapis.com/drive/v3/files", json={"name": "test"})
    assert len(calls) == 1
    assert calls[0]["headers"]["Content-Type"] == "application/json; charset=UTF-8"


def test_credentials_are_private_and_never_serialized(upload):
    """私有文件权限受限，公开设置及 Pydantic 序列化不泄漏 Token"""
    path = UploadHelper._credential_path("0")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    response = endpoint.get_upload_settings(None)
    assert "private-test-token" not in response.model_dump_json()
    assert response.data["credential_status"]["0"]
    assert "private-test-token" not in GdriveUploadCredential(token="private-test-token").model_dump_json()
    with pytest.raises(ValueError):
        UploadHelper.save_credential("../escape", GdriveUploadCredential(token='{"access_token":"value"}'))


@pytest.mark.parametrize("changes", [
    {"path": "../escape.mkv"}, {"path": "/absolute.mkv"}, {"account_ids": ["0", "0"]},
    {"account_ids": ["../escape"]}, {"drive_id": "drive?evil=true"}, {"folder_id": ""},
])
def test_target_rejects_invalid_paths_and_ids(upload, changes):
    """端点模型拒绝路径逃逸、重复账号和无效资源标识"""
    with pytest.raises(ValidationError):
        GdriveUploadTarget(**{**upload.target.model_dump(), **changes})


def test_direct_upload_does_not_require_mount_and_defaults_to_keep_source(upload, monkeypatch):
    """直接上传与挂载开关分离，默认保源并保存目标快照"""
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: GdriveSettings(enabled=False)))
    task_id, source = _create(upload)
    upload.target.path = "changed.mkv"
    task = upload.oper.get(task_id)
    assert task.backend == "api" and not task.remove_source
    assert task.upload_target["path"] == "电影/movie.mkv"
    assert source.exists() and not list(upload.root.rglob("*.strm"))


def test_cloud_upload_resume_queries_confirmed_offset_and_dedupes(upload, monkeypatch):
    """模拟 Google 分块协议，断点从服务端偏移恢复且重试不会创建第二个文件"""
    source = upload.root / "protocol.mkv"
    source.write_bytes(b"abcdef")
    target = upload.target.model_copy(update={"path": "movie.mkv"})
    calls = []
    completed = {"value": False}
    metadata = _remote(77, b"abcdef")
    monkeypatch.setattr(UploadHelper, "check_target", lambda *_: {})
    monkeypatch.setattr(UploadHelper, "_list", lambda *_: [])

    def request(self, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("generateIds"):
            return _response(payload={"ids": ["remote-file"]})
        if method == "get" and url.endswith("remote-file"):
            return _response(payload=metadata) if completed["value"] else _response(404)
        if method == "post":
            return _response(headers={"Location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=session"})
        if kwargs["headers"]["Content-Range"] == "bytes */6":
            return _response(308, headers={"Range": "bytes=0-2"})
        assert kwargs["data"] == b"def"
        assert kwargs["headers"]["Content-Range"] == "bytes 3-5/6"
        completed["value"] = True
        return _response(payload=metadata)

    monkeypatch.setattr(UploadHelper, "_request", request)
    progress = []
    result = UploadHelper("0").upload(77, source, target, 1024, progress.append)
    assert result["id"] == "remote-file" and progress == [3, 6]
    assert len([call for call in calls if call[0] == "post"]) == 1
    assert UploadHelper("0").upload(77, source, target, 1024, progress.append)["id"] == "remote-file"
    assert len([call for call in calls if call[0] == "post"]) == 1
    state_path = UploadHelper._state_path(77)
    old_state = json.loads(state_path.read_text())
    for key in ("metadata_enabled", "refresh_server", "refresh_library_id"):
        old_state["identity"]["target"].pop(key)
    UploadHelper._save_private(state_path, old_state)
    assert UploadHelper("0").verify_uploaded(77, target, "remote-file")["id"] == "remote-file"
    assert UploadHelper("0").upload(77, source, target, 1024, progress.append)["id"] == "remote-file"
    assert len([call for call in calls if call[0] == "post"]) == 1


@pytest.mark.parametrize("expired_status", [404, 410])
def test_expired_session_restarts_with_same_file_id(upload, monkeypatch, expired_status):
    """过期会话重建时沿用预分配 ID，完成响应必须再次查询云端核验"""
    source = upload.root / "movie.mkv"
    source.write_bytes(b"abcdef")
    target = upload.target.model_copy(update={"path": "movie.mkv"})
    state_path = UploadHelper._state_path(77)
    UploadHelper._save_private(state_path, {
        "identity": {"source": str(source), "size": 6, "md5": hashlib.md5(b"abcdef").hexdigest(),
                     "account": "0", "target": target.model_dump()},
        "parent": "folder", "file_id": "remote-file",
        "session": "https://www.googleapis.com/upload/drive/v3/files?upload_id=expired",
    })
    calls = []
    completed = False
    monkeypatch.setattr(UploadHelper, "check_target", lambda *_: {})

    def request(self, method, url, **kwargs):
        nonlocal completed
        calls.append((method, url))
        if method == "get":
            return _response(payload=_remote(77, b"abcdef")) if completed else _response(404)
        if url.endswith("upload_id=expired"):
            return _response(expired_status)
        if method == "post":
            assert kwargs["json"]["id"] == "remote-file"
            return _response(headers={"Location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=new"})
        if kwargs["data"] == b"":
            return _response(308)
        assert kwargs["data"] == b"abcdef"
        completed = True
        return _response(payload={})

    monkeypatch.setattr(UploadHelper, "_request", request)
    assert UploadHelper("0").upload(77, source, target, 1024, lambda _: None)["id"] == "remote-file"
    assert sum(method == "post" for method, _ in calls) == 1
    assert calls[-1] == ("get", "https://www.googleapis.com/drive/v3/files/remote-file")


@pytest.mark.parametrize("field,value", [("name", "renamed.mkv"), ("id", "other-file"),
                                         ("md5Checksum", "wrong"), ("parents", ["other-folder"])])
def test_remote_identity_mismatch_prevents_success(upload, monkeypatch, field, value):
    """最终核验拒绝改名、错误文件编号、内容或目录，不能据此删除源文件"""
    target = upload.target
    UploadHelper._save_private(UploadHelper._state_path(77), {
        "identity": {"size": 13, "md5": hashlib.md5(b"media-content").hexdigest(),
                     "account": "0", "target": target.model_dump()},
        "parent": "folder", "file_id": "remote-file",
    })
    monkeypatch.setattr(UploadHelper, "_metadata", lambda *_: {**_remote(77), field: value})
    with pytest.raises(IOError, match="保留源文件"):
        UploadHelper("0").verify_uploaded(77, target, "remote-file")


def test_cancel_before_quarantine_deletion_restores_source(upload, monkeypatch):
    """隔离复核期间取消时必须恢复源文件，不执行最终删除"""
    task_id, source = _create(upload, remove_source=True)
    original = UploadChain._verify_source

    def verify(task, path):
        original(task, path)
        if path != source:
            assert UploadChain.cancel_task(task_id)

    monkeypatch.setattr(UploadChain, "_verify_source", verify)
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: _remote(task_id))
    UploadChain._run_task(task_id, "0", 1024)
    assert source.read_bytes() == b"media-content"
    assert upload.oper.get(task_id).status == "cancelled"
    assert not source.with_name(f".{source.name}.{task_id}.gdrive-upload.delete").exists()


def test_http_refresh_retains_chunk_headers_and_rejects_redirects(upload, monkeypatch):
    """401 自动刷新后重发相同分块头，禁止跟随跳转或向任意主机发送 Token"""
    helper = UploadHelper("0")
    calls = []
    refreshes = []
    monkeypatch.setattr(helper, "_access_token", lambda refresh=False: refreshes.append(refresh) or "private")
    responses = iter([_response(401), _response(308)])

    def request(self, method, url, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr("app.helper.upload.RequestUtils.request", request)
    helper._request("put", "https://www.googleapis.com/upload/drive/v3/files?upload_id=test",
                    headers={"Content-Range": "bytes 0-2/6"}, data=b"abc")
    assert len(calls) == 2 and True in refreshes
    assert all(call["headers"]["Content-Range"] == "bytes 0-2/6" for call in calls)
    assert all(call["verify"] and not call["allow_redirects"] for call in calls)
    with pytest.raises(ValueError):
        helper._request("put", "https://evil.example/upload")


def test_checksum_failure_preserves_source_and_skips_strm(upload, monkeypatch):
    """云端校验失败不会进入 STRM 或删除源文件"""
    upload.target.strm_prefix = "/drive"
    task_id, source = _create(upload, remove_source=True, strm_root=str(upload.root / "library"))
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: (_ for _ in ()).throw(IOError("云端校验失败")))
    UploadChain._run_task(task_id, "0", 1024)
    assert source.exists() and upload.oper.get(task_id).status == "failed"
    assert not (upload.root / "library").exists()


def test_success_strm_failure_retry_and_safe_source_removal(upload, monkeypatch):
    """STRM 失败先保源，重试成功后写普通文件并删除原样测试源"""
    upload.target.strm_prefix = "/media/drive"
    root = upload.root / "library"
    task_id, source = _create(upload, remove_source=True, strm_root=str(root))
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: _remote(task_id))
    original = UploadHelper.write_strm
    monkeypatch.setattr(UploadHelper, "write_strm", lambda *_: (_ for _ in ()).throw(OSError("不可写")))
    UploadChain._run_task(task_id, "0", 1024)
    assert source.exists() and upload.oper.get(task_id).status == "failed"
    monkeypatch.setattr(UploadHelper, "write_strm", original)
    assert UploadChain.retry_task(task_id)[0]
    UploadChain._run_task(task_id, "0", 1024)
    output = root / "电影/movie.strm"
    assert output.read_text() == "/media/drive/电影/movie.mkv\n"
    assert not output.is_symlink() and not source.exists()
    assert upload.oper.get(task_id).status == "completed"


def test_source_replacement_after_cloud_upload_is_preserved(upload, monkeypatch):
    """上传期间源文件替换时保留替换内容，不生成 STRM 或删除"""
    task_id, source = _create(upload, remove_source=True)

    def replace(*_):
        source.unlink()
        source.write_bytes(b"replaced")
        return _remote(task_id)

    monkeypatch.setattr(UploadHelper, "upload", replace)
    UploadChain._run_task(task_id, "0", 1024)
    assert upload.oper.get(task_id).status == "failed" and source.read_bytes() == b"replaced"


def test_deletion_crash_recovery_checks_remote_without_reupload(upload, monkeypatch):
    """删除后完成记录前崩溃，可通过云端校验恢复，不要求重传不存在的源文件"""
    task_id, source = _create(upload, remove_source=True)
    upload.oper.update(task_id, stage="deleting_source", remote_file_id="remote-file")
    source.unlink()
    checks = []
    monkeypatch.setattr(UploadHelper, "verify_uploaded", lambda *args: checks.append(args) or _remote(task_id))
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: pytest.fail("不应重传"))
    UploadChain._run_task(task_id, "0", 1024)
    assert checks and upload.oper.get(task_id).status == "completed"


def test_cancelled_task_does_not_start_network(upload, monkeypatch):
    """等待任务取消后不会读取或创建云端资源"""
    task_id, source = _create(upload)
    upload.oper.update(task_id, status="cancelled")
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: pytest.fail("不应请求云端"))
    UploadChain._run_task(task_id, "0", 1024)
    assert source.exists() and upload.oper.get(task_id).status == "cancelled"


def test_strm_metadata_precedes_scan_and_failed_scan_keeps_source(upload, monkeypatch):
    """先完成本地元数据再扫库，扫库失败保留源文件，重试复用上传结果"""
    upload.target.metadata_enabled = True
    upload.target.refresh_server = "Emby"
    upload.target.refresh_library_id = "5"
    upload.target.strm_prefix = "http://playback"
    root = upload.root / "strm"
    task_id, source = _create(upload, strm_root=str(root), remove_source=True)
    source.with_suffix(".nfo").write_text("<movie><title>电影</title></movie>")
    events = []
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: events.append("upload") or _remote(task_id))
    def refresh(*args, **kwargs):
        assert (root / "电影" / "movie.nfo").is_file()
        events.append("scan")
        return False
    monkeypatch.setattr(UploadChain, "__init__", lambda _: None)
    monkeypatch.setattr(UploadChain, "run_module", refresh)
    UploadChain._run_task(task_id, "0", 1024)
    assert upload.oper.get(task_id).status == "failed" and source.exists()
    assert events == ["upload", "scan"]
    monkeypatch.setattr(UploadChain, "run_module", lambda *args, **kwargs: True)
    UploadChain.retry_task(task_id)
    UploadChain._run_task(task_id, "0", 1024)
    assert upload.oper.get(task_id).status == "completed" and not source.exists()


def test_existing_task_without_metadata_defaults_is_reused(upload):
    """新目标字段补默认值后仍能复用旧版活动任务"""
    task_id, source = _create(upload)
    previous = dict(upload.oper.get(task_id).upload_target)
    for key in ("metadata_enabled", "refresh_server", "refresh_library_id"):
        previous.pop(key)
    upload.oper.update(task_id, upload_target=previous)
    repeated, error = UploadChain.create_task(GdriveUploadCreate(source_path=str(source), target=upload.target))
    assert repeated == task_id and not error


def _archive_state(upload, task_id=77):
    target = upload.target.model_copy(update={"path": "整理/movie.mkv", "archive_folder_id": "media",
                                               "archive_path": "电影/国产/movie.mkv"})
    UploadHelper._save_private(UploadHelper._state_path(task_id), {
        "identity": {"size": 13, "md5": hashlib.md5(b"media-content").hexdigest(),
                     "account": "0", "target": target.model_dump()},
        "parent": "folder", "file_id": "remote-file",
    })
    return target


def test_cloud_archive_lost_response_recovers_same_id_without_second_move(upload, monkeypatch):
    """移动成功但响应丢失后，重试只读确认原文件的新位置，不重传也不重发移动"""
    target = _archive_state(upload)
    remote = _remote(77)
    calls = []
    monkeypatch.setattr(UploadHelper, "check_target", lambda *_: {})
    monkeypatch.setattr(UploadHelper, "_parent", lambda *_: "final-folder")
    monkeypatch.setattr(UploadHelper, "_metadata", lambda *_: remote)
    monkeypatch.setattr(UploadHelper, "_list", lambda *_: [])
    def request(self, method, url, **kwargs):
        calls.append((method, kwargs))
        assert method == "patch" and kwargs["params"]["removeParents"] == "folder"
        assert kwargs["params"]["addParents"] == "final-folder"
        remote["parents"] = ["final-folder"]
        raise RuntimeError("结果未确认")
    monkeypatch.setattr(UploadHelper, "_request", request)
    helper = UploadHelper("0")
    with pytest.raises(RuntimeError, match="结果未确认"):
        helper.archive_uploaded(77, target, "remote-file")
    assert len(calls) == 1
    assert helper.archive_uploaded(77, target, "remote-file")["id"] == "remote-file"
    assert helper.verify_archived(77, target, "remote-file")["parents"] == ["final-folder"]
    assert len(calls) == 1
    assert json.loads(UploadHelper._state_path(77).read_text())["archived"]
    remote["parents"] = ["folder"]
    with pytest.raises(IOError, match="位置发生变化"):
        helper.verify_archived(77, target, "remote-file")


def test_cloud_archive_conflict_does_not_move_or_confirm_final_location(upload, monkeypatch):
    """归档重名冲突保留暂存文件，不能把上传完成误认为归档完成"""
    target = _archive_state(upload)
    monkeypatch.setattr(UploadHelper, "check_target", lambda *_: {})
    monkeypatch.setattr(UploadHelper, "_parent", lambda *_: "final-folder")
    monkeypatch.setattr(UploadHelper, "_metadata", lambda *_: _remote(77))
    monkeypatch.setattr(UploadHelper, "_list", lambda *_: [{"id": "other-file"}])
    monkeypatch.setattr(UploadHelper, "_request", lambda *_a, **_k: pytest.fail("冲突时不应移动"))
    with pytest.raises(ValueError, match="同名"):
        UploadHelper("0").archive_uploaded(77, target, "remote-file")
    with pytest.raises(IOError, match="归档位置未确认"):
        UploadHelper("0").verify_archived(77, target, "remote-file")


def test_cloud_archive_renames_only_after_upload_and_rejects_wrong_checksum(upload, monkeypatch):
    """归档允许规则改名，最终查询内容不符时不能确认成功"""
    target = _archive_state(upload).model_copy(update={"archive_path": "电影/国产/renamed.mkv"})
    state_path = UploadHelper._state_path(77)
    state = json.loads(state_path.read_text())
    state['identity']['target'] = target.model_dump()
    UploadHelper._save_private(state_path, state)
    remote = _remote(77)
    monkeypatch.setattr(UploadHelper, "check_target", lambda *_: {})
    monkeypatch.setattr(UploadHelper, "_parent", lambda *_: "final-folder")
    monkeypatch.setattr(UploadHelper, "_metadata", lambda *_: remote)
    monkeypatch.setattr(UploadHelper, "_list", lambda *_: [])
    def request(self, method, url, **kwargs):
        assert remote['parents'] == ['folder'] and remote['name'] == 'movie.mkv'
        assert kwargs['json'] == {'name': 'renamed.mkv'}
        remote.update(name='renamed.mkv', parents=['final-folder'], md5Checksum='wrong')
        return _response(payload=remote)
    monkeypatch.setattr(UploadHelper, "_request", request)
    with pytest.raises(IOError, match="校验失败"):
        UploadHelper('0').archive_uploaded(77, target, 'remote-file')
    assert not json.loads(state_path.read_text()).get('archived')
    remote['md5Checksum'] = hashlib.md5(b'media-content').hexdigest()
    assert UploadHelper('0').archive_uploaded(77, target, 'remote-file')['name'] == 'renamed.mkv'


def test_archived_missing_file_is_not_reuploaded(upload, monkeypatch):
    """进入归档后的文件缺失必须人工处理，不能把旧续传会话变成第二次上传"""
    source = upload.root / 'movie.mkv'
    source.write_bytes(b'media-content')
    target = _archive_state(upload)
    state_path = UploadHelper._state_path(77)
    state = json.loads(state_path.read_text())
    state['identity']['source'] = str(source)
    state['archive'] = {'parent': 'final-folder', 'name': 'movie.mkv'}
    UploadHelper._save_private(state_path, state)
    monkeypatch.setattr(UploadHelper, 'check_target', lambda *_: {})
    monkeypatch.setattr(UploadHelper, '_metadata', lambda *_: None)
    monkeypatch.setattr(UploadHelper, '_request', lambda *_a, **_k: pytest.fail('不能重传'))
    with pytest.raises(IOError, match='停止重传'):
        UploadHelper('0').upload(77, source, target, 1024, lambda _: None)


def test_archive_failure_blocks_strm_metadata_scan_and_deletion_then_retry(upload, monkeypatch):
    """归档失败时所有后处理停止；重试依次确认归档、生成 STRM、准备资料和扫库"""
    upload.target.archive_folder_id = "media"
    upload.target.archive_path = "电影/国产/movie.mkv"
    upload.target.strm_prefix = "http://playback/media"
    upload.target.metadata_enabled = True
    upload.target.refresh_server = "Emby"
    upload.target.refresh_library_id = "5"
    root = upload.root / "strm"
    task_id, source = _create(upload, strm_root=str(root), remove_source=True)
    source.with_suffix(".nfo").write_text("<movie><title>离线电影</title></movie>")
    events = []
    monkeypatch.setattr(UploadHelper, "upload", lambda *_: events.append("upload") or _remote(task_id))
    monkeypatch.setattr(UploadHelper, "archive_uploaded", lambda *_: (_ for _ in ()).throw(IOError("归档未确认")))
    UploadChain._run_task(task_id, "0", 1024)
    assert upload.oper.get(task_id).status == "failed" and source.exists() and not root.exists()
    monkeypatch.setattr(UploadHelper, "archive_uploaded", lambda *_: events.append("archive") or _remote(task_id))
    original = UploadHelper.write_strm
    def write(*args):
        assert events[-1] == "archive"
        events.append("strm")
        return original(*args)
    monkeypatch.setattr(UploadHelper, "write_strm", write)
    monkeypatch.setattr(UploadChain, "__init__", lambda _: None)
    def refresh(*_args, **_kwargs):
        assert (root / "电影/国产/movie.nfo").is_file()
        events.append("scan")
        return True
    monkeypatch.setattr(UploadChain, "run_module", refresh)
    assert UploadChain.retry_task(task_id)[0]
    UploadChain._run_task(task_id, "0", 1024)
    assert events == ["upload", "upload", "archive", "strm", "scan"]
    assert upload.oper.get(task_id).status == "completed" and not source.exists()
    assert (root / "电影/国产/movie.strm").read_text() == "http://playback/media/电影/国产/movie.mkv\n"


def test_final_archive_target_is_serialized_across_different_staging_paths(upload):
    """不同暂存路径但最终归档相同时也必须串行，避免多账号同时归档同名文件"""
    upload.target.archive_folder_id = "media"
    upload.target.archive_path = "电影/movie.mkv"
    _create(upload, "first.mkv")
    _create(upload, "second.mkv")
    assert len(upload.jobs) == 1


def test_upload_migration_preserves_old_rows_and_is_repeatable(tmp_path):
    """迁移为旧记录标记挂载后端，重复升级不破坏记录"""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/migration.db")
    module = importlib.import_module("database.versions.b8e2f6a1c9d3_gdrive_upload")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE gdrivetask (id INTEGER PRIMARY KEY, src_path TEXT)"))
        connection.execute(sa.text("INSERT INTO gdrivetask VALUES (1, '/original.mkv')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        row = connection.execute(sa.text("SELECT src_path, backend FROM gdrivetask")).one()
        assert tuple(row) == ("/original.mkv", "mount")
    engine.dispose()


@pytest.mark.asyncio
async def test_upload_endpoints_require_privileged_identity(upload):
    """普通管理用户可入队但不能修改凭据或上传配置，未登录不能读取账号"""
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/gdrive")

    def denied():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="denied")

    app.dependency_overrides[get_current_active_manage_user] = denied
    app.dependency_overrides[get_current_active_superuser] = denied
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/gdrive/upload/settings")).status_code == 403
        app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace(id=1)
        assert (await client.get("/gdrive/upload/settings")).status_code == 200
        response = await client.put("/gdrive/upload/settings", json=upload.config.model_dump())
        assert response.status_code == 403
        response = await client.put("/gdrive/upload/accounts/0/credential", json={"token": "secret"})
        assert response.status_code == 403
