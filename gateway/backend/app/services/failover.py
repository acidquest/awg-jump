from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, EntryNode, GatewaySettings, RoutingPolicy, TunnelStatus
from app.services.external_ip import refresh_external_ip_info
from app.services.routing import apply_routing_plan
from app.services.runtime import probe_node_latency_details, resolve_tunnel_probe_target, start_tunnel


FAILOVER_DISCONNECT_GRACE = timedelta(minutes=3)
STARTUP_CONNECT_RETRIES = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def list_nodes_in_order(db: AsyncSession) -> list[EntryNode]:
    return (
        await db.execute(select(EntryNode).order_by(EntryNode.position.asc(), EntryNode.id.asc()))
    ).scalars().all()


async def append_node_to_order(db: AsyncSession, node: EntryNode) -> None:
    max_position = await db.scalar(select(EntryNode.position).order_by(EntryNode.position.desc(), EntryNode.id.desc()).limit(1))
    node.position = 0 if max_position is None else max_position + 1
    db.add(node)


async def normalize_node_order(db: AsyncSession) -> list[EntryNode]:
    nodes = await list_nodes_in_order(db)
    changed = False
    for index, node in enumerate(nodes):
        if node.position != index:
            node.position = index
            db.add(node)
            changed = True
    if changed:
        await db.flush()
    return nodes


async def move_node_by_direction(db: AsyncSession, node: EntryNode, direction: str) -> list[EntryNode]:
    nodes = await normalize_node_order(db)
    active_index = next((index for index, item in enumerate(nodes) if item.is_active), None)
    current_index = next(index for index, item in enumerate(nodes) if item.id == node.id)
    if direction == "up":
        target_index = current_index - 1
    elif direction == "down":
        target_index = current_index + 1
    else:
        raise ValueError("Unsupported move direction")

    if target_index < 0 or target_index >= len(nodes):
        return nodes
    if active_index == 0 and not node.is_active and target_index == 0:
        return nodes

    nodes[current_index], nodes[target_index] = nodes[target_index], nodes[current_index]
    for index, item in enumerate(nodes):
        item.position = index
        db.add(item)
    await db.flush()
    return nodes


async def remove_node_from_order(db: AsyncSession, node_id: int) -> None:
    nodes = [item for item in await list_nodes_in_order(db) if item.id != node_id]
    for index, item in enumerate(nodes):
        item.position = index
        db.add(item)
    await db.flush()


async def assign_active_node(
    db: AsyncSession,
    settings_row: GatewaySettings,
    node: EntryNode,
    *,
    record_event: bool,
    reset_latency: bool = True,
    event_type: str = "entry_node.activated",
    event_payload: dict | None = None,
) -> None:
    nodes = await normalize_node_order(db)
    for item in nodes:
        item.is_active = False
        db.add(item)
    remaining = [item for item in nodes if item.id != node.id]

    node.is_active = True
    node.position = 0
    if reset_latency:
        node.latest_latency_ms = None
        node.latest_latency_at = None
    node.last_error = None if resolve_tunnel_probe_target(node) else "Latency probe target is not configured"
    settings_row.active_entry_node_id = node.id
    settings_row.failover_unhealthy_since = None
    settings_row.failover_last_error = None
    settings_row.failover_last_event_at = utcnow()
    settings_row.active_node_connected_at_epoch = None

    db.add(node)
    db.add(settings_row)
    for index, item in enumerate(remaining, start=1):
        item.position = index
        db.add(item)
    if record_event:
        payload = {"entry_node_id": node.id}
        if event_payload:
            payload.update(event_payload)
        db.add(AuditEvent(event_type=event_type, payload=payload))
    await db.flush()


async def mark_failover_healthy(db: AsyncSession, settings_row: GatewaySettings) -> None:
    if settings_row.failover_unhealthy_since is None and settings_row.failover_last_error is None:
        return
    settings_row.failover_unhealthy_since = None
    settings_row.failover_last_error = None
    db.add(settings_row)
    await db.flush()


