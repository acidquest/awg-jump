from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime, timezone

from backend.database import Base


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)

    ipset_name = Column(String(64), nullable=True)    # например geoip_ru
    fwmark = Column(String(16), nullable=True)         # например 0x1
    table_id = Column(Integer, nullable=True)          # номер таблицы маршрутизации
    via_interface = Column(String(32), nullable=True)  # eth0 или awg1

    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
