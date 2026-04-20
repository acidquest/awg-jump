from __future__ import annotations

import ipaddress
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, commit_with_lock, prepare_session
from app.models import EntryNode, GatewaySettings, RoutingPolicy, TrackedDevice, TrackedDeviceFlowState, TrackedDeviceIp
from app.services.routing import apply_local_passthrough, apply_routing_plan, build_routing_plan
from app.services.traffic_sources import source_selectors


DEVICE_TRACKING_INTERVAL_SECONDS = 30
PING_TIMEOUT_SECONDS = 1
FLOW_RETENTION_MULTIPLIER = 4
NEIGHBOR_REACHABLE_STATES = {"REACHABLE", "PERMANENT"}
LOOPBACK_NETWORK = ipaddress.ip_network("127.0.0.0/8")


@dataclass
class FlowObservation:
    flow_key: str
    source_ip: str
    bytes_total: int
    route_target: str


@dataclass
class NeighborInfo:
    ip_address: str
    mac_address: str | None
    state: str | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _parse_conntrack_output(output: str, *, local_mark: str = "0x1", vpn_mark: str = "0x2") -> list[FlowObservation]:
    observations: list[FlowObservation] = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, list[str]] = {}
        for token in parts:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            values.setdefault(key, []).append(value)
        source_ip = (values.get("src") or [None])[0]
        dest_ip = (values.get("dst") or [None])[0]
        if not source_ip or not dest_ip:
            continue

        bytes_total = 0
        for candidate in values.get("bytes", []):
            try:
                bytes_total = max(bytes_total, int(candidate))
            except ValueError:
                continue

        route_target = "unknown"
        for mark in values.get("mark", []):
            if mark == local_mark:
                route_target = "local"
                break
            if mark == vpn_mark:
                route_target = "vpn"
                break

        observations.append(
            FlowObservation(
                flow_key=f"{parts[0]}|{source_ip}|{dest_ip}|{values.get('sport', [''])[0]}|{values.get('dport', [''])[0]}",
                source_ip=source_ip,
                bytes_total=bytes_total,
                route_target=route_target,
            )
        )
    return observations


def _parse_ip_neigh_output(output: str) -> dict[str, NeighborInfo]:
    neighbors: dict[str, NeighborInfo] = {}
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        ip_address = parts[0]
        mac_address = None
        state = parts[-1] if len(parts) > 1 else None
        if "lladdr" in parts:
            index = parts.index("lladdr")
            if index + 1 < len(parts):
                mac_address = parts[index + 1].lower()
        neighbors[ip_address] = NeighborInfo(ip_address=ip_address, mac_address=mac_address, state=state)
    return neighbors


def _normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower()


def _ip_in_selectors(ip_address: str, selectors: list[str]) -> bool:
    try:
        ip_value = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    if ip_value in LOOPBACK_NETWORK:
        return False
    for selector in selectors:
        try:
            if ip_value in ipaddress.ip_network(selector, strict=False):
                return True
        except ValueError:
            continue
    return False


def _resolve_hostname(ip_address: str) -> str | None:
    try:
        host, _aliases, _ips = socket.gethostbyaddr(ip_address)
        return host
    except OSError:
        return None


def _presence_from_neighbor(neighbor: NeighborInfo | None) -> tuple[bool, str | None]:
    if neighbor is None:
        return False, None
    if neighbor.mac_address and (neighbor.state in NEIGHBOR_REACHABLE_STATES or neighbor.state is None):
        return True, neighbor.mac_address
    return False, neighbor.mac_address


def _flow_has_fresh_traffic(previous_bytes: int | None, current_bytes: int) -> bool:
    if previous_bytes is None:
        return True
    return current_bytes != previous_bytes


def _flow_delta(previous_bytes: int | None, current_bytes: int) -> int:
    if previous_bytes is None:
        return max(current_bytes, 0)
    if current_bytes >= previous_bytes:
        return current_bytes - previous_bytes
    return max(current_bytes, 0)


def _ping(ip_address: str) -> bool:
    rc, _ = _run(["ping", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), ip_address])
    return rc == 0


def _matches_search(device: TrackedDevice, search: str) -> bool:
    if not search:
        return True
    candidate = search.lower()
    for value in [device.manual_alias, device.hostname, device.current_ip, device.mac_address]:
        if value and candidate in value.lower():
            return True
    return False


async def _load_neighbors() -> dict[str, NeighborInfo]:
    rc, out = _run(["ip", "neigh", "show"])
    if rc != 0:
        return {}
    return _parse_ip_neigh_output(out)


