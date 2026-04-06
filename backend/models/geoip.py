from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime, timezone

from backend.database import Base


class GeoipSource(Base):
    __tablename__ = "geoip_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    url = Column(String(512), nullable=False)
    country_code = Column(String(8), nullable=False, default="ru")
    ipset_name = Column(String(64), nullable=False, default="geoip_ru")

    last_updated = Column(DateTime, nullable=True)
    prefix_count = Column(Integer, nullable=True, default=0)
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
