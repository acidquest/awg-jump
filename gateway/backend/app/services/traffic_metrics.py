from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    GatewaySettings,
    TrafficMetricDay,
    TrafficMetricHour,
    TrafficMetricMinute,
    TrafficMetricRaw,
    TrafficMetricState,
)
from app.services.routing import _default_route


RAW_RETENTION_DAYS = 3
MINUTE_RETENTION_DAYS = 31
HOUR_RETENTION_DAYS = 31
DAY_RETENTION_DAYS = 366
RAW_SAMPLE_INTERVAL_SECONDS = 30


@dataclass
class InterfaceCounterSnapshot:
    local_interface_name: str | None
    vpn_interface_name: str
    local_rx_raw_bytes: int
    local_tx_raw_bytes: int
    vpn_rx_raw_bytes: int
    vpn_tx_raw_bytes: int


@dataclass
class TrafficUsageSnapshot:
    collected_at: datetime
    local_interface_name: str | None
    vpn_interface_name: str
    local_rx_bytes: int
    local_tx_bytes: int
    vpn_rx_bytes: int
    vpn_tx_bytes: int


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_command(args: list[str]) -> str:
    from app.services.routing import _run

    rc, out = _run(args)
    if rc != 0:
        raise RuntimeError(out or f"{' '.join(args)} failed")
    return out


def _default_interface_name() -> str | None:
    interface_name, _gateway = _default_route()
    return interface_name


def _sum_deltas(current: int, previous: int) -> int:
    if current < previous:
        return current
    return current - previous


def _bucket_start(value: datetime, *, granularity: str) -> datetime:
    normalized = value.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if granularity == "minute":
        return normalized
    if granularity == "hour":
        return normalized.replace(minute=0)
    if granularity == "day":
        return normalized.replace(hour=0, minute=0)
    raise ValueError(f"Unsupported granularity: {granularity}")


def _parse_ip_link_bytes(output: str) -> tuple[int, int]:
    rx_bytes = 0
    tx_bytes = 0
    lines = output.splitlines()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("RX:") and index + 1 < len(lines):
            parts = lines[index + 1].split()
            if parts:
                try:
                    rx_bytes = int(parts[0])
                except ValueError:
                    rx_bytes = 0
        if stripped.startswith("TX:") and index + 1 < len(lines):
            parts = lines[index + 1].split()
            if parts:
                try:
                    tx_bytes = int(parts[0])
                except ValueError:
                    tx_bytes = 0

    return rx_bytes, tx_bytes


def read_interface_counter_snapshot(gateway_settings: GatewaySettings | None) -> InterfaceCounterSnapshot:
    default_iface = _default_interface_name()
    vpn_rx_raw_bytes = 0
    vpn_tx_raw_bytes = 0
    local_rx_raw_bytes = 0
    local_tx_raw_bytes = 0

    try:
        vpn_link_output = _run_command(["ip", "-s", "-s", "link", "show", "dev", settings.tunnel_interface])
        vpn_rx_raw_bytes, vpn_tx_raw_bytes = _parse_ip_link_bytes(vpn_link_output)
    except RuntimeError:
        pass

    if default_iface:
        try:
            local_link_output = _run_command(["ip", "-s", "-s", "link", "show", "dev", default_iface])
            local_rx_raw_bytes, local_tx_raw_bytes = _parse_ip_link_bytes(local_link_output)
        except RuntimeError:
            pass

    return InterfaceCounterSnapshot(
        local_interface_name=default_iface,
        vpn_interface_name=settings.tunnel_interface,
        local_rx_raw_bytes=local_rx_raw_bytes,
        local_tx_raw_bytes=local_tx_raw_bytes,
        vpn_rx_raw_bytes=vpn_rx_raw_bytes,
        vpn_tx_raw_bytes=vpn_tx_raw_bytes,
    )


def _traffic_bucket_payload(prefix: str, delta: dict[str, int]) -> dict[str, int]:
    if prefix != "bytes":
        raise ValueError(f"Unsupported traffic bucket prefix: {prefix}")
    return {
        "local_rx_bytes": delta["local_rx_bytes"],
        "local_tx_bytes": delta["local_tx_bytes"],
        "vpn_rx_bytes": delta["vpn_rx_bytes"],
        "vpn_tx_bytes": delta["vpn_tx_bytes"],
    }