async def _load_conntrack_observations() -> list[FlowObservation]:
    rc, out = _run(["conntrack", "-L", "-o", "extended"])
    if rc != 0:
        return []
    return _parse_conntrack_output(out, local_mark=settings.fwmark_local, vpn_mark=settings.fwmark_vpn)


def _dedupe_flow_observations(observations: list[FlowObservation]) -> list[FlowObservation]:
    by_flow_key: dict[str, FlowObservation] = {}
    for item in observations:
        existing = by_flow_key.get(item.flow_key)
        if existing is None:
            by_flow_key[item.flow_key] = item
            continue
        existing.bytes_total = max(existing.bytes_total, item.bytes_total)
        if existing.route_target == "unknown" and item.route_target != "unknown":
            existing.route_target = item.route_target
    return list(by_flow_key.values())


async def _load_device_by_ip(session: AsyncSession, ip_address: str) -> TrackedDevice | None:
    return await session.scalar(
        select(TrackedDevice).where(
            or_(TrackedDevice.current_ip == ip_address, TrackedDevice.identity_key == f"ip:{ip_address}")
        )
    )


async def _load_device_by_mac(session: AsyncSession, mac_address: str | None) -> TrackedDevice | None:
    if not mac_address:
        return None
    return await session.scalar(select(TrackedDevice).where(TrackedDevice.mac_address == mac_address))


async def _upsert_ip_history(session: AsyncSession, device: TrackedDevice, ip_address: str, now: datetime) -> None:
    rows = (await session.scalars(select(TrackedDeviceIp).where(TrackedDeviceIp.device_id == device.id))).all()
    found = False
    for row in rows:
        row.is_current = row.ip_address == ip_address
        if row.is_current:
            row.last_seen_at = now
            found = True
        session.add(row)
    if not found:
        session.add(
            TrackedDeviceIp(
                device=device,
                ip_address=ip_address,
                is_current=True,
                first_seen_at=now,
                last_seen_at=now,
            )
        )


async def _merge_devices(session: AsyncSession, target: TrackedDevice, source: TrackedDevice) -> TrackedDevice:
    if target.id == source.id:
        return target
    target.first_seen_at = min(_as_utc(target.first_seen_at) or _utcnow(), _as_utc(source.first_seen_at) or _utcnow())
    target.last_seen_at = max(_as_utc(target.last_seen_at) or _utcnow(), _as_utc(source.last_seen_at) or _utcnow())
    source_last_traffic_at = _as_utc(source.last_traffic_at)
    target_last_traffic_at = _as_utc(target.last_traffic_at)
    if source_last_traffic_at and (target_last_traffic_at is None or source_last_traffic_at > target_last_traffic_at):
        target.last_traffic_at = source_last_traffic_at
    source_last_present_at = _as_utc(source.last_present_at)
    target_last_present_at = _as_utc(target.last_present_at)
    if source_last_present_at and (target_last_present_at is None or source_last_present_at > target_last_present_at):
        target.last_present_at = source_last_present_at
    source_last_absent_at = _as_utc(source.last_absent_at)
    target_last_absent_at = _as_utc(target.last_absent_at)
    if source_last_absent_at and (target_last_absent_at is None or source_last_absent_at > target_last_absent_at):
        target.last_absent_at = source_last_absent_at
    target.total_bytes += source.total_bytes
    target.is_marked = target.is_marked or source.is_marked
    if target.forced_route_target == "none" and source.forced_route_target != "none":
        target.forced_route_target = source.forced_route_target
    if not target.manual_alias:
        target.manual_alias = source.manual_alias
    if not target.hostname:
        target.hostname = source.hostname
    if not target.current_ip:
        target.current_ip = source.current_ip
    if not target.mac_address:
        target.mac_address = source.mac_address

    ip_rows = (await session.scalars(select(TrackedDeviceIp).where(TrackedDeviceIp.device_id == source.id))).all()
    for row in ip_rows:
        row.device_id = target.id
        session.add(row)

    flow_rows = (await session.scalars(select(TrackedDeviceFlowState).where(TrackedDeviceFlowState.device_id == source.id))).all()
    for row in flow_rows:
        row.device_id = target.id
        session.add(row)

    await session.delete(source)
    session.add(target)
    return target


