import importlib
import os
from datetime import datetime, timedelta, timezone
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
from app.db import Base
from app.db.gdrive_directory_oper import GdriveDirectoryOper as Oper
from app.db.models.gdrive_directory import GdriveDirectory
from app.db.models.gdrive_scan import GdriveScan
from app.db.models.gdrive_scan_item import GdriveScanItem
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_task_history import GdriveTaskHistory
from app.db.models.gdrive_media import GdriveMedia
from app.db.user_oper import get_current_active_manage_user, get_current_active_superuser
from app.schemas.gdrive import GdriveDirectoryConfig, GdriveSettings
from app.schemas.gdrive import GdriveCategoryPolicy
from app.helper.gdrive import GdriveHelper


@pytest.fixture
def directory(tmp_path, monkeypatch):
    """使用独立数据库和本地临时目录，所有挂载和后台提交均隔离"""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/test.db")
    for model in (GdriveDirectory, GdriveScan, GdriveScanItem, GdriveTask, GdriveTaskHistory, GdriveMedia):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.gdrive_directory_oper.SessionFactory", factory)
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    source.mkdir()
    mount.mkdir()
    settings = GdriveSettings(enabled=True, mount_path=str(mount), remove_source=False)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: settings))
    monkeypatch.setattr("pathlib.Path.is_mount", lambda path: path == mount)
    monkeypatch.setattr("app.chain.gdrive.global_vars", SimpleNamespace(is_system_stopped=False))
    monkeypatch.setattr(GdriveChain, "_submit_scan", lambda scan_id, dry_run: GdriveChain._scan_directory(scan_id, dry_run))
    yield SimpleNamespace(
        source=source, mount=mount, factory=factory, settings=settings,
        config=GdriveDirectoryConfig(name="测试", source_path=str(source), archive_path=str(mount / "media"), settle_seconds=0),
    )
    engine.dispose()


def _save(directory, **kwargs):
    return Oper.save(directory.config.model_copy(update=kwargs))


def test_saved_task_is_disabled_and_persistent(directory):
    """新任务停用且保存后可以重新读取，不会启动扫描"""
    task = _save(directory)
    assert not task.enabled and task.next_run_at is None
    assert Oper.get(task.id).name == "测试"
    assert Oper.list_scans() == []
    assert Oper.list()[0].id == task.id


def test_direct_upload_directory_preview_and_queue_without_mount(directory, monkeypatch):
    """直接上传复用分类扫描且不依赖挂载，总开关独立并冻结所选账号和云端路径"""
    from app.chain.upload import UploadChain
    from app.schemas.gdrive import GdriveUploadSettings, GdriveUploadTarget

    target = GdriveUploadTarget(account_ids=["one", "three"], drive_id="shared", folder_id="folder", path="媒体")
    directory.settings.enabled = False
    monkeypatch.setattr("pathlib.Path.is_mount", lambda _: False)
    monkeypatch.setattr(UploadChain, "get_settings", lambda: GdriveUploadSettings(enabled=True))
    submitted = []
    monkeypatch.setattr(UploadChain, "create_task", lambda value, **kwargs: submitted.append((value, kwargs)) or (100, ""))
    (directory.source / "movie.mkv").write_bytes(b"media")
    saved = _save(directory, enabled=True, upload_target=target)
    scan_id, error = GdriveChain.scan_directory(saved.id, dry_run=True)
    assert not error and Oper.detail(scan_id).scan.status == "previewed"
    assert submitted == []
    scan_id, error = GdriveChain.scan_directory(saved.id, dry_run=False)
    assert not error and submitted
    value, kwargs = submitted[0]
    assert value.target.path == "媒体/movie.mkv"
    assert value.target.account_ids == ["one", "three"]
    assert kwargs["dedupe_key"] and not value.remove_source


def test_directory_uploads_original_structure_before_cloud_classification(directory, monkeypatch):
    """本地整理目录先原样上传 drmedia，分类结果单独冻结供上传后移动至 media"""
    from app.chain.upload import UploadChain
    from app.schemas.gdrive import GdriveUploadSettings, GdriveUploadTarget

    target = GdriveUploadTarget(account_ids=["one"], drive_id="shared", folder_id="drmedia",
                                path="暂存", archive_folder_id="media", archive_path="归档")
    monkeypatch.setattr(UploadChain, "get_settings", lambda: GdriveUploadSettings(enabled=True))
    submitted = []
    monkeypatch.setattr(UploadChain, "create_task", lambda value, **kwargs: submitted.append(value) or (100, ""))
    monkeypatch.setattr(GdriveHelper, "directory_relative", lambda *_: __import__("pathlib").Path("电影/国产/Final/movie.mkv"))
    monkeypatch.setattr(GdriveChain, "_offline_media", lambda *_: {})
    (directory.source / "已整理").mkdir()
    (directory.source / "已整理/movie.mkv").write_bytes(b"media")
    saved = _save(directory, enabled=True, upload_target=target, classification_mode="custom")
    scan_id, error = GdriveChain.scan_directory(saved.id, dry_run=False)
    assert not error and submitted, Oper.detail(scan_id)
    assert submitted[0].target.folder_id == "drmedia"
    assert submitted[0].target.path == "暂存/已整理/movie.mkv"
    assert submitted[0].target.archive_folder_id == "media"
    assert submitted[0].target.archive_path == "归档/电影/国产/Final/movie.mkv"


