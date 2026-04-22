from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, AuditEvent, EntryNode, FirstNodeBootstrapLog, FirstNodeBootstrapStatus, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.conf_parser import parse_peer_conf, render_peer_conf, split_endpoint
from app.services.first_node_bootstrap import bootstrap_first_node, cleanup_bootstrap_queue, get_bootstrap_queue
from app.services.external_ip import refresh_external_ip_info
from app.services.failover import (
    append_node_to_order,
    assign_active_node,
    list_nodes_in_order,
    move_node_by_direction,
    remove_node_from_order,
    start_tunnel_with_retries,
)
from app.services.routing import apply_local_passthrough, apply_routing_plan
from app.services.runtime import (
    probe_node_latency_details,
    probe_udp_endpoint,
    remove_runtime_config,
    resolve_live_tunnel_status,
    resolve_tunnel_probe_target,
    stop_tunnel,
)
from app.services.runtime_state import (
    clear_node_runtime_state,
    get_failover_runtime_state,
    get_node_runtime_state,
    get_tunnel_runtime_state,
    should_refresh_node_latency,
    update_node_runtime_state,
)


router = APIRouter(prefix="/api/nodes", tags=["entry-nodes"])


class ImportConfRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    conf_text: str = Field(min_length=1)


class EntryNodeUpdate(BaseModel):
    name: str


class EntryNodeRawUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    conf_text: str = Field(min_length=1)


class EntryNodeVisualUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=3)
    probe_ip: str | None = None
    public_key: str = Field(min_length=3)
    private_key: str = Field(min_length=3)
    preshared_key: str | None = None
    tunnel_address: str = Field(min_length=3)
    dns_servers: list[str] = []
    allowed_ips: list[str] = []
    persistent_keepalive: int | None = None
    status_api_url: str | None = None


class FirstNodeBootstrapRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    ssh_user: str = Field(min_length=1, max_length=128)
    ssh_password: str = Field(min_length=1, max_length=512)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    remote_dir: str = Field(default="/opt/awg-jump", min_length=1, max_length=512)
    docker_namespace: str = Field(min_length=1, max_length=255)
    image_tag: str = Field(default="latest", min_length=1, max_length=128)


class FailoverSettingsUpdate(BaseModel):
    enabled: bool


class EntryNodeMoveRequest(BaseModel):
    direction: str = Field(pattern="^(up|down)$")


def _serialize_bootstrap_log(log: FirstNodeBootstrapLog) -> dict:
    return {
        "id": log.id,
        "target_host": log.target_host,
        "ssh_user": log.ssh_user,
        "ssh_port": log.ssh_port,
        "remote_dir": log.remote_dir,
        "docker_namespace": log.docker_namespace,
        "image_tag": log.image_tag,
        "status": log.status,
        "log_output": log.log_output,
        "finished_at": log.finished_at.isoformat() if log.finished_at else None,
        "created_at": log.created_at.isoformat(),
    }


def _to_payload(
    node: EntryNode,
    *,
    udp_status: str | None = None,
    udp_detail: str | None = None,
    latency_ms: float | None = None,
    latency_target: str | None = None,
    latency_via_interface: str | None = None,
    latency_method: str | None = None,
) -> dict:
    node_state = get_node_runtime_state(node.id)
    parsed_status_api_url = parse_peer_conf(node.raw_conf, name=node.name).status_api_url if node.raw_conf else None
    return {
        "id": node.id,
        "name": node.name,
        "raw_conf": node.raw_conf,
        "endpoint": node.endpoint,
        "endpoint_host": node.endpoint_host,
        "endpoint_port": node.endpoint_port,
        "probe_ip": node.probe_ip,
        "public_key": node.public_key,
        "private_key": node.private_key,
        "preshared_key": node.preshared_key,
        "tunnel_address": node.tunnel_address,
        "dns_servers": node.dns_servers,
        "allowed_ips": node.allowed_ips,
        "persistent_keepalive": node.persistent_keepalive,
        "obfuscation": node.obfuscation,
        "status_api_url": parsed_status_api_url,
        "latest_latency_ms": node_state.latency_ms if latency_ms is None else latency_ms,
        "latest_latency_at": node_state.latency_at.isoformat() if node_state.latency_at else None,
        "latest_latency_target": latency_target,
        "latest_latency_via_interface": latency_via_interface,
        "latest_latency_method": latency_method,
        "last_error": node_state.last_error,
        "udp_status": udp_status,
        "udp_detail": udp_detail,
        "is_active": node.is_active,
        "position": node.position,
        "created_at": node.created_at.isoformat(),
    }


