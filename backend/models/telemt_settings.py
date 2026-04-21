from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from backend.database import Base


class TelemtSettings(Base):
    __tablename__ = "telemt_settings"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    port = Column(Integer, nullable=False, default=443)
    tls_domain = Column(String(253), nullable=False, default="petrovich.ru")
    use_middle_proxy = Column(Boolean, nullable=False, default=True)
    log_level = Column(String(16), nullable=False, default="normal")
    mode_classic = Column(Boolean, nullable=False, default=False)
    mode_secure = Column(Boolean, nullable=False, default=False)
    mode_tls = Column(Boolean, nullable=False, default=True)
    config_text = Column(Text, nullable=False, default="")
    public_host = Column(String(255), nullable=False, default="")
    restart_required = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
