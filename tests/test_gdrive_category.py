from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.endpoints import gdrive as endpoint
from app.db.user_oper import get_current_active_manage_user
from app.helper.gdrive import GdriveHelper
from app.schemas.gdrive import GdriveCategoryPolicy, GdriveCategoryRule, GdriveDirectoryConfig, GdriveRuleConfig


def _policy(content):
    return GdriveHelper.parse_category_policy(content, GdriveCategoryPolicy(path_mode="relative"))


def test_symedia_order_optional_series_and_fallback():
    """复现用户的多层目录、可选组、类型排除、系列及空值兜底"""
    policy = _policy('''movie:
  特摄剧/日系电影:
    genre_ids: "-16"
    ?include_keywords: "tokusatsu,ultraman"
    ?keywords: "奥特曼"
    original_language: "ja"
    series_keywords: "奥特曼"
  纪录片/华语:
    genre_ids: "99,-10402,-16"
    ?original_language: "zh,cn"
    ?origin_country: "CN,TW,HK,MO"
  电影/其他:
tv:
  动漫/儿童:
    genre_ids: "+16,10762"
  动漫/其他:
    genre_ids: "16"
  剧集/欧美剧集/1994年后欧美剧集:
''')
    assert GdriveHelper.category_path(policy, {"title": "新奥特曼", "genre_ids": [28], "original_language": "ja"}, "movie") == "特摄剧/日系电影/奥特曼系列"
    assert GdriveHelper.category_path(policy, {"genre_ids": [99], "origin_country": ["CN"]}, "movie") == "纪录片/华语"
    assert GdriveHelper.category_path(policy, {"genre_ids": [99, 16], "origin_country": ["CN"]}, "movie") == "电影/其他"
    assert GdriveHelper.category_path(policy, {"genre_ids": [16, 10762]}, "tv") == "动漫/儿童"
    assert GdriveHelper.category_path(policy, {"genre_ids": [16]}, "tv") == "动漫/其他"
    assert GdriveHelper.category_path(policy, {}, "tv") == "剧集/欧美剧集/1994年后欧美剧集"


@pytest.mark.parametrize("media,expected", [
    ({"genre_ids": [28, 12]}, True), ({"genre_ids": [28, 14]}, True),
    ({"genre_ids": [28]}, False), ({"genre_ids": [12]}, False),
    ({"genre_ids": [28, 12, 27]}, False), ({}, False),
])
def test_required_optional_values_and_exclusions(media, expected):
    """加号全部必需，普通值至少一个，排除优先且缺失不通过"""
    policy = GdriveCategoryPolicy(movie=[{"name": "动作", "conditions": {"genre_ids": "+28,12,14,-27"}}], unmatched="keep")
    assert (GdriveHelper.category_path(policy, media, "movie") == "动作") == expected


def test_year_boundaries_negative_title_and_local_tags():
    """年份兼容旧字段和闭区间，标题排除与 TMDB 标签各用自身来源"""
    policy = _policy('''movie:
  电影/印度:
    keywords: "-战马"
    ?original_language: "hi"
    ?origin_country: "IN,-CN"
  电影/旧片:
    year: "1550-1999"
  电影/特摄:
    include_keywords: "tokusatsu"
  电影/其他:
''')
    assert GdriveHelper.category_path(policy, {"title": "战马", "original_language": "hi", "year": 1999}, "movie") == "电影/旧片"
    assert GdriveHelper.category_path(policy, {"title": "示例", "origin_country": ["IN"]}, "movie") == "电影/印度"
    assert GdriveHelper.category_path(policy, {"keywords": {"results": [{"name": "tokusatsu"}]}}, "movie") == "电影/特摄"
    assert GdriveHelper.category_path(policy, {"title": "tokusatsu", "year": 2000}, "movie") == "电影/其他"