def test_transfer_event_queues_staging_and_final_category_separately(directory, monkeypatch):
    """真实整理事件入口绕过静置等待并把原路径与最终云端分类路径分别入队"""
    from app.chain.upload import UploadChain
    from app.schemas.gdrive import GdriveUploadSettings, GdriveUploadTarget

    source = directory.source / 'Already.Organized.2026.mkv'
    source.write_bytes(b'media')
    target = GdriveUploadTarget(account_ids=['one'], drive_id='shared', folder_id='drmedia',
                                archive_folder_id='media')
    policy = GdriveCategoryPolicy(movie=[{'name': '华语电影', 'conditions': {'original_language': 'zh'}}])
    _save(directory, enabled=True, settle_seconds=300, classification_mode='custom', category_policy=policy,
          upload_target=target)
    monkeypatch.setattr(UploadChain, 'get_settings', lambda: GdriveUploadSettings(enabled=True))
    submitted = []
    monkeypatch.setattr(UploadChain, 'create_task', lambda value, **kwargs: submitted.append(value) or (100, ''))
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        'transferinfo': SimpleNamespace(file_list_new=[str(source)]),
        'mediainfo': SimpleNamespace(title='整理事件电影', year='2026', type='电影', original_language='zh'),
    }))
    assert len(submitted) == 1
    assert submitted[0].target.path == source.name
    assert submitted[0].target.archive_path == '电影/华语电影/整理事件电影 (2026)/整理事件电影 (2026).mkv'
    assert Oper.list_scans()[0].trigger == 'transfer'


@pytest.mark.parametrize("changes", [
    {"source_path": "relative"}, {"archive_path": ""}, {"extensions": [".strm"]},
    {"extensions": []}, {"extensions": ["../mkv"]}, {"interval_minutes": 0},
    {"ignore_keywords": ["x" * 101]}, {"name": "   "},
])
def test_invalid_configuration_rejected(directory, changes):
    """边界拒绝无效路径、危险扩展名和无效定时参数"""
    with pytest.raises(ValidationError):
        GdriveDirectoryConfig(**{**directory.config.model_dump(), **changes})


def test_filters_preview_and_nested_paths(directory):
    """只读扫描保存过滤原因并保留相对目录，不写入媒体或 STRM"""
    for name in ("movie.MKV", "sample.mkv", "trailer.mp4", "info.nfo", "movie.tmp.mkv"):
        (directory.source / name).write_bytes(b"data")
    nested = directory.source / "Season 1"
    nested.mkdir()
    (nested / "episode.mp4").write_bytes(b"episode")
    (directory.source / "linked.mkv").symlink_to(nested / "episode.mp4")
    task = _save(directory)
    scan_id, error = GdriveChain.scan_directory(task.id)
    assert not error
    detail = Oper.detail(scan_id)
    reasons = {item.source_path.split("/")[-1]: item.reason for item in detail.items}
    assert reasons == {
        "movie.MKV": "matched", "sample.mkv": "keyword", "trailer.mp4": "keyword",
        "info.nfo": "extension", "movie.tmp.mkv": "keyword", "linked.mkv": "symlink", "episode.mp4": "matched",
    }
    assert detail.scan.status == "previewed" and detail.scan.matched == 2
    episode = next(item for item in detail.items if item.source_path.endswith("episode.mp4"))
    assert episode.archive_path.endswith("media/Season 1/episode.mp4")
    assert not list(directory.mount.iterdir())
    assert not list(directory.source.rglob("*.strm"))


def test_size_suffix_settle_and_nonrecursive(directory):
    """大小、忽略后缀、文件静置及非递归过滤共同生效"""
    for name in ("small.mkv", "fresh.mp4", "skip.mkv"):
        (directory.source / name).write_bytes(b"x")
    (directory.source / "child").mkdir()
    (directory.source / "child/hidden.mkv").write_bytes(b"x")
    task = _save(directory, recursive=False, ignore_suffixes=["skip.mkv"], min_size_mb=1)
    scan_id, _ = GdriveChain.scan_directory(task.id)
    detail = Oper.detail(scan_id)
    assert len(detail.items) == 3
    assert {item.reason for item in detail.items} == {"too_small", "ignored_suffix"}
    Oper.save(directory.config.model_copy(update={"settle_seconds": 300}), task.id)
    scan_id, _ = GdriveChain.scan_directory(task.id)
    assert all(item.reason == "unsettled" for item in Oper.detail(scan_id).items)


