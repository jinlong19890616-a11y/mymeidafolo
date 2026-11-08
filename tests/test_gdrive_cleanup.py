from types import SimpleNamespace

from app.chain.gdrive import GdriveChain
from app.schemas.gdrive import GdriveSettings
from app.schemas.types import GdriveTaskStatus


def _task(task_id, source, archive, status):
    """构造任务清理测试对象。"""
    return SimpleNamespace(
        id=task_id,
        src_path=str(source),
        archive_path=str(archive) if archive else None,
        strm_path=None,
        status=status,
    )


def test_cleanup_dry_run_lists_cancelled_task_files_without_deleting(tmp_path, monkeypatch):
    """取消任务默认只预览，不能误删临时文件。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    staging = mount / ".mymediafolo-uploading" / "21-movie.mkv.part"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"partial")
    archive = mount / "movie.mkv"
    task = _task(21, source, archive, GdriveTaskStatus.CANCELLED.value)

    class FakeOper:
        def get(self, task_id):
            return task if task_id == task.id else None

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        GdriveChain,
        "get_settings",
        staticmethod(lambda: GdriveSettings(enabled=True, mount_path=str(mount))),
    )

    result = GdriveChain.cleanup_task(task.id)

    assert result.can_cleanup
    assert result.dry_run
    assert result.candidates == [str(staging)]
    assert result.removed_paths == []
    assert staging.exists()


def test_cleanup_cancelled_task_removes_only_task_staging_file(tmp_path, monkeypatch):
    """确认清理后只删除任务临时文件，不删除源文件或归档文件。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    staging = mount / ".mymediafolo-uploading" / "22-movie.mkv.part"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"partial")
    archive = mount / "movie.mkv"
    archive.write_bytes(b"existing")
    task = _task(22, source, archive, GdriveTaskStatus.CANCELLED.value)

    class FakeOper:
        def get(self, task_id):
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        GdriveChain,
        "get_settings",
        staticmethod(lambda: GdriveSettings(enabled=True, mount_path=str(mount))),
    )

    result = GdriveChain.cleanup_task(task.id, dry_run=False)

    assert result.removed_paths == [str(staging)]
    assert not staging.exists()
    assert source.exists()
    assert archive.read_bytes() == b"existing"


def test_cleanup_failed_task_keeps_staging_for_retry(tmp_path, monkeypatch):
    """失败任务不能清理断点文件，避免破坏后续重试。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    staging = mount / ".mymediafolo-uploading" / "23-movie.mkv.part"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"partial")
    task = _task(23, source, mount / "movie.mkv", GdriveTaskStatus.FAILED.value)

    class FakeOper:
        def get(self, task_id):
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        GdriveChain,
        "get_settings",
        staticmethod(lambda: GdriveSettings(enabled=True, mount_path=str(mount))),
    )

    result = GdriveChain.cleanup_task(task.id, dry_run=False)

    assert not result.can_cleanup
    assert result.removed_paths == []
    assert result.retained_paths == [str(staging)]
    assert staging.exists()


def test_cleanup_rejects_persisted_archive_path_outside_mount(tmp_path, monkeypatch):
    """任务中的历史归档路径越界时，清理必须停止而不能触碰外部路径。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = _task(24, source, tmp_path / "outside.mkv", GdriveTaskStatus.CANCELLED.value)

    class FakeOper:
        def get(self, task_id):
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        GdriveChain,
        "get_settings",
        staticmethod(lambda: GdriveSettings(enabled=True, mount_path=str(mount))),
    )

    result = GdriveChain.cleanup_task(task.id, dry_run=False)

    assert not result.can_cleanup
    assert "不在 Google Drive 挂载目录内" in result.message


def test_cleanup_rejects_arbitrary_strm_path(tmp_path, monkeypatch):
    """任务中的异常 STRM 路径不能被用作任意文件清理路径。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = _task(25, source, mount / "movie.mkv", GdriveTaskStatus.CANCELLED.value)
    task.strm_path = str(tmp_path / "outside" / "movie.strm")

    class FakeOper:
        def get(self, task_id):
            return task

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        GdriveChain,
        "get_settings",
        staticmethod(lambda: GdriveSettings(enabled=True, mount_path=str(mount))),
    )

    result = GdriveChain.cleanup_task(task.id, dry_run=False)

    assert not result.can_cleanup
    assert "STRM 路径不符合归档映射" in result.message
