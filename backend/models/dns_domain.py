from sqlalchemy import Boolean, Column, DateTime, Integer, String
from datetime import datetime, timezone

from backend.database import Base


class DnsDomain(Base):
    __tablename__ = "dns_domains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(253), unique=True, nullable=False)  # RFC 1035: max 253 chars
    upstream = Column(String(64), nullable=False, default="local", server_default="local")
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
