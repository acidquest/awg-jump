from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum
from datetime import datetime, timezone
import enum

from backend.database import Base


class DnsUpstream(str, enum.Enum):
    LOCAL = "yandex"   # local zone DNS
    VPN = "default"    # vpn zone DNS


class DnsDomain(Base):
    __tablename__ = "dns_domains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(253), unique=True, nullable=False)  # RFC 1035: max 253 chars
    upstream = Column(
        SAEnum(
            DnsUpstream,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
            native_enum=False,
        ),
        nullable=False,
        default=DnsUpstream.LOCAL,
        server_default="yandex",
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
