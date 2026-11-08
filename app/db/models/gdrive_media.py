from sqlalchemy import Column, JSON, String, Text

from app.db import Base, get_id_column


class GdriveMedia(Base):
    """整理事件留下的离线媒体快照，仅对同一源文件版本有效"""

    id = get_id_column()
    source_path = Column(Text, nullable=False, unique=True)
    signature = Column(String, nullable=False)
    media = Column(JSON, nullable=False)
