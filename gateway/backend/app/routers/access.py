from __future__ import annotations

from datetime import datetime, timezone
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import commit_with_lock, get_db, get_metrics_db
from app.models import EntryNode, GatewaySettings, RoutingPolicy
from app.security import get_api_settings, require_api_control
from app.services.device_tracking import get_devices_payload
from app.services.external_ip import serialize_external_ip_info
from app.services.routing import apply_local_passthrough, apply_routing_plan, build_prefix_summary, build_routing_plan, sync_firewall_backend
from app.services.runtime import probe_node_latency_details, resolve_live_tunnel_status, start_tunnel, stop_tunnel
from app.services.runtime_state import (
    get_node_runtime_state,
    get_tunnel_runtime_state,
    should_refresh_node_latency,
    update_node_runtime_state,
)
from app.services.system_metrics import get_metrics_history
from app.services.traffic_metrics import get_traffic_usage_summary


router = APIRouter(prefix="/api/access", tags=["access"])


class EnabledPayload(BaseModel):
    enabled: bool


async def _load_runtime_state(db: AsyncSession, settings_row: GatewaySettings) -> tuple[RoutingPolicy, EntryNode | None, dict, dict | None]:
    policy = await db.get(RoutingPolicy, 1)
    live_status, live_error = resolve_live_tunnel_status(settings_row)

    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    probe: dict | None = None
    if active_node is not None:
        if should_refresh_node_latency(active_node.id, ttl_seconds=20):
            probe = probe_node_latency_details(active_node, prefer_tunnel=True)
            latency_ms = probe["latency_ms"] if isinstance(probe["latency_ms"], float) else None
            update_node_runtime_state(
                active_node.id,
                latency_ms=latency_ms,
                latency_at=datetime.now(timezone.utc),
                latency_target=probe["target"] if isinstance(probe["target"], str) else None,
                latency_via_interface=probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
                latency_method=probe["method"] if isinstance(probe["method"], str) else None,
                last_error=None if latency_ms is not None else "Latency probe failed",
            )
        node_state = get_node_runtime_state(active_node.id)
        probe = {
            "latency_ms": node_state.latency_ms,
            "target": node_state.latency_target,
            "via_interface": node_state.latency_via_interface,
            "method": node_state.latency_method,
        }

    prefix_summary = build_prefix_summary(policy, settings_row)
    node_state = get_node_runtime_state(active_node.id if active_node is not None else None)
    runtime_state = {
        "live_status": live_status,
        "live_error": live_error,
        "latest_latency_ms": probe["latency_ms"] if probe and isinstance(probe["latency_ms"], float) else (
            node_state.latency_ms if active_node is not None else None
        ),
    }
    return policy, active_node, prefix_summary, probe, runtime_state


