import errno
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.chain.gdrive import GdriveChain
from app.schemas.gdrive import GdriveSettings
from app.schemas.types import GdriveTaskStatus
from app.db.models.gdrive_task import GdriveTask


def test_safe_directory_rejects_empty_path():
    """未配置挂载目录时不能把当前工作目录当作目标目录。"""
    with pytest.raises(RuntimeError):
        GdriveChain._safe_directory("", "挂载目录")


def test_archive_path_must_stay_inside_mount(tmp_path):
    """归档目标越过挂载根目录时必须拒绝。"""
    mount = tmp_path / "drive"
    mount.mkdir()
    config = GdriveSettings(enabled=True, mount_path=str(mount))
    task = SimpleNamespace(archive_path=str(tmp_path / "outside" / "movie.mkv"), stage="pending")

    with pytest.raises(ValueError):
        GdriveChain._get_archive_path(task, tmp_path / "movie.mkv", mount, config)


def test_write_strm_uses_media_server_prefix(tmp_path):
    """配置媒体服务器映射前缀时，STRM 内容应使用映射后的路径。"""
    mount = tmp_path / "drive"
    mount.mkdir()
    archive = mount / "Movies" / "movie.mkv"
    strm = tmp_path / "library" / "movie.strm"

    GdriveChain._write_strm(strm, archive, mount, "/media")

    assert strm.read_text(encoding="utf-8") == "/media/Movies/movie.mkv\n"


def test_write_strm_versions_existing_conflict_without_overwrite(tmp_path):
    """已有 STRM 内容不匹配时应创建版本文件且保持原文件不变。"""
    mount = tmp_path / "drive"
    mount.mkdir()
    archive = mount / "movie.mkv"
    archive.write_bytes(b"movie")
    strm = tmp_path / "library" / "movie.strm"
    strm.parent.mkdir()
    strm.write_text("manual-entry\n", encoding="utf-8")

    result = GdriveChain._write_strm(strm, archive, mount, "", task_id=91)

    assert strm.read_text(encoding="utf-8") == "manual-entry\n"
    assert result != strm
    assert result.read_text(encoding="utf-8") == f"{archive}\n"


def test_write_strm_link_io_error_is_not_converted_to_direct_write(tmp_path, monkeypatch):
    """STRM 硬链接发布出现 I/O 错误时必须失败，不能误判为能力不支持。"""
    mount = tmp_path / "drive"
    mount.mkdir()
    archive = mount / "movie.mkv"
    archive.write_bytes(b"movie")
    strm = tmp_path / "library" / "movie.strm"

    def io_error(*args):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr("app.chain.gdrive.os.link", io_error)

    with pytest.raises(OSError, match="Input/output error"):
        GdriveChain._write_strm(strm, archive, mount, "", task_id=92)

    assert not strm.exists()
    assert strm.with_name(".movie.strm.92.mymediafolo.claim").exists()


def test_process_task_deletes_source_only_after_strm_success(tmp_path, monkeypatch):
    """上传或 STRM 失败时必须保留源文件，全部成功后才删除。"""
    source = tmp_path / "source" / "movie.mkv"
    source.parent.mkdir()
    source.write_bytes(b"movie-content")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = SimpleNamespace(
        id=1,
        src_path=str(source),
        source_device=source.stat().st_dev,
        source_inode=source.stat().st_ino,
        source_sha256=hashlib.sha256(b"movie-content").hexdigest(),
        archive_path=None,
        strm_path=None,
        source_size=len(b"movie-content"),
        uploaded_size=0,
        status=GdriveTaskStatus.PENDING.value,
        stage=GdriveTaskStatus.PENDING.value,
        progress=0,
        remove_source=True,
        retry_count=0,
    )

    class FakeOper:
        def get(self, task_id):
            return task if task_id == task.id else None

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    config = GdriveSettings(
        enabled=True,
        mount_path=str(mount),
        copy_chunk_size=1024 * 1024,
    )
    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: config))
    chain = object.__new__(GdriveChain)

    chain._process_task(task.id)

    archive = mount / "movie.mkv"
    assert archive.read_bytes() == b"movie-content"
    assert (source.with_suffix(".strm")).read_text(encoding="utf-8") == f"{archive}\n"
    assert not source.exists()
    assert task.status == GdriveTaskStatus.COMPLETED.value


