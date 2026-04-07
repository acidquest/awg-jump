from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum
from datetime import datetime, timezone
import enum

from backend.database import Base


class DnsUpstream(str, enum.Enum):
    yandex = "yandex"    # 77.88.8.8 — для RU-доменов
    default = "default"  # 1.1.1.1 / 8.8.8.8 — для всего остального


class DnsDomain(Base):
    __tablename__ = "dns_domains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(253), unique=True, nullable=False)  # RFC 1035: max 253 chars
    upstream = Column(
        SAEnum(DnsUpstream),
        nullable=False,
        default=DnsUpstream.yandex,
        server_default="yandex",
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
