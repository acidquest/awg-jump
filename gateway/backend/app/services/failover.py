from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, EntryNode, GatewaySettings, RoutingPolicy, TunnelStatus
from app.services.external_ip import refresh_external_ip_info
from app.services.routing import apply_routing_plan
from app.services.runtime import probe_node_latency_details, resolve_tunnel_probe_target, start_tunnel
from app.services.runtime_state import (
    get_failover_runtime_state,
    get_node_runtime_state,
    get_tunnel_runtime_state,
    set_failover_runtime_state,
    update_node_runtime_state,
)


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
        update_node_runtime_state(
            node.id,
            latency_ms=None,
            latency_at=None,
            latency_target=None,
            latency_via_interface=None,
            latency_method=None,
            last_error=None,
        )
    else:
        get_node_runtime_state(node.id)
    update_node_runtime_state(
        node.id,
        latency_ms=get_node_runtime_state(node.id).latency_ms,
        latency_at=get_node_runtime_state(node.id).latency_at,
        latency_target=get_node_runtime_state(node.id).latency_target,
        latency_via_interface=get_node_runtime_state(node.id).latency_via_interface,
        latency_method=get_node_runtime_state(node.id).latency_method,
        last_error=None if resolve_tunnel_probe_target(node) else "Latency probe target is not configured",
    )
    settings_row.active_entry_node_id = node.id
    settings_row.active_entry_node = node
    set_failover_runtime_state(
        unhealthy_since=None,
        last_error=None,
        last_event_at=utcnow(),
    )
    get_tunnel_runtime_state().connected_at_epoch = None

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
    failover_state = get_failover_runtime_state()
    if failover_state.unhealthy_since is None and failover_state.last_error is None:
        return
    failover_state.unhealthy_since = None
    failover_state.last_error = None


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
        update_node_runtime_state(
            node.id,
            latency_ms=latency_ms if isinstance(latency_ms, float) else None,
            latency_at=utcnow(),
            latency_target=probe["target"] if isinstance(probe["target"], str) else None,
            latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
            latency_method=probe["method"] if isinstance(probe["method"], str) else None,
            last_error=None,
        )
        if latency_ms is not None:
            return result, probe
        update_node_runtime_state(
            node.id,
            latency_ms=None,
            latency_at=utcnow(),
            latency_target=probe["target"] if isinstance(probe["target"], str) else None,
            latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
            latency_method=probe["method"] if isinstance(probe["method"], str) else None,
            last_error="Tunnel probe failed after startup",
        )
        tunnel_state = get_tunnel_runtime_state()
        tunnel_state.status = TunnelStatus.error.value
        tunnel_state.last_error = (
            f"Tunnel connected but probe failed for {node.name} "
            f"(attempt {attempt}/{retries})"
        )
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
                get_tunnel_runtime_state().last_error = None
            except RuntimeError as exc:
                get_tunnel_runtime_state().last_error = str(exc)
        failover_state = get_failover_runtime_state()
        failover_state.last_event_at = utcnow()
        failover_state.last_error = None
        await refresh_external_ip_info(settings_row, policy, force=True)
        return candidate

    failover_state = get_failover_runtime_state()
    failover_state.last_event_at = utcnow()
    failover_state.last_error = reason
    return None


async def evaluate_failover_health(db: AsyncSession, settings_row: GatewaySettings) -> None:
    if not settings_row.failover_enabled or not settings_row.gateway_enabled or settings_row.active_entry_node_id is None:
        await mark_failover_healthy(db, settings_row)
        return

    active_node = await db.get(EntryNode, settings_row.active_entry_node_id)
    if active_node is None:
        await mark_failover_healthy(db, settings_row)
        return

    live_running = get_tunnel_runtime_state().status == TunnelStatus.running.value
    if live_running:
        probe = probe_node_latency_details(active_node, prefer_tunnel=True)
        latency_ms = probe["latency_ms"]
        unhealthy_reason = None if latency_ms is not None else "Active tunnel probe failed"
    else:
        unhealthy_reason = get_tunnel_runtime_state().last_error or "Active tunnel is not running"

    if unhealthy_reason is None:
        await mark_failover_healthy(db, settings_row)
        return

    now = utcnow()
    failover_state = get_failover_runtime_state()
    if failover_state.unhealthy_since is None:
        failover_state.unhealthy_since = now
        failover_state.last_error = unhealthy_reason
        return

    failover_state.last_error = unhealthy_reason
    if now - failover_state.unhealthy_since < FAILOVER_DISCONNECT_GRACE:
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