def test_disabled_execution_and_missing_mount(directory):
    """停用任务不能运行，普通目录不能伪装真实挂载点"""
    task = _save(directory)
    assert GdriveChain.scan_directory(task.id, dry_run=False)[0] is None
    directory.settings.mount_path = str(directory.source)
    scan_id, _ = GdriveChain.scan_directory(task.id)
    assert Oper.detail(scan_id).scan.status == "failed"


def test_overlapping_and_symlink_directories_rejected(directory):
    """拒绝相互嵌套或使用符号链接的目录"""
    config = directory.config.model_copy(update={"source_path": str(directory.mount)})
    assert "嵌套" in GdriveChain.directory_paths(config)[1]
    linked = directory.source.parent / "link"
    linked.symlink_to(directory.source, target_is_directory=True)
    config = directory.config.model_copy(update={"source_path": str(linked)})
    assert "符号链接" in GdriveChain.directory_paths(config)[1]


def test_active_scan_blocks_overlap_edits_and_deletes(directory):
    """数据库活动键拒绝重复批次，活动中不允许修改或删除"""
    task = _save(directory)
    scan_id, _ = Oper.begin_scan(task.id, True, "manual")
    assert scan_id
    assert Oper.begin_scan(task.id, True, "manual")[0] is None
    with pytest.raises(ValueError):
        Oper.save(directory.config, task.id)
    with pytest.raises(ValueError):
        Oper.delete(task.id)
    Oper.reconcile(interrupted=True)
    assert Oper.detail(scan_id).scan.status == "interrupted"
    assert Oper.delete(task.id)
    assert Oper.detail(scan_id).scan.directory_name == "测试"


def test_schedule_due_disabled_and_claim(directory):
    """定时只运行启用到期任务，触发后推进时间且关闭总开关时不运行"""
    task = _save(directory, enabled=True, schedule_enabled=True, interval_minutes=1)
    assert task.next_run_at and not Oper.due()
    with directory.factory.begin() as db:
        db.get(GdriveDirectory, task.id).next_run_at = "2000-01-01 00:00:00"
    directory.settings.enabled = False
    GdriveChain.poll_directories()
    assert not Oper.list_scans()
    directory.settings.enabled = True
    GdriveChain.poll_directories()
    scans = Oper.list_scans()
    assert len(scans) == 1 and scans[0].trigger == "scheduled"
    assert not Oper.due()
    GdriveChain.poll_directories()
    assert len(Oper.list_scans()) == 1


def test_execution_links_history_and_repeated_scan_skips(directory, monkeypatch):
    """执行批次关联文件状态，保留源文件时重复扫描也不会再次创建任务"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    task = _save(directory, enabled=True)
    calls = []
    def create(source_path, archive_path, remove_source, dedupe_key):
        calls.append(source_path)
        with directory.factory.begin() as db:
            row = GdriveTask(
                src_path=source_path, archive_path=archive_path, dedupe_key=dedupe_key,
                status="pending", stage="pending", created_at="2026-01-01", updated_at="2026-01-01",
            )
            db.add(row)
            db.flush()
            return row.id, ""
    monkeypatch.setattr(GdriveChain, "create_task", create)
    scan_id, _ = GdriveChain.scan_directory(task.id, dry_run=False)
    detail = Oper.detail(scan_id)
    assert detail.scan.status == "running" and detail.scan.active == 1
    assert detail.items[0].task_id
    with directory.factory.begin() as db:
        db.get(GdriveTask, detail.items[0].task_id).status = "completed"
    Oper.reconcile()
    assert Oper.detail(scan_id).scan.status == "completed"
    second, _ = GdriveChain.scan_directory(task.id, dry_run=False)
    assert Oper.detail(second).items[0].reason == "already_seen"
    assert calls == [str(source)]
    source.write_bytes(b"changed")
    third, _ = GdriveChain.scan_directory(task.id, dry_run=False)
    assert Oper.detail(third).scan.queued == 1
    assert len(calls) == 2


def test_failed_submission_preserves_source_and_reason(directory, monkeypatch):
    """文件创建失败应写入扫描明细并保持源文件"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    task = _save(directory, enabled=True)
    monkeypatch.setattr(GdriveChain, "create_task", lambda *args, **kwargs: (None, "测试失败"))
    scan_id, _ = GdriveChain.scan_directory(task.id, dry_run=False)
    detail = Oper.detail(scan_id)
    assert detail.scan.status == "failed"
    assert detail.scan.failed == 1 and detail.scan.matched == 1 and detail.scan.skipped == 0
    assert detail.items[0].reason == "error: 测试失败"
    assert source.read_bytes() == b"media"