async def _build_status_payload(db: AsyncSession, metrics_db: AsyncSession, settings_row: GatewaySettings) -> dict:
    policy, active_node, prefix_summary, probe, runtime_state = await _load_runtime_state(db, settings_row)
    latest_metric, _history = await get_metrics_history(metrics_db, hours=1)
    traffic_summary = await get_traffic_usage_summary(metrics_db)
    external_ip_info = serialize_external_ip_info(settings_row, policy)
    tunnel_state = get_tunnel_runtime_state()

    return {
        "status": {
            "vpn_enabled": settings_row.gateway_enabled,
            "tunnel_status": runtime_state["live_status"],
        },
        "active_node": {
            "name": active_node.name,
            "latency_ms": runtime_state["latest_latency_ms"],
            "latency_target": probe["target"] if probe else None,
            "latency_via_interface": probe["via_interface"] if probe else None,
        } if active_node else None,
        "external_ip": {
            "local": external_ip_info["local"]["value"],
            "vpn": external_ip_info["vpn"]["value"],
        },
        "uptime_seconds": max(int(time.time()) - tunnel_state.connected_at_epoch, 0)
        if tunnel_state.connected_at_epoch and runtime_state["live_status"] == "running"
        else 0,
        "active_stack": "nftables" if settings_row.experimental_nftables else "iptables",
        "active_prefixes": {
            "count": prefix_summary["total_prefixes"],
            "configured_count": prefix_summary["configured_prefixes"],
            "set_name": prefix_summary["ipset_name"],
        },
        "system": {
            "cpu_usage_percent": latest_metric.cpu_usage_percent if latest_metric else None,
            "memory_total_bytes": latest_metric.memory_total_bytes if latest_metric else None,
            "memory_used_bytes": latest_metric.memory_used_bytes if latest_metric else None,
            "memory_free_bytes": latest_metric.memory_free_bytes if latest_metric else None,
        },
        "traffic": traffic_summary,
        "runtime_mode": settings_row.runtime_mode,
        "routing_mode": {
            "target": "local" if policy.prefixes_route_local else "awg",
            "label": "send_to_local_interface" if policy.prefixes_route_local else "send_to_awg",
        },
        "kill_switch_enabled": policy.kill_switch_enabled,
        "api_control_enabled": settings_row.api_control_enabled,
    }


@router.get("/status")
async def api_status(
    db: AsyncSession = Depends(get_db),
    metrics_db: AsyncSession = Depends(get_metrics_db),
    settings_row: GatewaySettings = Depends(get_api_settings),
) -> dict:
    return await _build_status_payload(db, metrics_db, settings_row)


@router.get("/devices")
async def api_devices(
    scope: str | None = Query(None, pattern="^(all|marked)$"),
    metrics_db: AsyncSession = Depends(get_metrics_db),
    settings_row: GatewaySettings = Depends(get_api_settings),
) -> dict:
    effective_scope = scope or settings_row.device_api_default_scope
    if effective_scope not in {"all", "marked"}:
        raise HTTPException(status_code=400, detail="Unsupported scope")
    return await get_devices_payload(metrics_db, scope=effective_scope, include_ip_history=False)


@router.post("/control/tunnel")
async def api_control_tunnel(
    payload: EnabledPayload,
    db: AsyncSession = Depends(get_db),
    metrics_db: AsyncSession = Depends(get_metrics_db),
    settings_row: GatewaySettings = Depends(require_api_control),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    settings_row.gateway_enabled = payload.enabled
    db.add(settings_row)
    await commit_with_lock(db)

    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    if payload.enabled:
        sync_firewall_backend(settings_row, policy)
        if active_node is not None:
            await start_tunnel(active_node, settings_row)
            plan = build_routing_plan(settings_row, policy, active_node)
            if plan["safe_to_apply"]:
                apply_routing_plan(settings_row, policy, active_node)
    else:
        await stop_tunnel(settings_row)
        apply_local_passthrough(settings_row)

    return {
        "status": "updated",
        "gateway_enabled": settings_row.gateway_enabled,
        "telemetry": await _build_status_payload(db, metrics_db, settings_row),
    }


@router.post("/control/kill-switch")
async def api_control_kill_switch(
    payload: EnabledPayload,
    db: AsyncSession = Depends(get_db),
    metrics_db: AsyncSession = Depends(get_metrics_db),
    settings_row: GatewaySettings = Depends(require_api_control),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    policy.kill_switch_enabled = payload.enabled
    db.add(policy)
    await commit_with_lock(db)
    sync_firewall_backend(settings_row, policy)
    if settings_row.gateway_enabled and active_node is not None:
        plan = build_routing_plan(settings_row, policy, active_node)
        if plan["safe_to_apply"]:
            apply_routing_plan(settings_row, policy, active_node)
    else:
        apply_local_passthrough(settings_row)

    return {
        "status": "updated",
        "kill_switch_enabled": policy.kill_switch_enabled,
        "telemetry": await _build_status_payload(db, metrics_db, settings_row),
    }
