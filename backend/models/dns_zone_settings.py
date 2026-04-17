from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class DnsZoneSettings(Base):
    __tablename__ = "dns_zone_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    zone: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)
    dns_servers: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(String(256), default="", server_default="")
    is_builtin: Mapped[bool] = mapped_column(default=False, server_default="0", nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), default="plain", server_default="plain", nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(253), default="", server_default="", nullable=False)
    endpoint_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint_url: Mapped[str] = mapped_column(String(512), default="", server_default="", nullable=False)
    bootstrap_address: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