async def _upsert_bucket(
    session: AsyncSession,
    model: type[TrafficMetricMinute] | type[TrafficMetricHour] | type[TrafficMetricDay],
    bucket_start: datetime,
    delta: dict[str, int],
) -> None:
    payload = {
        "bucket_start": bucket_start.replace(tzinfo=None),
        "sample_count": 1,
        **_traffic_bucket_payload("bytes", delta),
    }
    stmt = insert(model).values(**payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=[model.bucket_start],
        set_={
            "sample_count": model.sample_count + 1,
            "local_rx_bytes": model.local_rx_bytes + delta["local_rx_bytes"],
            "local_tx_bytes": model.local_tx_bytes + delta["local_tx_bytes"],
            "vpn_rx_bytes": model.vpn_rx_bytes + delta["vpn_rx_bytes"],
            "vpn_tx_bytes": model.vpn_tx_bytes + delta["vpn_tx_bytes"],
        },
    )
    await session.execute(stmt)


def _current_usage_from_state(state: TrafficMetricState) -> TrafficUsageSnapshot:
    return TrafficUsageSnapshot(
        collected_at=state.collected_at,
        local_interface_name=state.local_interface_name,
        vpn_interface_name=state.vpn_interface_name,
        local_rx_bytes=state.local_rx_total_bytes,
        local_tx_bytes=state.local_tx_total_bytes,
        vpn_rx_bytes=state.vpn_rx_total_bytes,
        vpn_tx_bytes=state.vpn_tx_total_bytes,
    )


async def collect_traffic_metrics(
    session: AsyncSession,
    gateway_settings: GatewaySettings | None = None,
) -> TrafficUsageSnapshot:
    now = datetime.now(timezone.utc)
    raw_snapshot = read_interface_counter_snapshot(gateway_settings)
    state = await session.get(TrafficMetricState, 1)

    if state is None:
        state = TrafficMetricState(
            id=1,
            collected_at=now,
            local_interface_name=raw_snapshot.local_interface_name,
            vpn_interface_name=raw_snapshot.vpn_interface_name,
            local_rx_raw_bytes=raw_snapshot.local_rx_raw_bytes,
            local_tx_raw_bytes=raw_snapshot.local_tx_raw_bytes,
            vpn_rx_raw_bytes=raw_snapshot.vpn_rx_raw_bytes,
            vpn_tx_raw_bytes=raw_snapshot.vpn_tx_raw_bytes,
            local_rx_total_bytes=raw_snapshot.local_rx_raw_bytes,
            local_tx_total_bytes=raw_snapshot.local_tx_raw_bytes,
            vpn_rx_total_bytes=raw_snapshot.vpn_rx_raw_bytes,
            vpn_tx_total_bytes=raw_snapshot.vpn_tx_raw_bytes,
        )
        session.add(state)
        session.add(
            TrafficMetricRaw(
                collected_at=now,
                local_interface_name=raw_snapshot.local_interface_name,
                vpn_interface_name=raw_snapshot.vpn_interface_name,
                local_rx_total_bytes=state.local_rx_total_bytes,
                local_tx_total_bytes=state.local_tx_total_bytes,
                vpn_rx_total_bytes=state.vpn_rx_total_bytes,
                vpn_tx_total_bytes=state.vpn_tx_total_bytes,
            )
        )
        await _prune_traffic_metrics(session, now)
        return _current_usage_from_state(state)

    delta = {
        "local_rx_bytes": _sum_deltas(raw_snapshot.local_rx_raw_bytes, state.local_rx_raw_bytes),
        "local_tx_bytes": _sum_deltas(raw_snapshot.local_tx_raw_bytes, state.local_tx_raw_bytes),
        "vpn_rx_bytes": _sum_deltas(raw_snapshot.vpn_rx_raw_bytes, state.vpn_rx_raw_bytes),
        "vpn_tx_bytes": _sum_deltas(raw_snapshot.vpn_tx_raw_bytes, state.vpn_tx_raw_bytes),
    }

    state.collected_at = now
    state.local_interface_name = raw_snapshot.local_interface_name
    state.vpn_interface_name = raw_snapshot.vpn_interface_name
    state.local_rx_raw_bytes = raw_snapshot.local_rx_raw_bytes
    state.local_tx_raw_bytes = raw_snapshot.local_tx_raw_bytes
    state.vpn_rx_raw_bytes = raw_snapshot.vpn_rx_raw_bytes
    state.vpn_tx_raw_bytes = raw_snapshot.vpn_tx_raw_bytes
    state.local_rx_total_bytes += delta["local_rx_bytes"]
    state.local_tx_total_bytes += delta["local_tx_bytes"]
    state.vpn_rx_total_bytes += delta["vpn_rx_bytes"]
    state.vpn_tx_total_bytes += delta["vpn_tx_bytes"]
    session.add(state)

    session.add(
        TrafficMetricRaw(
            collected_at=now,
            local_interface_name=raw_snapshot.local_interface_name,
            vpn_interface_name=raw_snapshot.vpn_interface_name,
            local_rx_total_bytes=state.local_rx_total_bytes,
            local_tx_total_bytes=state.local_tx_total_bytes,
            vpn_rx_total_bytes=state.vpn_rx_total_bytes,
            vpn_tx_total_bytes=state.vpn_tx_total_bytes,
        )
    )

    await _upsert_bucket(session, TrafficMetricMinute, _bucket_start(now, granularity="minute"), delta)
    await _upsert_bucket(session, TrafficMetricHour, _bucket_start(now, granularity="hour"), delta)
    await _upsert_bucket(session, TrafficMetricDay, _bucket_start(now, granularity="day"), delta)
    await _prune_traffic_metrics(session, now)
    return _current_usage_from_state(state)


