from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.chain.gdrive import GdriveChain
from app.db.gdrive_directory_oper import GdriveDirectoryOper as Oper
from app.db.models.gdrive_directory import GdriveDirectory
from app.db.models.gdrive_scan import GdriveScan
from app.db.models.gdrive_scan_item import GdriveScanItem
from app.db.models.gdrive_task import GdriveTask
from app.db.models.gdrive_media import GdriveMedia
from app.helper.gdrive import GdriveHelper
from app.schemas.gdrive import GdriveCategoryPolicy, GdriveDirectoryConfig, GdriveRuleConfig, GdriveSettings


@pytest.fixture
def rules(tmp_path, monkeypatch):
    """规则和扫描使用独立临时数据库，禁止实际挂载或后台执行"""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/rules.db")
    for model in (GdriveDirectory, GdriveScan, GdriveScanItem, GdriveTask, GdriveMedia):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.gdrive_directory_oper.SessionFactory", factory)
    values = {}

    class Config:
        """隔离运行配置，避免读取测试库以外的规则"""

        def get(self, key):
            """读取测试规则"""
            return values.get(key)

        def set(self, key, value):
            """写入测试规则"""
            values[key] = value

        def increment(self, key):
            """分配测试编号"""
            values[key] = values.get(key, 0) + 1
            return values[key]

    monkeypatch.setattr("app.db.gdrive_directory_oper.SystemConfigOper", Config)
    yield factory
    engine.dispose()


def test_movie_and_episode_paths_use_real_filename_parser():
    """真实本地解析器分别生成电影目录和剧集季集结构"""
    rule = GdriveRuleConfig(name="媒体库")
    movie = GdriveHelper.rename_path(rule, Path("Example.Movie.2024.2160p.H265.mkv"))
    assert movie.parts[0] == "电影"
    assert movie.suffix == ".mkv" and "2024" in str(movie)
    series = GdriveHelper.rename_path(rule, Path("Example.Show.2024.S01E02.1080p.H264.mkv"))
    assert series.parts[0] == "电视剧"
    assert "Season 01" in series.parts and "S01E02" in series.name


@pytest.mark.parametrize("template", ["../{{ title }}", "/{{ title }}", "{{ title.__class__ }}", "{% for x in title %}", "{{ unknown }}", "{{ title }}/../escape", "{{ title }}\\escape"])
def test_unsafe_templates_rejected(template):
    """模板拒绝路径穿越、未知变量及可执行表达式"""
    with pytest.raises(ValidationError):
        GdriveRuleConfig(name="无效", movie_directory=template)


def test_missing_metadata_is_explicit_and_no_tmdb_lookup():
    """缺失 ID 不伪造识别结果，必须报告实际缺少字段"""
    rule = GdriveRuleConfig(name="带 ID", movie_directory="{{ title }} [tmdbid={{ tmdb_id }}]")
    with pytest.raises(ValueError, match="tmdb_id"):
        GdriveHelper.rename_path(rule, Path("Example.Movie.2024.mkv"))
    assert "123" in str(GdriveHelper.rename_path(rule, Path("Example.Movie.2024.[tmdbid=123].mkv")))


def test_category_order_and_filename_validation():
    """分类按指定顺序构建，文件名不能创建额外目录"""
    rule = GdriveRuleConfig(name="年份分类", categories=["year", "media_type"])
    result = GdriveHelper.rename_path(rule, Path("Example.Movie.2024.mkv"))
    assert result.parts[:2] == ("2024", "电影")
    with pytest.raises(ValidationError):
        GdriveRuleConfig(name="无效", tv_filename="folder/{{ title }}")
    with pytest.raises(ValidationError):
        GdriveRuleConfig(name="无效", categories=["year", "year"])


@pytest.mark.parametrize("filename", ["Example.Show.2024.S01-S02.mkv", "Example.Show.2024.S01E01-S02E02.mkv"])
def test_cross_season_range_rejected(filename):
    """跨季范围不能被静默归入首季目录"""
    rule = GdriveRuleConfig(name="剧集", media_type="tv")
    with pytest.raises(ValueError, match="跨季"):
        GdriveHelper.rename_path(rule, Path(filename))


