from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from backend.database import Base


class TelemtUser(Base):
    __tablename__ = "telemt_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    secret_hex = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