def _refresh_latency_for_active_tunnel(node: EntryNode) -> None:
    if not node.is_active:
        update_node_runtime_state(
            node.id,
            latency_ms=None,
            latency_at=None,
            latency_target=None,
            latency_via_interface=None,
            latency_method=None,
            last_error=None,
        )
        return
    probe = probe_node_latency_details(node, prefer_tunnel=True)
    latency_ms = probe["latency_ms"]
    measured_at = datetime.now(timezone.utc)
    probe_target = resolve_tunnel_probe_target(node)
    update_node_runtime_state(
        node.id,
        latency_ms=latency_ms if isinstance(latency_ms, float) else None,
        latency_at=measured_at,
        latency_target=probe["target"] if isinstance(probe["target"], str) else None,
        latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
        latency_method=probe["method"] if isinstance(probe["method"], str) else None,
        last_error=None if latency_ms is not None else ("Latency probe target is not configured" if not probe_target else "Latency probe failed"),
    )


@router.get("")
async def list_nodes(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> list[dict]:
    nodes = await list_nodes_in_order(db)
    payloads: list[dict] = []
    for node in nodes:
        if node.is_active:
            if should_refresh_node_latency(node.id, ttl_seconds=20):
                _refresh_latency_for_active_tunnel(node)
            node_state = get_node_runtime_state(node.id)
            payloads.append(
                _to_payload(
                    node,
                    latency_ms=node_state.latency_ms,
                    latency_target=node_state.latency_target,
                    latency_via_interface=node_state.latency_via_interface,
                    latency_method=node_state.latency_method,
                )
            )
            continue
        udp_status, udp_detail = probe_udp_endpoint(node)
        probe = probe_node_latency_details(node)
        payloads.append(
            _to_payload(
                node,
                udp_status=udp_status,
                udp_detail=udp_detail,
                latency_ms=probe["latency_ms"] if isinstance(probe["latency_ms"], float) else None,
                latency_target=probe["target"] if isinstance(probe["target"], str) else None,
                latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
                latency_method=probe["method"] if isinstance(probe["method"], str) else None,
                )
            )
    return payloads


@router.get("/bootstrap-first/logs")
async def list_first_node_bootstrap_logs(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> list[dict]:
    logs = (
        await db.execute(select(FirstNodeBootstrapLog).order_by(FirstNodeBootstrapLog.id.desc()).limit(20))
    ).scalars().all()
    return [_serialize_bootstrap_log(log) for log in logs]


@router.post("/bootstrap-first")
async def start_first_node_bootstrap(
    payload: FirstNodeBootstrapRequest,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    log = FirstNodeBootstrapLog(
        target_host=payload.host.strip(),
        ssh_user=payload.ssh_user.strip(),
        ssh_port=payload.ssh_port,
        remote_dir=payload.remote_dir.strip(),
        docker_namespace=payload.docker_namespace.strip(),
        image_tag=payload.image_tag.strip(),
        status=FirstNodeBootstrapStatus.running.value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(log)
    await db.flush()
    db.add(
        AuditEvent(
            event_type="entry_node.bootstrap_first_started",
            payload={"bootstrap_log_id": log.id, "target_host": log.target_host},
        )
    )
    await db.flush()
    asyncio.create_task(
        bootstrap_first_node(
            log_id=log.id,
            host=payload.host.strip(),
            ssh_user=payload.ssh_user.strip(),
            ssh_password=payload.ssh_password,
            ssh_port=payload.ssh_port,
            remote_dir=payload.remote_dir.strip(),
            docker_namespace=payload.docker_namespace.strip(),
            image_tag=payload.image_tag.strip(),
        )
    )
    return {"bootstrap_log_id": log.id}


@router.get("/bootstrap-first/{log_id}/stream")
async def stream_first_node_bootstrap_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> StreamingResponse:
    log = await db.get(FirstNodeBootstrapLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Bootstrap log not found")
    queue = get_bootstrap_queue(log_id)

    async def event_stream():
        existing_lines = [line for line in (log.log_output or "").splitlines() if line.strip()]
        step = 0
        for line in existing_lines:
            if line.startswith("[") and "] " in line:
                step += 1
            payload = {"step": step, "message": line, "status": log.status}
            yield f"data: {json.dumps(payload)}\n\n"
        while True:
            item = await queue.get()
            if item is None:
                yield "data: {\"finished\": true, \"status\": \"done\", \"message\": \"__done__\"}\n\n"
                cleanup_bootstrap_queue(log_id)
                break
            yield f"data: {item}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/import", status_code=201)
async def import_node(
    payload: ImportConfRequest,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    parsed = parse_peer_conf(payload.conf_text, name=payload.name)
    node = EntryNode(
        name=parsed.name,
        raw_conf=parsed.raw_conf,
        endpoint=parsed.endpoint,
        endpoint_host=parsed.endpoint_host,
        endpoint_port=parsed.endpoint_port,
        public_key=parsed.public_key,
        private_key=parsed.private_key,
        preshared_key=parsed.preshared_key,
        tunnel_address=parsed.tunnel_address,
        dns_servers=parsed.dns_servers,
        allowed_ips=parsed.allowed_ips,
        persistent_keepalive=parsed.persistent_keepalive,
        obfuscation=parsed.obfuscation,
    )
    await append_node_to_order(db, node)
    db.add(node)
    await db.flush()
    db.add(AuditEvent(event_type="entry_node.imported", payload={"entry_node_id": node.id, "name": node.name}))
    await db.flush()
    return _to_payload(node)


def _apply_parsed_node(node: EntryNode, parsed, *, name: str | None = None) -> None:
    node.name = name or parsed.name
    node.raw_conf = parsed.raw_conf
    node.endpoint = parsed.endpoint
    node.endpoint_host = parsed.endpoint_host
    node.endpoint_port = parsed.endpoint_port
    node.public_key = parsed.public_key
    node.private_key = parsed.private_key
    node.preshared_key = parsed.preshared_key
    node.tunnel_address = parsed.tunnel_address
    node.dns_servers = parsed.dns_servers
    node.allowed_ips = parsed.allowed_ips
    node.persistent_keepalive = parsed.persistent_keepalive
    node.obfuscation = parsed.obfuscation
    node.updated_at = datetime.now(timezone.utc)


@router.put("/{node_id}/raw-conf")
async def update_node_raw_conf(
    node_id: int,
    payload: EntryNodeRawUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    parsed = parse_peer_conf(payload.conf_text, name=payload.name or node.name)
    _apply_parsed_node(node, parsed, name=payload.name or node.name)
    db.add(node)
    db.add(AuditEvent(event_type="entry_node.updated_raw_conf", payload={"entry_node_id": node.id}))
    await db.flush()
    return _to_payload(node)


@router.put("/{node_id}/visual")
async def update_node_visual(
    node_id: int,
    payload: EntryNodeVisualUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    endpoint_host, endpoint_port = split_endpoint(payload.endpoint)
    raw_conf = render_peer_conf(
        private_key=payload.private_key,
        tunnel_address=payload.tunnel_address,
        dns_servers=payload.dns_servers,
        obfuscation=node.obfuscation,
        public_key=payload.public_key,
        endpoint=payload.endpoint,
        allowed_ips=payload.allowed_ips,
        preshared_key=payload.preshared_key,
        persistent_keepalive=payload.persistent_keepalive,
        status_api_url=payload.status_api_url,
    )
    parsed = parse_peer_conf(raw_conf, name=payload.name)
    _apply_parsed_node(node, parsed, name=payload.name)
    node.endpoint_host = endpoint_host
    node.endpoint_port = endpoint_port
    node.probe_ip = payload.probe_ip.strip() if payload.probe_ip else None
    db.add(node)
    db.add(AuditEvent(event_type="entry_node.updated_visual", payload={"entry_node_id": node.id}))
    await db.flush()
    return _to_payload(node)


@router.delete("/{node_id}")
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    settings_row = await db.get(GatewaySettings, 1)
    if settings_row.active_entry_node_id == node.id:
        settings_row.active_entry_node_id = None
        settings_row.active_entry_node = None
        failover_state = get_failover_runtime_state()
        failover_state.unhealthy_since = None
        failover_state.last_error = None
        db.add(settings_row)
        if settings_row.gateway_enabled:
            await stop_tunnel(settings_row)
    await db.delete(node)
    await db.flush()
    clear_node_runtime_state(node.id)
    remove_runtime_config(node.id)
    await remove_node_from_order(db, node.id)
    return {"status": "deleted"}


@router.get("/failover")
async def get_failover_settings(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    failover_state = get_failover_runtime_state()
    return {
        "enabled": settings_row.failover_enabled,
        "last_error": failover_state.last_error,
        "last_event_at": failover_state.last_event_at.isoformat() if failover_state.last_event_at else None,
    }


@router.put("/failover")
async def update_failover_settings(
    payload: FailoverSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    settings_row.failover_enabled = payload.enabled
    failover_state = get_failover_runtime_state()
    if not payload.enabled:
        failover_state.unhealthy_since = None
        failover_state.last_error = None
    db.add(settings_row)
    await db.flush()
    return {
        "enabled": settings_row.failover_enabled,
        "last_error": failover_state.last_error,
        "last_event_at": failover_state.last_event_at.isoformat() if failover_state.last_event_at else None,
    }


@router.put("/{node_id}")
async def update_node(
    node_id: int,
    payload: EntryNodeUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    node.name = payload.name
    db.add(node)
    await db.flush()
    return _to_payload(node)


@router.get("/{node_id}")
async def get_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    return _to_payload(node)


@router.post("/{node_id}/move")
async def move_node(
    node_id: int,
    payload: EntryNodeMoveRequest,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    await move_node_by_direction(db, node, payload.direction)
    db.add(AuditEvent(event_type="entry_node.moved", payload={"entry_node_id": node.id, "direction": payload.direction}))
    await db.flush()
    return {"status": "moved"}


@router.post("/{node_id}/activate")
async def activate_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")

    settings_row = await db.get(GatewaySettings, 1)
    live_status, live_error = resolve_live_tunnel_status(settings_row)
    tunnel_state = get_tunnel_runtime_state()
    tunnel_state.status = live_status
    tunnel_state.last_error = live_error
    await assign_active_node(db, settings_row, node, record_event=True)
    if not settings_row.gateway_enabled:
        policy = await db.get(RoutingPolicy, 1)
        await refresh_external_ip_info(settings_row, policy, force=True)
        return _to_payload(node)
    if live_status == "running":
        result, probe = await start_tunnel_with_retries(db, node, settings_row)
        if result["status"] == "running":
            latency_ms = probe["latency_ms"]
            update_node_runtime_state(
                node.id,
                latency_ms=latency_ms if isinstance(latency_ms, float) else None,
                latency_at=datetime.now(timezone.utc),
                latency_target=probe["target"] if isinstance(probe["target"], str) else None,
                latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
                latency_method=probe["method"] if isinstance(probe["method"], str) else None,
                last_error=None if latency_ms is not None else "Tunnel probe failed after startup",
            )
            policy = await db.get(RoutingPolicy, 1)
            try:
                apply_routing_plan(settings_row, policy, node)
                get_tunnel_runtime_state().last_error = None
            except RuntimeError as exc:
                get_tunnel_runtime_state().last_error = str(exc)
    policy = await db.get(RoutingPolicy, 1)
    await refresh_external_ip_info(settings_row, policy, force=True)
    return _to_payload(node)


@router.post("/{node_id}/probe")
async def probe_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    node = await db.get(EntryNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Entry node not found")
    _refresh_latency_for_active_tunnel(node)
    node_state = get_node_runtime_state(node.id)
    return {
        "node_id": node.id,
        "latency_ms": node_state.latency_ms,
        "measured_at": node_state.latency_at.isoformat() if node_state.latency_at else None,
    }


@router.post("/runtime/start")
async def start_active_tunnel(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    if not settings_row.gateway_enabled:
        raise HTTPException(status_code=409, detail="Gateway routing is disabled")
    if settings_row.active_entry_node_id is None:
        raise HTTPException(status_code=400, detail="No active entry node selected")
    node = await db.get(EntryNode, settings_row.active_entry_node_id)
    result, probe = await start_tunnel_with_retries(db, node, settings_row)
    if result["status"] == "running":
        latency_ms = probe["latency_ms"]
        update_node_runtime_state(
            node.id,
            latency_ms=latency_ms if isinstance(latency_ms, float) else None,
            latency_at=datetime.now(timezone.utc),
            latency_target=probe["target"] if isinstance(probe["target"], str) else None,
            latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
            latency_method=probe["method"] if isinstance(probe["method"], str) else None,
            last_error=None if latency_ms is not None else "Tunnel probe failed after startup",
        )
        policy = await db.get(RoutingPolicy, 1)
        try:
            apply_routing_plan(settings_row, policy, node)
        except RuntimeError as exc:
            get_tunnel_runtime_state().last_error = str(exc)
            result["routing_error"] = str(exc)
        result["latency_ms"] = get_node_runtime_state(node.id).latency_ms
    policy = await db.get(RoutingPolicy, 1)
    result["external_ip_info"] = await refresh_external_ip_info(settings_row, policy, force=True)
    return result


@router.post("/runtime/stop")
async def stop_active_tunnel(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    result = await stop_tunnel(settings_row)
    if not settings_row.gateway_enabled:
        apply_local_passthrough(settings_row)
    policy = await db.get(RoutingPolicy, 1)
    result["external_ip_info"] = await refresh_external_ip_info(settings_row, policy, force=True)
    return result