def test_process_task_keeps_source_when_strm_fails(tmp_path, monkeypatch):
    """STRM 写入异常时源文件和任务失败前的归档文件都不能被误删。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie-content")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = SimpleNamespace(
        id=2,
        src_path=str(source),
        archive_path=None,
        strm_path=None,
        source_size=len(b"movie-content"),
        uploaded_size=0,
        status=GdriveTaskStatus.PENDING.value,
        stage=GdriveTaskStatus.PENDING.value,
        progress=0,
        remove_source=True,
        retry_count=0,
    )

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    config = GdriveSettings(
        enabled=True,
        mount_path=str(mount),
        copy_chunk_size=1024 * 1024,
    )
    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: config))
    monkeypatch.setattr(
        GdriveChain,
        "_write_strm",
        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))),
    )
    chain = object.__new__(GdriveChain)

    with pytest.raises(OSError, match="disk full"):
        chain._process_task(task.id)

    assert source.exists()
    assert (mount / "movie.mkv").exists()


def test_archive_recovery_preserves_existing_file_on_mismatch(tmp_path, monkeypatch):
    """恢复归档时无法证明文件归属，校验失败不能删除既有文件。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"new content")
    mount = tmp_path / "drive"
    mount.mkdir()
    archive = mount / "movie.mkv"
    archive.write_bytes(b"old content")
    task = SimpleNamespace(
        id=3, src_path=str(source), archive_path=str(archive),
        source_device=source.stat().st_dev, source_inode=source.stat().st_ino,
        source_sha256=hashlib.sha256(b"new content").hexdigest(),
        source_size=source.stat().st_size, status="archiving", stage="archiving",
        remove_source=True,
    )

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    chain = object.__new__(GdriveChain)

    with pytest.raises(OSError, match="无法验证归档文件归属"):
        chain._process_task(task.id)

    assert archive.read_bytes() == b"old content"
    assert source.read_bytes() == b"new content"
    assert task.stage == "archiving"
    task.stage = "pending"
    chain._process_task(task.id)
    assert archive.read_bytes() == b"old content"
    assert (mount / "movie (version 2).mkv").read_bytes() == b"new content"