async def _resolve_device(session: AsyncSession, *, ip_address: str, mac_address: str | None, now: datetime) -> TrackedDevice:
    normalized_mac = _normalize_mac(mac_address)
    device_by_mac = await _load_device_by_mac(session, normalized_mac)
    device_by_ip = await _load_device_by_ip(session, ip_address)

    if device_by_mac and device_by_ip and device_by_mac.id != device_by_ip.id:
        device = await _merge_devices(session, device_by_mac, device_by_ip)
    else:
        device = device_by_mac or device_by_ip

    if device is None:
        device = TrackedDevice(
            identity_key=f"mac:{normalized_mac}" if normalized_mac else f"ip:{ip_address}",
            identity_source="mac" if normalized_mac else "ip",
            mac_address=normalized_mac,
            current_ip=ip_address,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(device)
    else:
        if normalized_mac and device.identity_source != "mac":
            device.identity_key = f"mac:{normalized_mac}"
            device.identity_source = "mac"
        if normalized_mac:
            device.mac_address = normalized_mac
        device.current_ip = ip_address
        device.last_seen_at = now

    await _upsert_ip_history(session, device, ip_address, now)
    return device


async def _reload_device_route_runtime() -> None:
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        settings_row = await session.get(GatewaySettings, 1)
        policy = await session.get(RoutingPolicy, 1)
        if settings_row is None or policy is None:
            return
        active_node = await session.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
        if not settings_row.gateway_enabled:
            apply_local_passthrough(settings_row)
            return
        plan = build_routing_plan(settings_row, policy, active_node)
        if not plan["safe_to_apply"]:
            return
        try:
            apply_routing_plan(settings_row, policy, active_node)
            policy.last_error = None
        except RuntimeError as exc:
            policy.last_error = str(exc)
        session.add(policy)
        await commit_with_lock(session)


async def collect_device_inventory(session: AsyncSession, settings_row: GatewaySettings | None) -> None:
    if settings_row is None or not settings_row.device_tracking_enabled:
        return

    now = _utcnow()
    selectors = source_selectors(settings_row)
    neighbors = await _load_neighbors()
    observations = _dedupe_flow_observations(
        [item for item in await _load_conntrack_observations() if _ip_in_selectors(item.source_ip, selectors)]
    )
    pending_flow_states: dict[str, TrackedDeviceFlowState] = {}
    device_route_overrides_dirty = False

    for item in observations:
        flow_state = pending_flow_states.get(item.flow_key)
        if flow_state is None:
            flow_state = await session.get(TrackedDeviceFlowState, item.flow_key)
        previous_bytes = flow_state.last_bytes if flow_state is not None else None
        if not _flow_has_fresh_traffic(previous_bytes, item.bytes_total):
            continue

        neighbor = neighbors.get(item.source_ip)
        existing_device = await _load_device_by_mac(session, _normalize_mac(neighbor.mac_address) if neighbor else None)
        if existing_device is None:
            existing_device = await _load_device_by_ip(session, item.source_ip)
        previous_ip = existing_device.current_ip if existing_device is not None else None
        device = await _resolve_device(
            session,
            ip_address=item.source_ip,
            mac_address=neighbor.mac_address if neighbor else None,
            now=now,
        )
        if device.forced_route_target != "none" and previous_ip != device.current_ip:
            device_route_overrides_dirty = True
        if not device.hostname:
            device.hostname = _resolve_hostname(item.source_ip)
        device.last_traffic_at = now
        device.last_present_at = now
        device.is_active = True
        device.is_present = True
        if item.route_target != "unknown":
            device.last_route_target = item.route_target
        session.add(device)

        delta = _flow_delta(previous_bytes, item.bytes_total)
        if flow_state is None:
            flow_state = TrackedDeviceFlowState(
                flow_key=item.flow_key,
                device=device,
                source_ip=item.source_ip,
                route_target=item.route_target,
                last_bytes=item.bytes_total,
                last_seen_at=now,
            )
        else:
            flow_state.device_id = device.id
            flow_state.source_ip = item.source_ip
            flow_state.route_target = item.route_target
            flow_state.last_bytes = item.bytes_total
            flow_state.last_seen_at = now
        pending_flow_states[item.flow_key] = flow_state
        device.total_bytes += max(delta, 0)
        session.add(flow_state)
        session.add(device)

    cutoff = now - timedelta(seconds=max(settings_row.device_activity_timeout_seconds * FLOW_RETENTION_MULTIPLIER, 600))
    await session.execute(delete(TrackedDeviceFlowState).where(TrackedDeviceFlowState.last_seen_at < cutoff))

    devices = (await session.scalars(select(TrackedDevice).order_by(TrackedDevice.last_seen_at.desc()))).all()
    timeout_cutoff = now - timedelta(seconds=settings_row.device_activity_timeout_seconds)
    for device in devices:
        last_traffic_at = _as_utc(device.last_traffic_at)
        is_active = last_traffic_at is not None and last_traffic_at >= timeout_cutoff
        is_present = is_active
        if not is_active and device.current_ip:
            ping_ok = _ping(device.current_ip)
            arp_present, mac_address = _presence_from_neighbor(neighbors.get(device.current_ip))
            is_present = ping_ok or arp_present
            if mac_address and not device.mac_address:
                device.mac_address = mac_address
                device.identity_key = f"mac:{mac_address}"
                device.identity_source = "mac"
        device.is_active = bool(is_active)
        device.is_present = bool(is_present)
        device.last_presence_check_at = now
        if is_present:
            device.last_present_at = now
        else:
            device.last_absent_at = now
        session.add(device)

    if device_route_overrides_dirty:
        await commit_with_lock(session, metrics=True)
        await _reload_device_route_runtime()


async def get_devices_payload(
    session: AsyncSession,
    *,
    scope: str = "all",
    status: str = "all",
    search: str = "",
    include_ip_history: bool = False,
) -> dict[str, Any]:
    devices = (await session.scalars(select(TrackedDevice).order_by(TrackedDevice.last_seen_at.desc(), TrackedDevice.id.desc()))).all()
    history_by_device: dict[int, list[TrackedDeviceIp]] = {}
    if include_ip_history and devices:
        rows = (
            await session.scalars(
                select(TrackedDeviceIp)
                .where(TrackedDeviceIp.device_id.in_([device.id for device in devices]))
                .order_by(TrackedDeviceIp.device_id.asc(), TrackedDeviceIp.last_seen_at.desc())
            )
        ).all()
        for row in rows:
            history_by_device.setdefault(row.device_id, []).append(row)

    filtered: list[TrackedDevice] = []
    for device in devices:
        if scope == "marked" and not device.is_marked:
            continue
        if status == "active" and not device.is_active:
            continue
        if status == "present" and not device.is_present:
            continue
        if status == "inactive" and (device.is_active or device.is_present):
            continue
        if not _matches_search(device, search):
            continue
        filtered.append(device)

    return {
        "scope": scope,
        "status": status,
        "search": search,
        "summary": {
            "total": len(filtered),
            "all_devices": len(devices),
            "marked": sum(1 for device in filtered if device.is_marked),
            "active": sum(1 for device in filtered if device.is_active),
            "present": sum(1 for device in filtered if device.is_present),
            "inactive": sum(1 for device in filtered if not device.is_active and not device.is_present),
        },
        "devices": [
            {
                "id": device.id,
                "identity_key": device.identity_key,
                "identity_source": device.identity_source,
                "mac_address": device.mac_address,
                "current_ip": device.current_ip,
                "hostname": device.hostname,
                "manual_alias": device.manual_alias,
                "display_name": device.manual_alias or device.hostname or device.current_ip or device.mac_address or device.identity_key,
                "is_marked": device.is_marked,
                "forced_route_target": device.forced_route_target,
                "is_active": device.is_active,
                "is_present": device.is_present,
                "presence_state": "active" if device.is_active else "present" if device.is_present else "inactive",
                "last_route_target": device.last_route_target,
                "total_bytes": device.total_bytes,
                "first_seen_at": _as_utc(device.first_seen_at).isoformat() if device.first_seen_at else None,
                "last_seen_at": _as_utc(device.last_seen_at).isoformat() if device.last_seen_at else None,
                "last_traffic_at": _as_utc(device.last_traffic_at).isoformat() if device.last_traffic_at else None,
                "last_presence_check_at": _as_utc(device.last_presence_check_at).isoformat() if device.last_presence_check_at else None,
                "last_present_at": _as_utc(device.last_present_at).isoformat() if device.last_present_at else None,
                "last_absent_at": _as_utc(device.last_absent_at).isoformat() if device.last_absent_at else None,
                "ip_history": [
                    {
                        "ip_address": row.ip_address,
                        "is_current": row.is_current,
                        "first_seen_at": _as_utc(row.first_seen_at).isoformat() if row.first_seen_at else None,
                        "last_seen_at": _as_utc(row.last_seen_at).isoformat() if row.last_seen_at else None,
                    }
                    for row in history_by_device.get(device.id, [])
                ] if include_ip_history else [],
            }
            for device in filtered
        ],
    }
