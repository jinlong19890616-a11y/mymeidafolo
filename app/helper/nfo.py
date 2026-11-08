import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from app.core.context import MediaInfo
from app.schemas.types import MediaType


class NfoReader:
    """读取本地 NFO 中已有的媒体资料"""

    @staticmethod
    def read_media(source: Path, root: Path) -> Optional[MediaInfo]:
        """在下载根目录内读取随片资料，不查询在线识别源。"""
        if source.resolve() != source or root not in source.parents:
            return None
        candidates = [source.with_suffix('.nfo'), source.parent / 'movie.nfo']
        for parent in list(source.parents)[:3]:
            if parent == root or root in parent.parents:
                candidates.append(parent / 'tvshow.nfo')
        for path in candidates:
            if path.resolve() != path or not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            content = path.read_bytes()
            if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                continue
            try:
                node = ET.fromstring(content)
            except ET.ParseError:
                continue
            title, year = node.findtext('title'), node.findtext('year')
            if node.tag not in {'movie', 'tvshow'} or not title or not year or not year.isdigit():
                continue
            media_type = MediaType.TV if node.tag == 'tvshow' else MediaType.MOVIE
            identity = hashlib.sha256(f'{media_type.value}:{title}:{year}'.encode()).hexdigest()
            countries = [item.text for item in node.findall('country') if item.text]
            return MediaInfo(
                source='local_nfo', media_id=identity, type=media_type, title=title, year=year,
                overview=node.findtext('plot'), original_language=node.findtext('original_language'),
                origin_country=countries, production_countries=[{'iso_3166_1': value} for value in countries],
                genre_ids=[int(item.text) for item in node.findall('genre') if item.text and item.text.isdigit()],
                release_date=node.findtext('premiered') or year,
            )
        return None

    def __init__(self, xml_file_path: Path):
        self.xml_file_path = xml_file_path
        self.tree = ET.parse(xml_file_path)
        self.root = self.tree.getroot()

    def get_element_value(self, element_path) -> Optional[str]:
        element = self.root.find(element_path)
        return element.text if element is not None else None

    def get_elements(self, element_path) -> List[ET.Element]:
        return self.root.findall(element_path)