def test_source_change_after_strm_preserves_archive(tmp_path, monkeypatch):
    """生成 STRM 期间源文件变化时保留两份文件并要求重试。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = SimpleNamespace(
        id=4, src_path=str(source), archive_path=None, source_size=8,
        status="pending", stage="pending", remove_source=True,
    )

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    original_write = GdriveChain._write_strm

    def write_and_change(*args, **kwargs):
        result = original_write(*args, **kwargs)
        source.write_bytes(b"modified")
        return result

    monkeypatch.setattr(GdriveChain, "_write_strm", staticmethod(write_and_change))
    with pytest.raises(OSError, match="源文件在归档期间发生变化"):
        object.__new__(GdriveChain)._process_task(task.id)

    assert source.read_bytes() == b"modified"
    assert (mount / "movie.mkv").read_bytes() == b"original"
    assert task.stage == "pending"


def test_archive_recovers_after_link_before_state_update(tmp_path, monkeypatch):
    """归档链接创建后崩溃，重启通过文件身份确认归属且不产生重复版本。"""
    import os

    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    staging = mount / ".mymediafolo-uploading" / "5-movie.mkv.part"
    staging.parent.mkdir()
    staging.write_bytes(b"movie")
    archive = mount / "movie.mkv"
    os.link(staging, archive)
    task = SimpleNamespace(id=5, src_path=str(source), archive_path=str(archive),
                           source_size=5, status="archiving", stage="archiving", remove_source=False)

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    object.__new__(GdriveChain)._process_task(task.id)
    assert task.status == "completed"
    assert archive.read_bytes() == b"movie"
    assert not staging.exists()
    assert not (mount / "movie (version 2).mkv").exists()


def test_unsupported_archive_link_uses_exclusive_copy_without_overwrite(tmp_path, monkeypatch):
    """挂载不支持硬链接时使用排他复制，并保留既有归档版本。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"new")
    mount = tmp_path / "drive"
    mount.mkdir()
    archive = mount / "movie.mkv"
    archive.write_bytes(b"old")
    task = SimpleNamespace(id=6, src_path=str(source), archive_path=None,
                           source_device=source.stat().st_dev, source_inode=source.stat().st_ino,
                           source_sha256=hashlib.sha256(b"new").hexdigest(),
                           source_size=3, status="pending", stage="pending", remove_source=True)

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def release(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    def unsupported(*args):
        raise OSError(errno.EPERM, "hard links unsupported")

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr("app.chain.gdrive.os.link", unsupported)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    object.__new__(GdriveChain)._process_task(task.id)
    assert not source.exists()
    assert archive.read_bytes() == b"old"
    assert (mount / "movie (version 2).mkv").read_bytes() == b"new"


def test_archive_link_io_error_is_not_converted_to_copy(tmp_path, monkeypatch):
    """硬链接发布出现 I/O 错误时必须失败，不能误判为能力不支持并复制。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"new")
    mount = tmp_path / "drive"
    mount.mkdir()
    task = SimpleNamespace(id=10, src_path=str(source), archive_path=None,
                           source_size=3, status="pending", stage="pending", remove_source=False)

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    def io_error(*args):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr("app.chain.gdrive.os.link", io_error)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))

    with pytest.raises(OSError, match="Input/output error"):
        object.__new__(GdriveChain)._process_task(task.id)

    assert not (mount / "movie.mkv").exists()
    assert not (mount / ".movie.mkv.10.mymediafolo.claim").exists()
    assert source.exists()


def test_resume_uses_unbounded_unfinished_query(monkeypatch):
    """启动恢复不使用受分页限制的管理列表。"""
    tasks = [SimpleNamespace(id=i, status="pending") for i in range(150)]
    submitted = []

    class FakeOper:
        def list_unfinished(self):
            return tasks

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True)
    ))
    monkeypatch.setattr(GdriveChain, "start_task", classmethod(
        lambda cls, task_id: submitted.append(task_id)
    ))
    monkeypatch.setattr(GdriveChain, "__init__", lambda self: None)
    GdriveChain.resume_pending_tasks()
    assert submitted == list(range(150))


def test_strm_recovery_rejects_replaced_archive(tmp_path, monkeypatch):
    """生成阶段的归档被替换后，不能仅凭内容相同删除源文件。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    staging = mount / ".mymediafolo-uploading" / "7-movie.mkv.part"
    staging.parent.mkdir()
    staging.write_bytes(b"movie")
    archive = mount / "movie.mkv"
    archive.write_bytes(b"movie")
    task = SimpleNamespace(id=7, src_path=str(source), archive_path=str(archive),
                           source_size=5, status="generating_strm", stage="generating_strm",
                           remove_source=True)

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    with pytest.raises(OSError, match="无法验证归档文件归属"):
        object.__new__(GdriveChain)._process_task(task.id)
    assert source.read_bytes() == b"movie"
    assert archive.read_bytes() == b"movie"
    assert staging.exists()
    assert not source.with_suffix(".strm").exists()


def test_recovery_after_source_removed_before_completion(tmp_path, monkeypatch):
    """删除源文件后状态写入前中断，恢复时凭硬链接确认归档身份。"""
    import os

    source = tmp_path / "movie.mkv"
    mount = tmp_path / "drive"
    mount.mkdir()
    staging = mount / ".mymediafolo-uploading" / "8-movie.mkv.part"
    staging.parent.mkdir()
    staging.write_bytes(b"movie")
    archive = mount / "movie.mkv"
    os.link(staging, archive)
    task = SimpleNamespace(id=8, src_path=str(source), archive_path=str(archive),
                           source_size=5, status="generating_strm", stage="generating_strm",
                           remove_source=True)

    class FakeOper:
        def get(self, task_id):
            return task

        def claim(self, *args):
            return True

        def update(self, task_id, **kwargs):
            for key, value in kwargs.items():
                setattr(task, key, value)
            return True

    monkeypatch.setattr("app.chain.gdrive.GdriveTaskOper", FakeOper)
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(
        lambda: GdriveSettings(enabled=True, mount_path=str(mount))
    ))
    object.__new__(GdriveChain)._process_task(task.id)
    assert task.status == "completed"
    assert source.with_suffix(".strm").read_text(encoding="utf-8") == f"{archive}\n"
    assert archive.read_bytes() == b"movie"
    assert not staging.exists()