def test_series_only_and_combined_rules():
    """仅系列规则须命中，组合规则命中后系列缺失不阻止分类"""
    policy = _policy('''movie:
  演唱会:
    series_actors: "周杰伦,林俊杰"
  动画:
    genre_ids: "16"
    series_keywords: "哆啦A梦"
  其他:
''')
    assert GdriveHelper.category_path(policy, {"credits": {"cast": [{"name": "周杰伦"}]}}, "movie") == "演唱会/周杰伦系列"
    assert GdriveHelper.category_path(policy, {"genre_ids": [16], "title": "其他动画"}, "movie") == "动画"
    assert GdriveHelper.category_path(policy, {}, "movie") == "其他"


@pytest.mark.parametrize("name", ["/root", "../escape", "电影/../逃逸", "电影//其他", "电影/其他.", "电影/其他\\目录", "电影/ 其他", ""])
def test_category_paths_reject_unsafe_segments(name):
    """多层目录仍逐段拒绝绝对路径、空段和穿越"""
    with pytest.raises(ValidationError):
        GdriveCategoryRule(name=name)


@pytest.mark.parametrize("content", [
    "movie:\n  电影: null\n  电影: null", "movie:\n  电影:\n    typo: abc",
    "movie:\n  电影: false", "movie:\n  电影: []", "other: {}",
    "movie: &rules {}\ntv: *rules", "movie:\n  电影:\n    year: 2024-2000",
])
def test_yaml_rejects_invalid_and_ambiguous_configuration(content):
    """未知字段、重复键、无效结构和别名不能静默变成兜底分类"""
    with pytest.raises(ValueError):
        _policy(content)


def test_rule_policy_inherits_and_directory_custom_overrides():
    """规则与任务覆盖使用相同计算，路径模式避免重复媒体类型前缀"""
    policy = _policy("movie:\n  电影/华语电影:\n    original_language: zh")
    rule = GdriveRuleConfig(name="分类规则", category_policy=policy)
    config = GdriveDirectoryConfig(name="任务", source_path="/source", archive_path="/archive")
    media = {"title": "示例", "year": 2024, "original_language": "zh"}
    source = Path("/source/Example.Movie.2024.mkv")
    assert GdriveHelper.directory_relative(config, rule, source, media).as_posix() == "电影/华语电影/示例 (2024)/示例 (2024).mkv"
    custom = GdriveCategoryPolicy(movie=[{"name": "其他"}])
    config = config.model_copy(update={"classification_mode": "custom", "category_policy": custom})
    assert GdriveHelper.directory_relative(config, rule, source, media).parts[:2] == ("电影", "其他")


def test_nfo_and_snapshot_keep_existing_tags_and_people(tmp_path):
    """标签及演职员只从事件快照和 NFO 读取，不触发额外识别"""
    source = tmp_path / "Example.Movie.2024.mkv"
    source.with_suffix(".nfo").write_text('<movie><tag>tokusatsu</tag><actor><name>周杰伦</name></actor><director>导演</director></movie>')
    media = GdriveHelper.read_nfo(source, tmp_path)
    assert media["include_keywords"] == ["tokusatsu"] and media["actors"] == ["周杰伦"]
    snapshot = GdriveHelper.media_snapshot(SimpleNamespace(include_keywords=["tokusatsu"], actors=["周杰伦"]))
    assert snapshot["actors"] == ["周杰伦"]


@pytest.mark.anyio
async def test_import_and_rule_preview_api_are_offline():
    """导入只返回草稿，规则预览应用分类和样例信息且不读目录"""
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/gdrive")
    app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = (await client.post('/gdrive/categories/import', json={
            "content": "movie:\n  电影/动画:\n    genre_ids: '16'\n  电影/其他:\ntv: {}",
            "policy": {"path_mode": "relative"},
        })).json()
        assert response["success"] and response["data"]["warnings"]
        policy = response["data"]["policy"]
        preview = (await client.post('/gdrive/rules/preview', json={
            "rule": {"name": "规则", "category_policy": policy}, "filename": "Example.Movie.2024.mkv",
            "media": {"title": "动画样例", "genre_ids": [16]},
        })).json()
        assert preview["success"] and preview["data"]["path"].startswith("电影/动画/动画样例 (2024)/")
        invalid = (await client.post('/gdrive/categories/import', json={"content": "movie:\n  电影: false"})).json()
        assert not invalid["success"]