def test_rule_reference_and_scan_snapshot(rules):
    """规则被引用时不能删除，运行批次保留修改前的命名快照"""
    rule = Oper.save_rule(GdriveRuleConfig(name="规则"))
    task = Oper.save(GdriveDirectoryConfig(name="任务", source_path="/source", archive_path="/mount", rule_id=rule.id))
    scan_id, error = Oper.begin_scan(task.id, True, "manual")
    assert not error
    Oper.save_rule(GdriveRuleConfig(name="新版", movie_filename="{{ original_name }}"), rule.id)
    assert Oper.get_rule_snapshot(scan_id).name == "规则"
    assert Oper.detail(scan_id).rule.revision == 1
    assert Oper.rules()[0].revision == 2
    with pytest.raises(ValueError, match="引用"):
        Oper.delete_rule(rule.id)
    Oper.finish_scan(scan_id)
    Oper.delete(task.id)
    assert Oper.delete_rule(rule.id)
    assert Oper.detail(scan_id).rule.name == "规则"


def test_missing_rule_cannot_be_saved(rules):
    """拒绝保存悬空规则引用"""
    with pytest.raises(ValueError, match="不存在"):
        Oper.save(GdriveDirectoryConfig(name="任务", source_path="/source", archive_path="/mount", rule_id=999))


def test_category_rule_snapshot_is_frozen(rules):
    """二次分类随规则版本冻结，后续编辑不会改变已有批次目标"""
    policy = GdriveCategoryPolicy(movie=[{"name": "电影/旧分类"}], path_mode="relative")
    rule = Oper.save_rule(GdriveRuleConfig(name="规则", category_policy=policy))
    task = Oper.save(GdriveDirectoryConfig(name="任务", source_path="/source", archive_path="/mount", rule_id=rule.id))
    scan_id, error = Oper.begin_scan(task.id, True, "manual")
    assert not error
    Oper.save_rule(GdriveRuleConfig(name="新规则", category_policy=GdriveCategoryPolicy(movie=[{"name": "新分类"}])), rule.id)
    frozen = Oper.get_rule_snapshot(scan_id)
    assert frozen.category_policy.movie[0].name == "电影/旧分类"
    assert frozen.category_policy.path_mode == "relative"


def test_scan_applies_rule_and_retains_unrecognized_source(rules, tmp_path, monkeypatch):
    """实际扫描使用规则生成目标并报告字段缺失，预览不创建文件任务或 STRM"""
    source = tmp_path / "source"
    mount = tmp_path / "mount"
    source.mkdir()
    mount.mkdir()
    (source / "Example.Movie.2024.mkv").write_bytes(b"media")
    (source / "Unknown.mkv").write_bytes(b"media")
    rule = Oper.save_rule(GdriveRuleConfig(name="规则"))
    task = Oper.save(GdriveDirectoryConfig(name="任务", source_path=str(source), archive_path=str(mount / "media"), rule_id=rule.id, settle_seconds=0))
    monkeypatch.setattr(GdriveChain, "get_settings", staticmethod(lambda: GdriveSettings(mount_path=str(mount))))
    monkeypatch.setattr(Path, "is_mount", lambda path: path == mount)
    monkeypatch.setattr(GdriveChain, "_submit_scan", lambda scan_id, dry_run: GdriveChain._scan_directory(scan_id, dry_run))
    scan_id, error = GdriveChain.scan_directory(task.id)
    assert not error
    detail = Oper.detail(scan_id)
    valid = next(item for item in detail.items if "2024" in item.source_path)
    invalid = next(item for item in detail.items if "Unknown" in item.source_path)
    assert "/电影/" in valid.archive_path and valid.reason == "matched"
    assert invalid.reason.startswith("error:") and invalid.archive_path is None
    assert detail.scan.failed == 1
    assert not list(mount.iterdir()) and len(list(source.iterdir())) == 2
    assert all(item.task_id is None for item in detail.items)