def test_source_replacement_during_isolation_is_not_deleted(tmp_path, monkeypatch):
    """源文件隔离后原路径被替换时，只移除已验证的旧 inode。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    original_stat = source.stat()
    archive = tmp_path / "archive.mkv"
    archive.write_bytes(b"original")
    task = SimpleNamespace(
        source_device=original_stat.st_dev,
        source_inode=original_stat.st_ino,
        source_sha256=hashlib.sha256(b"original").hexdigest(),
    )
    replace_source = source.with_name("replacement")
    replace_source.write_bytes(b"replacement")
    real_replace = __import__("os").replace
    replaced = False

    def replace_and_race(src, dst):
        nonlocal replaced
        real_replace(src, dst)
        if Path(src) == source and not replaced:
            replace_source.rename(source)
            replaced = True

    monkeypatch.setattr("app.chain.gdrive.os.replace", replace_and_race)
    monkeypatch.setattr(GdriveChain, "_ensure_lease", staticmethod(lambda task_id: None))

    GdriveChain._remove_source_safely(92, source, archive, task)

    assert source.read_bytes() == b"replacement"
    assert archive.read_bytes() == b"original"


def test_same_inode_source_content_replacement_is_not_deleted(tmp_path, monkeypatch):
    """源文件原地替换后即使 inode 不变，也不能因内容匹配归档而删除。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original")
    original_stat = source.stat()
    source.write_bytes(b"replacement")
    archive = tmp_path / "archive.mkv"
    archive.write_bytes(b"replacement")
    task = SimpleNamespace(
        source_device=original_stat.st_dev,
        source_inode=original_stat.st_ino,
        source_sha256=hashlib.sha256(b"original").hexdigest(),
    )

    monkeypatch.setattr(GdriveChain, "_ensure_lease", staticmethod(lambda task_id: None))

    with pytest.raises(OSError, match="无法验证隔离源文件身份"):
        GdriveChain._remove_source_safely(93, source, archive, task)

    assert source.read_bytes() == b"replacement"


def test_database_lease_is_exclusive_and_expires():
    """数据库租约在有效期内互斥，超时后允许其他 worker 接管。"""
    engine = create_engine("sqlite://")
    GdriveTask.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    now = datetime(2026, 1, 1, 0, 0, 0)
    with session_factory() as session:
        task = GdriveTask(
            src_path="/source.mkv", source_size=1, status="pending", stage="pending",
            created_at=now.isoformat(), updated_at=now.isoformat(),
        )
        session.add(task)
        session.commit()
        task_id = task.id

        assert GdriveTask.claim_task(
            session, task_id, "worker-a", now.isoformat(), (now + timedelta(seconds=30)).isoformat()
        )
        assert GdriveTask.owns_task(session, task_id, "worker-a")
        assert not GdriveTask.claim_task(
            session, task_id, "worker-b", now.isoformat(), (now + timedelta(seconds=30)).isoformat()
        )
        assert GdriveTask.claim_task(
            session, task_id, "worker-b", (now + timedelta(seconds=31)).isoformat(),
            (now + timedelta(seconds=61)).isoformat(),
        )
        assert not GdriveTask.owns_task(session, task_id, "worker-a")
        assert GdriveTask.owns_task(session, task_id, "worker-b")
        assert not GdriveTask.release_task(session, task_id, "worker-a")
        assert GdriveTask.release_task(session, task_id, "worker-b")
    engine.dispose()
