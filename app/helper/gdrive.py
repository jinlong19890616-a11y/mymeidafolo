import re
import os
import tempfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import ruamel.yaml

from app.core.metainfo import MetaInfoPath
from app.schemas.gdrive import GdriveCategoryPolicy, GdriveDirectoryConfig, GdriveRuleConfig


class GdriveHelper:
    """使用本地文件名信息构建安全的归档相对路径"""

    @staticmethod
    def prepare_strm_metadata(source: Path, strm: Path, root: Path) -> list[str]:
        """将本地 NFO 和图片整理到 STRM 旁，剧集保留剧、季和单集层级，不在线识别"""
        if root.resolve() != root or strm.resolve() != strm or root not in strm.parents:
            raise ValueError("STRM 元数据输出越界或包含符号链接")
        meta = MetaInfoPath(source)
        is_tv = meta.begin_episode is not None or meta.begin_season is not None
        pairs = []

        def nfo(path: Path, expected: set[str]) -> Optional[bytes]:
            if not path.is_file():
                return None
            if path.resolve() != path or path.stat().st_size > 1024 * 1024:
                raise ValueError("本地 NFO 包含符号链接或超过大小限制")
            content = path.read_bytes()
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("本地 NFO 不允许外部实体")
            try:
                node = ElementTree.fromstring(content)
            except ElementTree.ParseError as err:
                raise ValueError("本地 NFO 格式无效") from err
            if node.tag not in expected:
                raise ValueError("本地 NFO 媒体类型不匹配")
            # 只引用旁边已经存在的图片，避免 Emby 读取 NFO 时下载远端图片。
            for parent in node.iter():
                for child in list(parent):
                    if child.tag in {"thumb", "fanart"}:
                        parent.remove(child)
            lock = node.find("lockdata")
            if lock is None:
                lock = ElementTree.SubElement(node, "lockdata")
            lock.text = "true"
            return ElementTree.tostring(node, encoding="utf-8", xml_declaration=True)

        def images(directory: Path, output: Path, names: tuple[str, ...]) -> None:
            for name in names:
                for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    path = directory / (name + suffix)
                    if path.is_file():
                        if path.resolve() != path or path.stat().st_size > 20 * 1024 * 1024:
                            raise ValueError("本地图片包含符号链接或超过大小限制")
                        pairs.append((output / path.name, path.read_bytes()))

        if is_tv:
            # 标准归档结构为剧目录/Season xx/单集；拒绝将剧级文件写到分类根。
            if not re.fullmatch(r"(?i)Season\s+\d+", strm.parent.name) or strm.parent.parent == root:
                raise ValueError("剧集元数据要求归档路径包含 Season 季目录")
            show_source = next((parent for parent in list(source.parents)[:3]
                                if (parent / "tvshow.nfo").is_file()), None)
            if show_source is None:
                raise ValueError("缺少本地 tvshow.nfo，无法准备剧集元数据；源文件保留")
            pairs.append((strm.parent.parent / "tvshow.nfo", nfo(show_source / "tvshow.nfo", {"tvshow"})))
            episode = nfo(source.with_suffix(".nfo"), {"episodedetails"})
            if episode is None:
                node = ElementTree.Element("episodedetails")
                for key, value in {"title": source.stem, "season": meta.begin_season if meta.begin_season is not None else 1,
                                   "episode": meta.begin_episode, "lockdata": "true"}.items():
                    if value is not None:
                        ElementTree.SubElement(node, key).text = str(value)
                episode = ElementTree.tostring(node, encoding="utf-8", xml_declaration=True)
            pairs.append((strm.with_suffix(".nfo"), episode))
            season = nfo(source.parent / "season.nfo", {"season"})
            if season:
                pairs.append((strm.parent / "season.nfo", season))
            images(show_source, strm.parent.parent, ("poster", "fanart", "backdrop", "banner", "logo"))
            images(source.parent, strm.parent, ("season-poster",))
        else:
            content = nfo(source.with_suffix(".nfo"), {"movie"}) or nfo(source.parent / "movie.nfo", {"movie"})
            if content is None:
                raise ValueError("缺少本地电影 NFO，无法准备元数据；源文件保留")
            pairs.append((strm.with_suffix(".nfo"), content))
            images(source.parent, strm.parent, ("poster", "fanart", "backdrop", "banner", "logo"))
        for name in ("poster", "fanart", "thumb"):
            for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                image = source.with_name(f"{source.stem}-{name}{suffix}")
                if image.is_file():
                    if image.resolve() != image or image.stat().st_size > 20 * 1024 * 1024:
                        raise ValueError("本地图片包含符号链接或超过大小限制")
                    pairs.append((strm.with_name(f"{strm.stem}-{name}{suffix}"), image.read_bytes()))
        for output, content in pairs:
            if output.resolve() != output or root not in output.parents:
                raise ValueError("元数据目标包含符号链接或超出 STRM 根目录")
            if output.exists() and output.read_bytes() != content:
                raise ValueError(f"已有元数据内容不同，保留原文件：{output.name}")
        for output, content in pairs:
            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", dir=output.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(temporary).chmod(0o644)
                try:
                    os.link(temporary, output)
                except FileExistsError:
                    if output.resolve() != output or output.read_bytes() != content:
                        raise ValueError("元数据目标已被其他文件占用")
            finally:
                Path(temporary).unlink(missing_ok=True)
        return [str(output) for output, _ in pairs]

    @staticmethod
    def rename_path(rule: GdriveRuleConfig, source: Path, media: Optional[dict] = None) -> Path:
        """渲染电影或剧集规则；缺失必需信息时拒绝猜测目标"""
        meta = MetaInfoPath(source)
        media = media or {}
        if media.get("media_type") not in (None, "movie", "tv"):
            raise ValueError("离线媒体类型必须是 movie 或 tv")
        media_type = rule.media_type
        if media_type == "auto":
            media_type = media.get("media_type") or ("tv" if meta.begin_episode is not None or meta.begin_season is not None else "movie")
        season = meta.begin_season if meta.begin_season is not None else 1
        # 通用解析器会把文件的跨季范围折叠为首季，命名时仍需拒绝这种有歧义的归属。
        season_range = re.search(r"(?i)\bS(\d{1,3})(?:E\d+)?\s*[-~]\s*S(\d{1,3})", source.stem)
        if media_type == "tv" and (
            (meta.end_season is not None and meta.end_season != season)
            or (season_range and int(season_range[1]) != int(season_range[2]))
        ):
            raise ValueError("文件包含跨季范围，请拆分文件或使用保留源目录结构")
        episode = meta.begin_episode
        episode_text = f"E{episode:02d}" if episode is not None else ""
        if meta.end_episode is not None and meta.end_episode != episode:
            episode_text += f"-E{meta.end_episode:02d}"
        context = {
            "title": media.get("title") or meta.name, "year": media.get("year") or meta.year,
            "tmdb_id": media.get("tmdb_id") or meta.tmdbid,
            "season": f"{season:02d}" if media_type == "tv" else None,
            "episode": f"{episode:02d}" if episode is not None else None,
            "season_episode": f"S{season:02d}{episode_text}" if episode is not None else None,
            "resolution": meta.resource_pix, "video_codec": meta.video_encode,
            "audio_codec": meta.audio_encode, "team": meta.resource_team,
            "original_name": source.stem, "media_type": "电视剧" if media_type == "tv" else "电影",
        }

        def safe(value: object) -> str:
            text = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", str(value)).strip().rstrip(". ")
            if not text or text in {".", ".."}:
                raise ValueError("规则生成了空路径，请调整模板或文件名")
            return text

        def render(template: str) -> str:
            def replace(match: re.Match) -> str:
                key = match.group(1)
                if context[key] is None or context[key] == "":
                    raise ValueError(f"本地文件名缺少 {key}，请调整规则或补全文件名")
                return safe(context[key])
            return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)

        directory = render(rule.tv_directory if media_type == "tv" else rule.movie_directory)
        filename = render(rule.tv_filename if media_type == "tv" else rule.movie_filename)
        parts = [safe(context[key] or "未分类") for key in rule.categories]
        parts.extend(safe(part) for part in directory.split("/"))
        parts.append(safe(filename) + source.suffix.lower())
        result = Path(*parts)
        if len(result.as_posix()) > 3500 or any(len(part.encode("utf-8")) > 255 for part in result.parts):
            raise ValueError("规则生成的路径过长")
        return result

    @staticmethod
    def media_snapshot(media: object) -> dict:
        """提取整理事件已有字段，不触发任何在线识别"""
        raw = getattr(media, "tmdb_info", None) or {}
        result = dict(raw)
        for key in ("title", "year", "tmdb_id", "category", "genre_ids", "original_language",
                    "origin_country", "production_countries", "release_date", "first_air_date",
                    "keywords", "include_keywords", "actors", "directors", "credits"):
            value = getattr(media, key, None) or raw.get(key)
            if value:
                result[key] = value
        media_type = getattr(media, "type", None)
        media_type = getattr(media_type, "value", media_type)
        if media_type in ("电影", "movie"):
            result["media_type"] = "movie"
        elif media_type in ("电视剧", "tv"):
            result["media_type"] = "tv"
        if result.get("year") and not (result.get("release_date") or result.get("first_air_date")):
            result["release_date"] = str(result["year"])
        if not result.get("tmdb_id") and getattr(media, "tmdbid", None):
            result["tmdb_id"] = media.tmdbid
        return result

    @staticmethod
    def read_category_policy(path: Path, unmatched: GdriveCategoryPolicy) -> GdriveCategoryPolicy:
        """只读冻结 MoviePilot 分类文件，不实例化会初始化配置的分类模块"""
        if not path.is_file():
            raise ValueError("MoviePilot 二级分类配置不存在，请先在媒体整理中配置")
        if path.stat().st_size > 262144:
            raise ValueError("分类 YAML 不能超过 256 KiB")
        return GdriveHelper.parse_category_policy(path.read_text(encoding="utf-8"), unmatched)

    @staticmethod
    def parse_category_policy(content: str, policy: GdriveCategoryPolicy) -> GdriveCategoryPolicy:
        """安全解析有序分类映射，拒绝重复键、别名和未知顶层配置"""
        try:
            parser = ruamel.yaml.YAML(typ="safe")
            if any(isinstance(token, (ruamel.yaml.tokens.AliasToken, ruamel.yaml.tokens.AnchorToken))
                   for token in parser.scan(content)):
                raise ValueError("分类 YAML 不支持锚点或别名，请展开后导入")
            data = parser.load(content) or {}
        except ruamel.yaml.YAMLError as err:
            raise ValueError("分类 YAML 格式无效，请检查缩进和重复名称") from err
        if (not isinstance(data, dict) or not data or set(data) - {"movie", "tv"}
                or any(data.get(key) is not None and not isinstance(data[key], dict) for key in ("movie", "tv"))):
            raise ValueError("分类 YAML 顶层只支持 movie 和 tv 映射")
        return GdriveCategoryPolicy(
            movie=[{"name": name, "conditions": {} if conditions is None else conditions} for name, conditions in (data.get("movie") or {}).items()],
            tv=[{"name": name, "conditions": {} if conditions is None else conditions} for name, conditions in (data.get("tv") or {}).items()],
            unmatched=policy.unmatched, fallback=policy.fallback, path_mode=policy.path_mode,
        )

    @staticmethod
    def read_nfo(source: Path, root: Path) -> dict:
        """读取源目录内有限大小的 NFO；不解析外部实体或访问网络"""
        result = {}
        candidates = [source.with_suffix(".nfo")]
        meta = MetaInfoPath(source)
        # 混合目录中的通用电影信息不能抢占已有季集号文件的剧集归属。
        if meta.begin_season is None and meta.begin_episode is None:
            candidates.append(source.parent / "movie.nfo")
        parent = source.parent
        for _ in range(3):
            if parent != root and root not in parent.parents:
                break
            candidates.append(parent / "tvshow.nfo")
            parent = parent.parent
        for path in candidates:
            if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            content = path.read_bytes()
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                continue
            try:
                node = ElementTree.fromstring(content)
            except ElementTree.ParseError:
                continue
            for field, tag in (("title", "title"), ("year", "year"), ("original_language", "original_language"),
                               ("release_date", "premiered")):
                if node.tag == "episodedetails" and field in ("title", "year", "release_date"):
                    continue
                if node.findtext(tag):
                    result.setdefault(field, node.findtext(tag))
            if node.tag in ("tvshow", "episodedetails"):
                result.setdefault("media_type", "tv")
            elif node.tag == "movie":
                result.setdefault("media_type", "movie")
            countries = [item.text for item in node.findall("country") if item.text and re.fullmatch(r"[A-Za-z]{2}", item.text)]
            if countries:
                result.setdefault("origin_country", countries)
                result.setdefault("production_countries", [{"iso_3166_1": country} for country in countries])
            # NFO 风格名称与 TMDB 编号并非一一对应，仅复用明确的数字编号。
            genres = [int(item.text) for item in node.findall("genre") if item.text and item.text.isdigit()]
            if genres:
                result.setdefault("genre_ids", genres)
            for field, tag in (("include_keywords", "tag"), ("actors", "actor/name"), ("directors", "director")):
                values = [item.text for item in node.findall(tag) if item.text]
                if values:
                    result.setdefault(field, values)
        if result.get("year") and not result.get("release_date"):
            result["release_date"] = result["year"]
        return result

    @staticmethod
    def category_path(policy: GdriveCategoryPolicy, media: dict, media_type: str) -> Optional[str]:
        """按顺序匹配必需条件与可选条件组，系列字段仅生成子目录"""
        if media_type not in ("movie", "tv"):
            raise ValueError("离线媒体类型必须是 movie 或 tv")
        for rule in getattr(policy, media_type):
            required, optional, series = [], [], []
            for key, condition in rule.conditions.items():
                field = key.lstrip("?")
                if field.startswith("series_"):
                    values = GdriveHelper._category_values(media, field.removeprefix("series_"))
                    series.extend(token for token in condition.split(",")
                                  if any(token.casefold() in value for value in values))
                else:
                    (optional if key.startswith("?") else required).append(
                        GdriveHelper._category_condition(media, field, condition))
            if all(required) and (not optional or any(optional)) and (required or optional or not rule.conditions or series):
                if series:
                    segment = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", series[0]).strip().rstrip(". ")
                    return f"{rule.name}/{segment}系列"
                return rule.name
        if policy.unmatched == "skip":
            raise ValueError("离线媒体信息未匹配分类条件；请补充 NFO、调整策略或配置未匹配处理")
        return policy.fallback if policy.unmatched == "fallback" else None

    @staticmethod
    def _category_values(media: dict, field: str) -> list[str]:
        if field in {"year", "release_year"}:
            value = str(media.get("year") or media.get("release_date") or media.get("first_air_date") or "")[:4]
        elif field == "keywords":
            value = media.get("title") or media.get("name")
        elif field == "include_keywords":
            value = media.get(field, media.get("keywords"))
            if isinstance(value, dict):
                value = value.get("keywords", value.get("results", []))
        elif field in {"actors", "directors"}:
            value = media.get(field)
            if value is None:
                credits = media.get("credits") if isinstance(media.get("credits"), dict) else {}
                value = credits.get("cast") if field == "actors" else [
                    item for item in credits.get("crew", []) if item.get("job") == "Director"]
        else:
            value = media.get(field)
        values = value if isinstance(value, list) else [value]
        return [str(item.get("iso_3166_1") or item.get("name") or item.get("id") or "").casefold()
                if isinstance(item, dict) else str(item).casefold() for item in values if item is not None and item != ""]

    @staticmethod
    def _category_condition(media: dict, field: str, condition: str) -> bool:
        values = GdriveHelper._category_values(media, field)
        if not values:
            return False
        def contains(token: str) -> bool:
            if field in {"year", "release_year"} and "-" in token:
                begin, end = token.split("-", 1)
                return any(value.isdigit() and int(begin) <= int(value) <= int(end) for value in values)
            if field in {"keywords", "include_keywords"}:
                return any(token in value for value in values)
            return token in values
        include, required, exclude = [], [], []
        for token in condition.casefold().split(","):
            if token.startswith(("!", "-")):
                exclude.append(token[1:])
            elif token.startswith("+"):
                required.append(token[1:])
            else:
                include.append(token)
        return (all(contains(token) for token in required)
                and (not include or any(contains(token) for token in include))
                and not any(contains(token) for token in exclude))

    @staticmethod
    def directory_relative(value: GdriveDirectoryConfig, rule: Optional[GdriveRuleConfig],
                           path: Path, media: dict) -> Path:
        """事件和补扫共用分类与命名计算，沿用模式保持已有目录"""
        relative = path.relative_to(value.source_path)
        policy = value.category_policy
        if value.classification_mode == "inherit":
            if not rule or rule.category_policy is None:
                return GdriveHelper.rename_path(rule, path, media) if rule else relative
            policy = rule.category_policy
        meta = MetaInfoPath(path)
        media = {"title": meta.name, "year": meta.year, **{key: value for key, value in media.items() if value is not None}}
        media_type = (rule.media_type if rule and rule.media_type != "auto" else media.get("media_type")) or (
            "tv" if meta.begin_season is not None or meta.begin_episode is not None else "movie")
        category = GdriveHelper.category_path(policy, media, media_type)
        if category is None:
            return relative
        naming = rule or GdriveRuleConfig(name="默认归档命名", categories=[])
        naming = naming.model_copy(update={"categories": []})
        prefix = Path("电视剧" if media_type == "tv" else "电影") if policy.path_mode == "media_type" else Path()
        result = prefix / category / GdriveHelper.rename_path(naming, path, media)
        if len(result.as_posix()) > 3500 or any(len(part.encode("utf-8")) > 255 for part in result.parts):
            raise ValueError("分类生成的路径过长")
        return result
