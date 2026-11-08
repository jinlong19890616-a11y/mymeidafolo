from pathlib import Path

from app.chain.gdrive import GdriveChain
from app.schemas.gdrive import GdriveSettings


def test_preflight_reports_plain_directory_as_not_ready(tmp_path, monkeypatch):
    """普通目录只能通过路径检查，不能被预检误报为真实挂载点。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    config = GdriveSettings(enabled=True, mount_path=str(mount))
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: config))

    result = GdriveChain.preflight(str(source))

    assert not result.ready
    assert result.archive_path == str(mount / "movie.mkv")
    assert result.strm_path == str(source.with_suffix(".strm"))
    assert any(check.name == "real_mount" and check.level == "warning" for check in result.checks)
    assert not (mount / ".mymediafolo-preflight").exists()


def test_preflight_rejects_archive_outside_mount(tmp_path, monkeypatch):
    """预检必须拒绝越过挂载根目录的归档目标。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    config = GdriveSettings(enabled=True, mount_path=str(mount))
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: config))

    result = GdriveChain.preflight(str(source), str(tmp_path / "outside" / "movie.mkv"))

    assert not result.ready
    archive_check = next(check for check in result.checks if check.name == "archive_path")
    assert not archive_check.passed
    assert result.archive_path is None


def test_preflight_does_not_touch_source_or_mount(tmp_path, monkeypatch):
    """预检只读，不应改变源文件和挂载目录内容。"""
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"movie")
    mount = tmp_path / "drive"
    mount.mkdir()
    before = {path.relative_to(mount) for path in mount.rglob("*")}
    config = GdriveSettings(enabled=True, mount_path=str(mount))
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: config))

    GdriveChain.preflight(str(source))

    assert source.read_bytes() == b"movie"
    assert {path.relative_to(mount) for path in mount.rglob("*")} == before