def test_partial_failure_keeps_active_batch_until_files_finish(directory):
    """扫描部分提交后失败，已有文件结束之前仍阻止重入"""
    task = _save(directory, enabled=True)
    scan_id, _ = Oper.begin_scan(task.id, False, "manual")
    with directory.factory.begin() as db:
        file_task = GdriveTask(src_path="/test.mkv", status="pending", created_at="2026-01-01", updated_at="2026-01-01")
        db.add(file_task)
        db.flush()
        file_id = file_task.id
        db.add(GdriveScanItem(scan_id=scan_id, source_path="/test.mkv", reason="queued", task_id=file_id))
    Oper.finish_scan(scan_id, "测试扫描中断")
    assert Oper.begin_scan(task.id, False, "manual")[0] is None
    assert Oper.detail(scan_id).scan.active == 1
    with directory.factory.begin() as db:
        db.get(GdriveTask, file_id).status = "completed"
    Oper.reconcile()
    assert Oper.detail(scan_id).scan.status == "failed"
    assert Oper.begin_scan(task.id, False, "manual")[0]


def test_restart_repairs_submitted_file_association(directory):
    """文件任务落库后尚未关联时重启，可按指纹修复关联并保留活动键"""
    task = _save(directory, enabled=True)
    scan_id, _ = Oper.begin_scan(task.id, False, "manual")
    with directory.factory.begin() as db:
        db.add(GdriveTask(src_path="/test.mkv", dedupe_key="fingerprint", status="pending", created_at="2026-01-01", updated_at="2026-01-01"))
        db.add(GdriveScanItem(scan_id=scan_id, source_path="/test.mkv", fingerprint="fingerprint", reason="matched"))
    Oper.reconcile(interrupted=True)
    detail = Oper.detail(scan_id)
    assert detail.scan.status == "interrupted" and detail.scan.active == 1
    assert detail.items[0].task_id and detail.items[0].reason == "queued"
    assert Oper.begin_scan(task.id, False, "manual")[0] is None


def test_scan_limit_never_submits_partial_results(directory, monkeypatch):
    """超出遍历上限不启动文件任务"""
    task = _save(directory, enabled=True)
    monkeypatch.setattr("app.chain.gdrive.os.walk", lambda *args, **kwargs: iter([(str(directory.source), [], [f"{index}.mkv" for index in range(10001)])]))
    submit = []
    monkeypatch.setattr(GdriveChain, "create_task", lambda *args, **kwargs: submit.append(args))
    scan_id, _ = GdriveChain.scan_directory(task.id, False)
    assert Oper.detail(scan_id).scan.status == "failed"
    assert submit == []


def test_scan_rechecks_source_after_traversal(directory, monkeypatch):
    """遍历后变化的文件保留源文件且记录为提交失败"""
    path = directory.source / "movie.mkv"
    path.write_bytes(b"old")
    task = _save(directory, enabled=True)
    candidates = Oper.candidates
    def changed(scan_id):
        result = candidates(scan_id)
        path.write_bytes(b"changed")
        return result
    monkeypatch.setattr(Oper, "candidates", changed)
    scan_id, _ = GdriveChain.scan_directory(task.id, False)
    detail = Oper.detail(scan_id)
    assert detail.scan.failed == 1
    assert "发生变化" in detail.items[0].reason


@pytest.mark.anyio
async def test_http_permissions_and_validation(directory):
    """目录读取允许管理用户，写入和启动必须超级管理员，参数由 HTTP 层验证"""
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/gdrive")
    app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.get("/gdrive/directories")).status_code == 200
        assert (await client.post("/gdrive/directories", json=directory.config.model_dump())).status_code in (401, 403)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace()
        created = (await client.post("/gdrive/directories", json=directory.config.model_dump())).json()
        assert created["success"]
        assert (await client.post("/gdrive/directories", json={"name": "bad"})).status_code == 422
        assert (await client.get("/gdrive/scans/999999")).status_code == 404
        task_id = created["data"]["id"]
        preview = (await client.post(f"/gdrive/directories/{task_id}/scan", json={})).json()
        detail = (await client.get(f"/gdrive/scans/{preview['data']['id']}")).json()
        assert detail["scan"]["dry_run"]


def test_directory_migration_repeat_upgrade_and_rollback(monkeypatch):
    """新迁移支持重复升级，回滚时保留原文件任务"""
    engine = sa.create_engine("sqlite://")
    migration = importlib.import_module("database.versions.f5c9d0e1a2b3_gdrive_directory")
    with engine.begin() as connection:
        sa.Table("gdrivetask", sa.MetaData(), sa.Column("id", sa.Integer(), primary_key=True)).create(connection)
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        assert {"gdrivedirectory", "gdrivescan", "gdrivescanitem"} <= set(sa.inspect(connection).get_table_names())
        migration.downgrade()
        assert sa.inspect(connection).get_table_names() == ["gdrivetask"]


