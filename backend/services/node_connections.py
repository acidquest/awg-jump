"""Persistent upstream outage and reconnect diagnostics (UTC)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.upstream_node import NodeConnectionIncident, NodeConnectionAttempt, UpstreamNode


def utcnow() -> datetime:
    # SQLite DateTime returns naive values; store and compare consistently in UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


async def observe_connection(session: AsyncSession, node: UpstreamNode, alive: bool) -> None:
    # A failover is part of the same service outage until any selected tunnel recovers.
    incident = await session.scalar(
        select(NodeConnectionIncident)
        .where(NodeConnectionIncident.ended_at.is_(None))
        .order_by(NodeConnectionIncident.id)
        .limit(1)
    )
    if alive:
        if incident:
            incident.ended_at = utcnow()
    elif incident is None:
        session.add(NodeConnectionIncident(
            node_id=node.id, node_name=node.name, started_at=utcnow(),
        ))
    await session.commit()


async def connection_statistics(session: AsyncSession, days: int, page: int, page_size: int) -> dict:
    now = utcnow()
    start = now - timedelta(days=days)
    incidents = (await session.scalars(
        select(NodeConnectionIncident)
        .where(NodeConnectionIncident.ended_at.is_(None) | (NodeConnectionIncident.ended_at >= start))
        .order_by(NodeConnectionIncident.started_at.desc())
    )).all()
    attempts = (await session.scalars(
        select(NodeConnectionAttempt)
        .where(NodeConnectionAttempt.created_at >= start)
        .order_by(NodeConnectionAttempt.created_at.desc())
    )).all()
    downtime = sum(
        max(0, (min(i.ended_at or now, now) - max(i.started_at, start)).total_seconds())
        for i in incidents
    )
    per_node = {}
    for attempt in attempts:
        row = per_node.setdefault((attempt.node_id, attempt.node_name), {
            "node_id": attempt.node_id, "node_name": attempt.node_name, "attempts": 0, "errors": 0,
        })
        row["attempts"] += 1
        row["errors"] += int(attempt.error is not None)

    offset = (page - 1) * page_size
    return {
        "days": days,
        "disconnects": sum(i.started_at >= start for i in incidents),
        "downtime_seconds": downtime,
        "ongoing_since": next((_stamp(i.started_at) for i in incidents if i.ended_at is None), None),
        "attempts": len(attempts),
        "attempt_errors": sum(a.error is not None for a in attempts),
        "per_node": list(per_node.values()),
        "incidents": [{
            "id": i.id,
            "node_name": i.node_name,
            "started_at": _stamp(i.started_at),
            "ended_at": _stamp(i.ended_at),
            "duration_seconds": ((i.ended_at or now) - i.started_at).total_seconds(),
        } for i in incidents[offset:offset + page_size]],
        "total_incidents": len(incidents),
        "page": page,
        "total_pages": max(1, (len(incidents) + page_size - 1) // page_size),
        "recent_attempts": [{
            "id": a.id,
            "node_name": a.node_name,
            "created_at": _stamp(a.created_at),
            "applied": a.applied,
            "error": a.error,
        } for a in attempts[:20]],
    }
