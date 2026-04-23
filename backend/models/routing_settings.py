from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class RoutingSettings(Base):
    __tablename__ = "routing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    invert_geoip: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failover_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
