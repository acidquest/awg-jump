import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.system_metric import SystemMetric

logger = logging.getLogger(__name__)

RETENTION_HOURS = 24
ONE_DAY_MINUTES = RETENTION_HOURS * 60
SAMPLE_INTERVAL_SECONDS = 60


def _read_cpu_counters() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as fh:
        fields = fh.readline().split()

    if len(fields) < 5 or fields[0] != "cpu":
        raise RuntimeError("Unable to read CPU counters from /proc/stat")

    counters = [int(value) for value in fields[1:]]
    idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
    total = sum(counters)
    return total, idle


def _read_memory_bytes() -> tuple[int, int, int]:
    values_kb: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            key, raw = line.split(":", 1)
            values_kb[key] = int(raw.strip().split()[0])

    total = values_kb.get("MemTotal", 0) * 1024
    available = values_kb.get("MemAvailable", values_kb.get("MemFree", 0)) * 1024
    used = max(total - available, 0)
    return total, used, available


async def collect_system_metrics(session: AsyncSession) -> SystemMetric:
    total_ticks, idle_ticks = _read_cpu_counters()
    memory_total, memory_used, memory_free = _read_memory_bytes()

    previous = await session.scalar(
        select(SystemMetric).order_by(SystemMetric.collected_at.desc()).limit(1)
    )

    cpu_usage_percent = 0.0
    if previous:
        total_delta = total_ticks - previous.cpu_total_ticks
        idle_delta = idle_ticks - previous.cpu_idle_ticks
        if total_delta > 0:
            cpu_usage_percent = max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100))

    metric = SystemMetric(
        collected_at=datetime.now(timezone.utc),
        cpu_usage_percent=cpu_usage_percent,
        cpu_total_ticks=total_ticks,
        cpu_idle_ticks=idle_ticks,
        memory_total_bytes=memory_total,
        memory_used_bytes=memory_used,
        memory_free_bytes=memory_free,
    )
    session.add(metric)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)
    await session.execute(delete(SystemMetric).where(SystemMetric.collected_at < cutoff))
    await session.commit()
    await session.refresh(metric)
    return metric


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def ensure_recent_system_metrics(session: AsyncSession) -> SystemMetric | None:
    latest = await session.scalar(
        select(SystemMetric).order_by(SystemMetric.collected_at.desc()).limit(1)
    )
    latest_collected_at = _to_utc(latest.collected_at) if latest else None

    if latest_collected_at is None:
        return await collect_system_metrics(session)

    now = datetime.now(timezone.utc)
    if (now - latest_collected_at).total_seconds() >= SAMPLE_INTERVAL_SECONDS:
        return await collect_system_metrics(session)

    return latest


async def get_metrics_history(session: AsyncSession, hours: int) -> tuple[SystemMetric | None, list[SystemMetric]]:
    bounded_hours = 24 if hours >= 24 else 1
    cutoff = datetime.now(timezone.utc) - timedelta(hours=bounded_hours)
    latest = await ensure_recent_system_metrics(session)
    history = (
        await session.execute(
            select(SystemMetric)
            .where(SystemMetric.collected_at >= cutoff)
            .order_by(SystemMetric.collected_at.asc())
            .limit(ONE_DAY_MINUTES)
        )
    ).scalars().all()
    return latest, history
