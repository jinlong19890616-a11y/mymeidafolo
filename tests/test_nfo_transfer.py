"""验证无 TMDB 密钥时随片 NFO 的离线整理资料。"""
from types import SimpleNamespace

from app.chain.transfer import TransferChain
from app.core.config import settings
from app.core.metainfo import MetaInfo
from app.schemas import FileItem, TransferDirectoryConf, TransferTask
from app.helper.nfo import NfoReader
from app.schemas.types import MediaType


def test_read_movie_media_without_network(tmp_path):
    """随片 NFO 保留标题、年份和分类字段。"""
    source = tmp_path / 'Movie.2026.mp4'
    source.touch()
    source.with_suffix('.nfo').write_text('<movie><title>离线电影</title><year>2026</year>'
        '<country>CN</country><genre>18</genre><original_language>zh</original_language></movie>')
    media = NfoReader.read_media(source, tmp_path)
    assert media.type == MediaType.MOVIE
    assert media.source == 'local_nfo' and media.tmdb_id is None
    assert media.title == '离线电影' and media.year == '2026'
    assert media.genre_ids == [18] and media.origin_country == ['CN']


def test_episode_uses_show_title_with_bounded_search(tmp_path):
    """单集标题不得作为整部剧名，查找不能越过下载根。"""
    season = tmp_path / 'Show' / 'Season 01'
    season.mkdir(parents=True)
    source = season / 'Show.S01E01.mp4'
    source.touch()
    source.with_suffix('.nfo').write_text('<episodedetails><title>第1集</title></episodedetails>')
    (season.parent / 'tvshow.nfo').write_text('<tvshow><title>离线剧集</title><year>2026</year></tvshow>')
    assert NfoReader.read_media(source, tmp_path).title == '离线剧集'
    assert NfoReader.read_media(source, season) is None


def test_nfo_rejects_entities_symlinks_and_incomplete_identity(tmp_path):
    """危险或缺少必要身份的资料不能进入自动整理。"""
    source = tmp_path / 'Movie.mp4'
    source.touch()
    nfo = source.with_suffix('.nfo')
    nfo.write_text('<!DOCTYPE movie [<!ENTITY x SYSTEM "file:///etc/passwd">]><movie><title>&x;</title><year>2026</year></movie>')
    assert NfoReader.read_media(source, tmp_path) is None
    nfo.write_text('<movie><title>缺年份</title></movie>')
    assert NfoReader.read_media(source, tmp_path) is None
    nfo.unlink()
    nfo.symlink_to('/etc/passwd')
    assert NfoReader.read_media(source, tmp_path) is None


def test_transfer_reads_nfo_and_skips_online_recognition(tmp_path, monkeypatch):
    """真实整理入口读取本地身份，不能调用在线识别或辅助 TMDB。"""
    source = tmp_path / 'Movie.2026.mp4'
    source.touch()
    source.with_suffix('.nfo').write_text('<movie><title>离线电影</title><year>2026</year></movie>')
    directory = TransferDirectoryConf(download_path=str(tmp_path), library_path=str(tmp_path / 'library'),
        library_storage='local', library_category_folder=True)
    monkeypatch.setattr(settings, 'TMDB_API_KEY', '')
    monkeypatch.setattr('app.chain.transfer.DirectoryHelper', lambda: SimpleNamespace(get_dir=lambda *a, **kw: directory))
    monkeypatch.setattr('app.chain.transfer.TransferHistoryOper', lambda: SimpleNamespace())
    monkeypatch.setattr('app.chain.transfer.MediaChain', lambda: (_ for _ in ()).throw(AssertionError('不能在线识别')))
    chain = object.__new__(TransferChain)
    chain.jobview = SimpleNamespace(try_remove_job=lambda task: None)
    task = TransferTask(fileitem=FileItem(storage='local',path=str(source),type='file',name=source.name,extension='mp4'),
        meta=MetaInfo('Movie 2026'), target_directory=directory, library_category_folder=True, preview=True)
    success, message = chain._TransferChain__handle_transfer(task)
    assert not success and message == '未识别到 TMDB 辅助信息，无法按媒体类别整理'
    assert task.mediainfo.source == 'local_nfo' and task.mediainfo.title == '离线电影'