async def start_tunnel_with_retries(
    db: AsyncSession,
    node: EntryNode,
    settings_row: GatewaySettings,
    *,
    retries: int = STARTUP_CONNECT_RETRIES,
) -> tuple[dict, dict[str, str | float | None]]:
    result: dict = {"status": TunnelStatus.error.value, "error": "Tunnel start was not attempted"}
    probe: dict[str, str | float | None] = {
        "latency_ms": None,
        "target": None,
        "via_interface": None,
        "method": None,
    }

    for attempt in range(1, retries + 1):
        result = await start_tunnel(db, node, settings_row)
        if result.get("status") != TunnelStatus.running.value:
            continue
        probe = probe_node_latency_details(node, prefer_tunnel=True)
        latency_ms = probe["latency_ms"]
        node.latest_latency_ms = latency_ms if isinstance(latency_ms, float) else None
        node.latest_latency_at = utcnow()
        if latency_ms is not None:
            node.last_error = None
            db.add(node)
            await db.flush()
            return result, probe
        node.last_error = "Tunnel probe failed after startup"
        settings_row.tunnel_status = TunnelStatus.error.value
        settings_row.tunnel_last_error = (
            f"Tunnel connected but probe failed for {node.name} "
            f"(attempt {attempt}/{retries})"
        )
        db.add(node)
        db.add(settings_row)
        await db.flush()
    return result, probe


async def failover_to_next_available(
    db: AsyncSession,
    settings_row: GatewaySettings,
    *,
    reason: str,
    failed_node_id: int | None = None,
) -> EntryNode | None:
    nodes = await normalize_node_order(db)
    candidates = [
        node for node in nodes
        if node.id != failed_node_id
    ]
    for candidate in candidates:
        result, probe = await start_tunnel_with_retries(db, candidate, settings_row)
        if result.get("status") != TunnelStatus.running.value:
            continue
        latency_ms = probe["latency_ms"]
        if not isinstance(latency_ms, float):
            continue
        await assign_active_node(
            db,
            settings_row,
            candidate,
            record_event=True,
            reset_latency=False,
            event_type="entry_node.failover_activated",
            event_payload={"reason": reason},
        )
        policy = await db.get(RoutingPolicy, 1)
        if policy is not None:
            try:
                apply_routing_plan(settings_row, policy, candidate)
                settings_row.tunnel_last_error = None
            except RuntimeError as exc:
                settings_row.tunnel_last_error = str(exc)
        settings_row.failover_last_event_at = utcnow()
        settings_row.failover_last_error = None
        db.add(settings_row)
        await db.flush()
        await refresh_external_ip_info(db, settings_row, policy, force=True)
        return candidate

    settings_row.failover_last_event_at = utcnow()
    settings_row.failover_last_error = reason
    db.add(settings_row)
    await db.flush()
    return None


async def evaluate_failover_health(db: AsyncSession, settings_row: GatewaySettings) -> None:
    if not settings_row.failover_enabled or not settings_row.gateway_enabled or settings_row.active_entry_node_id is None:
        await mark_failover_healthy(db, settings_row)
        return

    active_node = await db.get(EntryNode, settings_row.active_entry_node_id)
    if active_node is None:
        await mark_failover_healthy(db, settings_row)
        return

    live_running = settings_row.tunnel_status == TunnelStatus.running.value
    if live_running:
        probe = probe_node_latency_details(active_node, prefer_tunnel=True)
        latency_ms = probe["latency_ms"]
        unhealthy_reason = None if latency_ms is not None else "Active tunnel probe failed"
    else:
        unhealthy_reason = settings_row.tunnel_last_error or "Active tunnel is not running"

    if unhealthy_reason is None:
        await mark_failover_healthy(db, settings_row)
        return

    now = utcnow()
    if settings_row.failover_unhealthy_since is None:
        settings_row.failover_unhealthy_since = now
        settings_row.failover_last_error = unhealthy_reason
        db.add(settings_row)
        await db.flush()
        return

    settings_row.failover_last_error = unhealthy_reason
    if now - settings_row.failover_unhealthy_since < FAILOVER_DISCONNECT_GRACE:
        db.add(settings_row)
        await db.flush()
        return

    await failover_to_next_available(
        db,
        settings_row,
        reason=(
            f"Active node {active_node.name} became unavailable for more than "
            f"{int(FAILOVER_DISCONNECT_GRACE.total_seconds() // 60)} minutes: {unhealthy_reason}"
        ),
        failed_node_id=active_node.id,
    )
