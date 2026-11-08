"""STRM 本地元数据与 Emby 定向扫库的离线验证。"""
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from app.helper.gdrive import GdriveHelper
from app.modules.emby.emby import Emby
from app.schemas.gdrive import GdriveUploadTarget


def test_movie_metadata_is_local_idempotent_and_preserves_conflicts(tmp_path):
    """电影复用完整 NFO 和图片，移除远端图片引用，重试不覆盖用户文件"""
    source = tmp_path / "source" / "Movie.2026.mp4"
    source.parent.mkdir()
    source.touch()
    source.with_suffix(".nfo").write_text('<movie><title>电影</title><plot>剧情</plot><thumb>https://example.org/a.jpg</thumb></movie>')
    (source.parent / "poster.jpg").write_bytes(b"local-poster")
    root = tmp_path / "strm"
    output = root / "电影" / "Movie.strm"
    output.parent.mkdir(parents=True)
    paths = GdriveHelper.prepare_strm_metadata(source, output, root)
    node = ElementTree.parse(output.with_suffix(".nfo")).getroot()
    assert node.findtext("plot") == "剧情" and node.findtext("lockdata") == "true"
    assert node.find("thumb") is None
    assert (output.parent / "poster.jpg").read_bytes() == b"local-poster"
    assert GdriveHelper.prepare_strm_metadata(source, output, root) == paths
    output.with_suffix(".nfo").write_text("user metadata")
    with pytest.raises(ValueError, match="已有元数据"):
        GdriveHelper.prepare_strm_metadata(source, output, root)
    assert output.with_suffix(".nfo").read_text() == "user metadata"


def test_tv_metadata_separates_show_season_episode(tmp_path):
    """剧级和集级 NFO 分别落盘，缺少集信息时仅用已有季集号生成"""
    source = tmp_path / "source" / "Season 01" / "Series.2026.S01E02.mp4"
    source.parent.mkdir(parents=True)
    source.touch()
    (source.parent.parent / "tvshow.nfo").write_text('<tvshow><title>剧名</title><year>2026</year></tvshow>')
    root = tmp_path / "strm"
    output = root / "分类" / "剧名" / "Season 01" / "Series - S01E02.strm"
    output.parent.mkdir(parents=True)
    GdriveHelper.prepare_strm_metadata(source, output, root)
    assert ElementTree.parse(output.parent.parent / "tvshow.nfo").getroot().findtext("title") == "剧名"
    node = ElementTree.parse(output.with_suffix(".nfo")).getroot()
    assert node.tag == "episodedetails" and node.findtext("episode") == "2"


def test_metadata_missing_external_entities_and_symlinks_rejected(tmp_path):
    """缺失或不安全资料不能生成成功状态，更不能覆盖输出路径外文件"""
    source = tmp_path / "Movie.2026.mp4"
    source.touch()
    root = tmp_path / "strm"
    output = root / "Movie" / "Movie.strm"
    output.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="缺少"):
        GdriveHelper.prepare_strm_metadata(source, output, root)
    source.with_suffix(".nfo").write_text('<!DOCTYPE movie [<!ENTITY x SYSTEM "file:///private">]><movie/>')
    with pytest.raises(ValueError, match="外部实体"):
        GdriveHelper.prepare_strm_metadata(source, output, root)
    source.with_suffix(".nfo").write_text('<movie><title>电影</title></movie>')
    output.with_suffix(".nfo").symlink_to(source.with_suffix(".nfo"))
    with pytest.raises(ValueError, match="符号链接"):
        GdriveHelper.prepare_strm_metadata(source, output, root)


def test_refresh_target_requires_explicit_library():
    """禁止缺少库 ID 时退回全库刷新"""
    with pytest.raises(ValidationError, match="同时填写"):
        GdriveUploadTarget(account_ids=["test"], drive_id="drive", folder_id="folder", refresh_server="Emby")


def test_emby_refresh_validates_library_and_http_status(monkeypatch):
    """只刷新已知媒体库，HTTP 400 不能视为成功，202 表示接受请求"""
    emby = object.__new__(Emby)
    emby._host = "http://test/"
    emby._apikey = "private"
    emby.folders = [{"Id": "5"}]
    calls = []
    codes = iter([400, 202])
    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=next(codes), close=lambda: None)
    monkeypatch.setattr("app.modules.emby.emby.RequestUtils.post_res", request)
    assert emby.refresh_library("unknown") is False and not calls
    assert emby.refresh_library("5") is False
    assert emby.refresh_library("5") is True
    assert calls[0]["params"]["ReplaceAllMetadata"] == "false"