def test_category_order_missing_fields_and_unmatched():
    """优先顺序、多条件、排除、年份范围和缺失信息遵循离线语义"""
    policy = GdriveCategoryPolicy(movie=[
        {"name": "华语动画", "conditions": {"genre_ids": "16", "original_language": "zh,cn"}},
        {"name": "外语新片", "conditions": {"original_language": "!zh,!cn", "release_year": "2020-2026,!2022"}},
    ])
    assert GdriveHelper.category_path(policy, {"genre_ids": [16], "original_language": "zh"}, "movie") == "华语动画"
    assert GdriveHelper.category_path(policy, {"original_language": "en", "year": "2024"}, "movie") == "外语新片"
    for media in ({}, {"original_language": "en", "year": "2022"}, {"genre_ids": [16]}):
        with pytest.raises(ValueError, match="未匹配"):
            GdriveHelper.category_path(policy, media, "movie")
    assert GdriveHelper.category_path(policy.model_copy(update={"unmatched": "fallback"}), {}, "movie") == "未分类"
    assert GdriveHelper.category_path(policy.model_copy(update={"unmatched": "keep"}), {}, "tv") is None


@pytest.mark.parametrize("policy", [
    {"movie": [{"name": "../逃逸"}]}, {"fallback": "/逃逸"},
    {"tv": [{"name": "重复"}, {"name": "重复"}]},
    {"movie": [{"name": "年份", "conditions": {"release_year": "2026-2020"}}]},
])
def test_category_policy_rejects_unsafe_names_and_ranges(policy):
    """分类策略边界拒绝越界名称、重复名称和无效范围"""
    with pytest.raises(ValidationError):
        GdriveCategoryPolicy(**policy)


def test_event_uses_directory_rules_and_dedupes_scan(directory, monkeypatch):
    """整理事件绕过静置等待但保持过滤及分类，与活动扫描共用持久指纹"""
    from pathlib import Path
    source = directory.source / "Example.Movie.2024.mkv"
    source.write_bytes(b"media")
    ignored = directory.source / "sample.mkv"
    ignored.write_bytes(b"sample")
    policy = GdriveCategoryPolicy(movie=[{"name": "华语电影", "conditions": {"original_language": "zh"}}])
    task = _save(directory, enabled=True, settle_seconds=300, classification_mode="custom", category_policy=policy,
                 strm_root=str(directory.source.parent / "library"))
    active_id, _ = Oper.begin_scan(task.id, True, "manual")
    calls = []
    def create(source_path, archive_path, remove_source, dedupe_key, **kwargs):
        calls.append((source_path, archive_path, kwargs))
        with directory.factory.begin() as db:
            row = GdriveTask(src_path=source_path, archive_path=archive_path, dedupe_key=dedupe_key,
                             status="completed", stage="completed", created_at="2026", updated_at="2026", **kwargs)
            db.add(row)
            db.flush()
            return row.id, ""
    monkeypatch.setattr(GdriveChain, "create_task", create)
    monkeypatch.setattr(GdriveChain, "_offline_media", classmethod(lambda cls, path, root: Oper.get_media(
        str(path), cls._directory_fingerprint(path, path),
    )))
    event = SimpleNamespace(event_data={"transferinfo": SimpleNamespace(file_list_new=[str(source), str(ignored)]),
                                       "mediainfo": SimpleNamespace(title="示例", year="2024", type="电影", original_language="zh")})
    chain = object.__new__(GdriveChain)
    chain.on_transfer_complete(event)
    chain.on_transfer_complete(event)
    assert len(calls) == 1
    assert "/电影/华语电影/示例 (2024)/示例 (2024).mkv" in calls[0][1]
    assert calls[0][2]["strm_root"].endswith("library")
    assert any(item.reason == "keyword" for scan in Oper.list_scans() for item in Oper.detail(scan.id).items)
    Oper.finish_scan(active_id)
    os.utime(source, (0, 0))
    # 改变版本后旧事件信息不再适用，补扫须明确报告分类失败。
    monkeypatch.setattr(GdriveChain, "_offline_media", classmethod(lambda cls, path, root: {}))
    scan_id, _ = GdriveChain.scan_directory(task.id)
    assert any("未匹配" in item.reason for item in Oper.detail(scan_id).items)
    assert not list(Path(directory.source.parent / "library").glob("**/*.strm"))