async def _prune_traffic_metrics(session: AsyncSession, now: datetime) -> None:
    raw_cutoff = (now - timedelta(days=RAW_RETENTION_DAYS)).replace(tzinfo=None)
    minute_cutoff = (now - timedelta(days=MINUTE_RETENTION_DAYS)).replace(tzinfo=None)
    hour_cutoff = (now - timedelta(days=HOUR_RETENTION_DAYS)).replace(tzinfo=None)
    day_cutoff = (now - timedelta(days=DAY_RETENTION_DAYS)).replace(tzinfo=None)

    await session.execute(delete(TrafficMetricRaw).where(TrafficMetricRaw.collected_at < raw_cutoff))
    await session.execute(delete(TrafficMetricMinute).where(TrafficMetricMinute.bucket_start < minute_cutoff))
    await session.execute(delete(TrafficMetricHour).where(TrafficMetricHour.bucket_start < hour_cutoff))
    await session.execute(delete(TrafficMetricDay).where(TrafficMetricDay.bucket_start < day_cutoff))


async def ensure_recent_traffic_metrics(session: AsyncSession) -> TrafficUsageSnapshot | None:
    state = await session.get(TrafficMetricState, 1)
    latest_collected_at = _to_utc(state.collected_at) if state else None
    now = datetime.now(timezone.utc)

    if latest_collected_at is None or (now - latest_collected_at).total_seconds() >= RAW_SAMPLE_INTERVAL_SECONDS:
        return await collect_traffic_metrics(session)
    return _current_usage_from_state(state)


async def get_current_traffic_usage(session: AsyncSession) -> TrafficUsageSnapshot | None:
    state = await session.get(TrafficMetricState, 1)
    if state is None:
        return None
    return _current_usage_from_state(state)


async def _sum_aggregate_window(
    session: AsyncSession,
    model: type[TrafficMetricMinute] | type[TrafficMetricHour] | type[TrafficMetricDay],
    since: datetime,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(model)
            .where(model.bucket_start >= since.replace(tzinfo=None))
            .order_by(model.bucket_start.asc())
        )
    ).scalars().all()

    return {
        "local_rx_bytes": sum(row.local_rx_bytes for row in rows),
        "local_tx_bytes": sum(row.local_tx_bytes for row in rows),
        "vpn_rx_bytes": sum(row.vpn_rx_bytes for row in rows),
        "vpn_tx_bytes": sum(row.vpn_tx_bytes for row in rows),
    }


async def get_traffic_usage_summary(session: AsyncSession) -> dict:
    current = await get_current_traffic_usage(session)
    if current is None:
        return {
            "current": None,
            "last_hour": None,
            "last_day": None,
        }

    now = datetime.now(timezone.utc)
    last_hour = await _sum_aggregate_window(
        session,
        TrafficMetricMinute,
        _bucket_start(now - timedelta(hours=1), granularity="minute"),
    )
    last_day = await _sum_aggregate_window(
        session,
        TrafficMetricMinute,
        _bucket_start(now - timedelta(days=1), granularity="minute"),
    )

    return {
        "current": {
            "collected_at": current.collected_at.isoformat(),
            "local_interface_name": current.local_interface_name,
            "vpn_interface_name": current.vpn_interface_name,
            "local": {
                "rx_bytes": current.local_rx_bytes,
                "tx_bytes": current.local_tx_bytes,
            },
            "vpn": {
                "rx_bytes": current.vpn_rx_bytes,
                "tx_bytes": current.vpn_tx_bytes,
            },
        },
        "last_hour": {
            "local": {
                "rx_bytes": last_hour["local_rx_bytes"],
                "tx_bytes": last_hour["local_tx_bytes"],
            },
            "vpn": {
                "rx_bytes": last_hour["vpn_rx_bytes"],
                "tx_bytes": last_hour["vpn_tx_bytes"],
            },
        },
        "last_day": {
            "local": {
                "rx_bytes": last_day["local_rx_bytes"],
                "tx_bytes": last_day["local_tx_bytes"],
            },
            "vpn": {
                "rx_bytes": last_day["vpn_rx_bytes"],
                "tx_bytes": last_day["vpn_tx_bytes"],
            },
        },
    }
