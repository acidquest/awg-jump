from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer

from backend.database import Base


class SystemMetric(Base):
    __tablename__ = "system_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    cpu_usage_percent = Column(Float, nullable=False, default=0.0)
    cpu_total_ticks = Column(Integer, nullable=False, default=0)
    cpu_idle_ticks = Column(Integer, nullable=False, default=0)

    memory_total_bytes = Column(Integer, nullable=False, default=0)
    memory_used_bytes = Column(Integer, nullable=False, default=0)
    memory_free_bytes = Column(Integer, nullable=False, default=0)