def test_event_batch_survives_restart_and_keeps_policy(directory, monkeypatch):
    """事件持久化后中断可重放，配置快照和媒体版本仍然可读取"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    task = _save(directory, enabled=True)
    monkeypatch.setattr(GdriveChain, "_submit_scan", lambda *args: None)
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        "transferinfo": SimpleNamespace(file_list_new=[str(source)]),
        "mediainfo": SimpleNamespace(title="测试", original_language="zh"),
    }))
    scan_id = Oper.pending_events()[0]
    assert Oper.claim_event(scan_id)
    assert not Oper.claim_event(scan_id)
    Oper.reconcile(interrupted=True)
    assert Oper.pending_events() == [scan_id]
    assert Oper.get_media(str(source), GdriveChain._directory_fingerprint(source, source))["title"] == "测试"
    with pytest.raises(ValueError, match="批次"):
        Oper.save(directory.config, task.id)
    monkeypatch.setattr(GdriveChain, "create_task", lambda *args, **kwargs: (None, "离线测试拒绝执行"))
    GdriveChain._scan_directory(scan_id, False)
    assert Oper.detail(scan_id).scan.status in ("running", "failed", "completed")


def test_strm_mapping_isolated_and_follows_archive_version(directory):
    """最终冲突版本映射到独立媒体库，拒绝符号链接和目录嵌套"""
    root = directory.source.parent / "library"
    value = directory.config.model_copy(update={"strm_root": str(root)})
    assert not GdriveChain.directory_paths(value)[1]
    archive = directory.mount / "media" / "电影" / "movie.version1.mkv"
    task = SimpleNamespace(src_path=str(directory.source / "movie.mkv"), strm_root=str(root),
                           archive_root=str(directory.mount / "media"), strm_path=None)
    assert GdriveChain._task_strm_path(task, archive, directory.mount) == root / "电影/movie.version1.strm"
    root.symlink_to(directory.source, target_is_directory=True)
    with pytest.raises(ValueError, match="根目录"):
        GdriveChain._task_strm_path(task, archive, directory.mount)
    assert GdriveChain.directory_paths(value)[1]


def test_nfo_is_local_and_does_not_expand_entities(directory):
    """仅使用限定源目录内的本地元数据，实体声明不会被解析"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    nfo = source.with_suffix(".nfo")
    nfo.write_text('<movie><title>示例</title><year>2024</year><original_language>zh</original_language><genre>16</genre><country>CN</country></movie>')
    media = GdriveHelper.read_nfo(source, directory.source)
    assert media["genre_ids"] == [16] and media["original_language"] == "zh"
    show_nfo = directory.source / "tvshow.nfo"
    show_nfo.write_text('<tvshow><title>剧集</title><year>2023</year></tvshow>')
    assert GdriveHelper.read_nfo(source, directory.source)["media_type"] == "movie"
    episode = directory.source / "Example.Show.S01E01.mkv"
    episode_media = GdriveHelper.read_nfo(episode, directory.source)
    assert episode_media["media_type"] == "tv" and episode_media["title"] == "剧集"
    assert episode_media["year"] == "2023"
    show_nfo.unlink()
    nfo.write_text('<!DOCTYPE movie [<!ENTITY x SYSTEM "file:///etc/passwd">]><movie><title>&x;</title></movie>')
    assert GdriveHelper.read_nfo(source, directory.source) == {}


def test_enabled_overlapping_directory_rejected(directory):
    """源目录重叠的启用任务不能重复接管媒体"""
    _save(directory, enabled=True)
    with pytest.raises(ValueError, match="嵌套"):
        _save(directory, enabled=True, source_path=str(directory.source / "child"))


def test_classification_migration_preserves_tasks(monkeypatch):
    """新增字段迁移可重复执行并保留既有任务，回滚只撤销本次结构"""
    engine = sa.create_engine("sqlite://")
    migration = importlib.import_module("database.versions.a6d0e1f2b3c4_gdrive_classification")
    with engine.begin() as connection:
        table = sa.Table("gdrivetask", sa.MetaData(), sa.Column("id", sa.Integer(), primary_key=True))
        table.create(connection)
        connection.execute(table.insert().values(id=42))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.upgrade()
        assert {"archive_root", "strm_root"} <= {item["name"] for item in sa.inspect(connection).get_columns("gdrivetask")}
        assert connection.execute(sa.select(table.c.id)).scalar() == 42
        migration.downgrade()
        assert sa.inspect(connection).get_table_names() == ["gdrivetask"]
    engine.dispose()


def test_moviepilot_category_snapshot_is_read_only(directory, monkeypatch):
    """只读复用分类文件，批次不随后续全局策略变动"""
    monkeypatch.setattr("app.chain.gdrive.settings", SimpleNamespace(CONFIG_PATH=directory.source.parent))
    category = directory.source.parent / "category.yaml"
    category.write_text("movie:\n  华语电影:\n    original_language: zh\ntv:\n  日番:\n    origin_country: JP\n", encoding="utf-8")
    task = _save(directory, classification_mode="moviepilot")
    before = category.read_bytes()
    scan_id, error = GdriveChain.scan_directory(task.id)
    assert not error and category.read_bytes() == before
    category.write_text("movie:\n  其他电影:\ntv: {}\n", encoding="utf-8")
    assert Oper.detail(scan_id).config.category_policy.movie[0].name == "华语电影"
    category.unlink()
    assert "配置不存在" in GdriveChain.scan_directory(task.id)[1]
    assert not category.exists()


@pytest.mark.anyio
async def test_offline_category_preview_api(directory):
    """分类预览复用实际计算并校验类型，不写任务、媒体或 STRM"""
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/gdrive")
    app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace()
    config = {**directory.config.model_dump(), "classification_mode": "custom", "category_policy": {
        "movie": [{"name": "动画", "conditions": {"genre_ids": "16"}}],
    }}
    body = {"config": config, "filename": "Example.Movie.2024.mkv", "media": {"genre_ids": [16], "media_type": "movie"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = (await client.post("/gdrive/categories/preview", json=body)).json()
        assert response["success"] and response["data"]["path"].startswith("电影/动画/")
        body["media"]["media_type"] = "invalid"
        assert not (await client.post("/gdrive/categories/preview", json=body)).json()["success"]
    assert not Oper.list_scans() and not list(directory.mount.iterdir())


def test_event_then_scan_uses_same_fingerprint(directory, monkeypatch):
    """整理事件和后续补扫不会为相同文件版本重复创建任务"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    task = _save(directory, enabled=True)
    calls = []
    def create(source_path, archive_path, remove_source, dedupe_key):
        calls.append(dedupe_key)
        with directory.factory.begin() as db:
            row = GdriveTask(src_path=source_path, archive_path=archive_path, dedupe_key=dedupe_key,
                             status="completed", stage="completed", created_at="2026", updated_at="2026")
            db.add(row)
            db.flush()
            return row.id, ""
    monkeypatch.setattr(GdriveChain, "create_task", create)
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        "transferinfo": SimpleNamespace(file_list_new=[str(source)]), "mediainfo": SimpleNamespace(title="电影"),
    }))
    scan_id, error = GdriveChain.scan_directory(task.id, False)
    assert not error and Oper.detail(scan_id).items[0].reason == "already_seen"
    assert len(calls) == 1


def test_event_does_not_postpone_scheduled_scan(directory, monkeypatch):
    """持续到来的整理事件不能推迟负责补漏的扫描时间"""
    source = directory.source / "movie.mkv"
    source.write_bytes(b"media")
    task = _save(directory, enabled=True, schedule_enabled=True)
    with directory.factory.begin() as db:
        db.get(GdriveDirectory, task.id).next_run_at = "2000-01-01 00:00:00"
    monkeypatch.setattr(GdriveChain, "_submit_scan", lambda *args: None)
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        "transferinfo": SimpleNamespace(file_list_new=[str(source)]), "mediainfo": SimpleNamespace(title="电影"),
    }))
    assert Oper.get(task.id).next_run_at == "2000-01-01 00:00:00"
    assert len(Oper.pending_events()) == 1
    source.write_bytes(b"changed")
    GdriveChain._scan_directory(Oper.pending_events()[0], False)
    assert "发生变化" in Oper.detail(Oper.list_scans()[0].id).items[0].reason


def test_transfer_event_prepares_local_metadata_before_upload(directory, monkeypatch):
    """整理事件带齐随片资料后才能提交上传，不生成早于归档的 STRM。"""
    from app.chain.upload import UploadChain
    from app.schemas.gdrive import GdriveUploadSettings, GdriveUploadTarget

    original = directory.source.parent / 'downloads' / 'Movie.2026.mp4'
    original.parent.mkdir()
    original.write_bytes(b'media')
    original.with_suffix('.nfo').write_text('<movie><title>电影</title><year>2026</year></movie>')
    source = directory.source / 'Movie.2026.mp4'
    source.write_bytes(b'media')
    target = GdriveUploadTarget(account_ids=['one'], drive_id='shared', folder_id='staging',
        archive_folder_id='archive', path='', metadata_enabled=True, strm_prefix='http://playback')
    monkeypatch.setattr(UploadChain, 'get_settings', lambda: GdriveUploadSettings(enabled=True))
    _save(directory, enabled=True, upload_target=target)
    submitted = []

    def submit(scan_id, dry_run):
        assert source.with_suffix('.nfo').is_file()
        assert not source.with_suffix('.strm').exists()
        submitted.append(scan_id)

    monkeypatch.setattr(GdriveChain, '_submit_scan', submit)
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        'fileitem':SimpleNamespace(storage='local',path=str(original)),
        'transferinfo':SimpleNamespace(file_list_new=[str(source)]),
        'mediainfo':SimpleNamespace(title='电影',year='2026',type='电影'),
    }))
    assert len(submitted) == 1


def test_transfer_event_missing_metadata_retains_source_and_stops_upload(directory, monkeypatch):
    """资料缺失时整理源保留，不能提交没有资料的上传任务。"""
    from app.chain.upload import UploadChain
    from app.schemas.gdrive import GdriveUploadSettings, GdriveUploadTarget

    original = directory.source.parent / 'downloads' / 'Movie.2026.mp4'
    original.parent.mkdir()
    original.write_bytes(b'media')
    source = directory.source / 'Movie.2026.mp4'
    source.write_bytes(b'media')
    target = GdriveUploadTarget(account_ids=['one'], drive_id='shared', folder_id='staging',
        archive_folder_id='archive', path='', metadata_enabled=True, strm_prefix='http://playback')
    monkeypatch.setattr(UploadChain, 'get_settings', lambda: GdriveUploadSettings(enabled=True))
    _save(directory, enabled=True, upload_target=target)
    monkeypatch.setattr(GdriveChain, '_submit_scan', lambda *args: pytest.fail('资料缺失不能提交'))
    object.__new__(GdriveChain).on_transfer_complete(SimpleNamespace(event_data={
        'fileitem':SimpleNamespace(storage='local',path=str(original)),
        'transferinfo':SimpleNamespace(file_list_new=[str(source)]),
    }))
    assert original.read_bytes() == source.read_bytes() == b'media'


def test_event_archive_strm_failure_retry_and_cleanup(directory, monkeypatch):
    """使用真实任务状态机验证分类、独立输出、失败保源及重启后恢复和清理"""
    from app.db.gdrive_task_oper import GdriveTaskOper
    from app.db.gdrive_task_history_oper import GdriveTaskHistoryOper
    source = directory.source / "Example.Movie.2024.mkv"
    source.write_bytes(b"offline-integration-media")
    library = directory.source.parent / "library"
    _save(directory, enabled=True, remove_source=True, classification_mode="custom", strm_root=str(library),
          category_policy=GdriveCategoryPolicy(movie=[{"name": "华语电影", "conditions": {"original_language": "zh"}}]))
    scheduled = []
    monkeypatch.setattr(GdriveChain, "start_task", classmethod(lambda cls, task_id: scheduled.append(task_id)))
    with directory.factory() as session:
        task_oper = GdriveTaskOper(session)
        monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", lambda: task_oper)
        chain = object.__new__(GdriveChain)
        chain.on_transfer_complete(SimpleNamespace(event_data={
            "transferinfo": SimpleNamespace(file_list_new=[str(source)]),
            "mediainfo": SimpleNamespace(title="示例电影", year="2024", type="电影", original_language="zh"),
        }))
        assert len(scheduled) == 1
        task_id = scheduled[0]
        original_write = GdriveChain._write_strm
        def fail_strm(*args, **kwargs):
            raise OSError("模拟输出盘暂时不可写")
        monkeypatch.setattr(GdriveChain, "_write_strm", staticmethod(fail_strm))
        chain._run_task(task_id)
        task = task_oper.get(task_id)
        assert task.status == "failed" and task.stage == "generating_strm"
        assert source.read_bytes() == b"offline-integration-media"
        archive = directory.mount / "media/电影/华语电影/示例电影 (2024)/示例电影 (2024).mkv"
        assert archive.read_bytes() == b"offline-integration-media"
        monkeypatch.setattr(GdriveChain, "_write_strm", staticmethod(original_write))
        assert GdriveChain.retry_task(task_id)[0]
        # 新实例只依赖数据库中的阶段、根目录与路径恢复。
        object.__new__(GdriveChain)._run_task(task_id)
        task = task_oper.get(task_id)
        assert task.status == "completed" and not source.exists()
        output = library / archive.relative_to(directory.mount / "media").with_suffix(".strm")
        assert output.read_text() == str(archive) + "\n"
        assert list(directory.mount.glob("**/*.mkv")) == [archive]
        assert GdriveChain.cleanup_task(task_id, False).can_cleanup
        assert archive.is_file() and output.is_file()


def test_scan_rejects_changed_directory_revision(directory):
    """策略读取后配置发生变化时拒绝建立混用版本的批次"""
    task = _save(directory)
    Oper.save(directory.config.model_copy(update={"name": "已更新"}), task.id)
    scan_id, error = Oper.begin_scan(task.id, True, "manual", expected_revision=task.revision)
    assert scan_id is None and "配置已改变" in error
    assert not Oper.list_scans()
