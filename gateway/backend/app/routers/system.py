from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser, DnsDomainRule, EntryNode, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.external_ip import serialize_external_ip_info
from app.services.routing import build_prefix_summary
from app.services.runtime import (
    current_pid,
    get_kernel_support_status,
    is_runtime_available,
    probe_node_latency_details,
    resolve_live_tunnel_status,
)
from app.services.traffic_sources import migrate_legacy_source_settings
from app.services.system_metrics import get_metrics_history
from app.services.traffic_metrics import get_traffic_usage_summary


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@router.get("/status")
async def status(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    kernel_available, kernel_message = get_kernel_support_status()
    gateway_settings = await db.get(GatewaySettings, 1)
    if migrate_legacy_source_settings(gateway_settings):
        db.add(gateway_settings)
        await db.flush()
    routing_policy = await db.get(RoutingPolicy, 1)
    prefix_summary = build_prefix_summary(routing_policy, gateway_settings)
    live_status, live_error = resolve_live_tunnel_status(gateway_settings)
    active_node = await db.get(EntryNode, gateway_settings.active_entry_node_id) if gateway_settings.active_entry_node_id else None
    probe: dict | None = None
    latest_latency_ms: float | None = active_node.latest_latency_ms if active_node is not None else None
    if active_node is not None:
        probe = probe_node_latency_details(active_node, prefer_tunnel=True)
        latency_ms = probe["latency_ms"]
        latest_latency_ms = latency_ms if isinstance(latency_ms, float) else None
    entry_node_count = await db.scalar(select(func.count()).select_from(EntryNode))
    dns_rule_count = await db.scalar(select(func.count()).select_from(DnsDomainRule))
    traffic_summary = await get_traffic_usage_summary(db)
    return {
        "runtime_available": is_runtime_available(),
        "runtime_pid": current_pid(),
        "tunnel_status": live_status,
        "tunnel_last_error": live_error,
        "active_entry_node": {
            "id": active_node.id,
            "name": active_node.name,
            "endpoint": active_node.endpoint,
            "latest_latency_ms": latest_latency_ms,
            "latest_latency_target": probe["target"] if isinstance(probe["target"], str) else None,
            "latest_latency_via_interface": probe["via_interface"] if isinstance(probe["via_interface"], str) else None,
            "latest_latency_method": probe["method"] if isinstance(probe["method"], str) else None,
            "uptime_seconds": max(int(time.time()) - gateway_settings.active_node_connected_at_epoch, 0)
            if gateway_settings.active_node_connected_at_epoch and live_status == "running"
            else 0,
        } if active_node else None,
        "entry_node_count": entry_node_count,
        "dns_rule_count": dns_rule_count,
        "allowed_client_cidrs": gateway_settings.allowed_client_cidrs,
        "gateway_enabled": gateway_settings.gateway_enabled,
        "failover_enabled": gateway_settings.failover_enabled,
        "runtime_mode": gateway_settings.runtime_mode,
        "experimental_nftables": gateway_settings.experimental_nftables,
        "kernel_available": kernel_available,
        "kernel_message": kernel_message,
        "ui_language": gateway_settings.ui_language,
        "kill_switch_enabled": routing_policy.kill_switch_enabled,
        "geoip_countries": routing_policy.geoip_countries,
        "ipset_name": routing_policy.geoip_ipset_name,
        "firewall_backend": "nftables" if gateway_settings.experimental_nftables else "iptables",
        "external_ip_info": serialize_external_ip_info(gateway_settings, routing_policy),
        "active_prefixes_count": prefix_summary["total_prefixes"],
        "active_prefixes_configured_count": prefix_summary["configured_prefixes"],
        "prefix_summary": {
            "countries_enabled": routing_policy.countries_enabled,
            "manual_prefixes_enabled": routing_policy.manual_prefixes_enabled,
            "fqdn_prefixes_enabled": routing_policy.fqdn_prefixes_enabled,
        },
        "traffic_summary": traffic_summary,
    }


@router.get("/metrics")
async def metrics(
    period: str = Query("24h", pattern="^(1h|24h)$"),
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    hours = 24 if period == "24h" else 1
    latest, history = await get_metrics_history(db, hours=hours)
    latest_payload = None
    if latest:
        latest_payload = {
            "collected_at": latest.collected_at.isoformat(),
            "cpu_usage_percent": latest.cpu_usage_percent,
            "memory_total_bytes": latest.memory_total_bytes,
            "memory_used_bytes": latest.memory_used_bytes,
            "memory_free_bytes": latest.memory_free_bytes,
        }
    return {
        "period": period,
        "retention_hours": 24,
        "sampling_interval_seconds": 60,
        "latest": latest_payload,
        "points": [
            {
                "collected_at": item.collected_at.isoformat(),
                "cpu_usage_percent": item.cpu_usage_percent,
                "memory_total_bytes": item.memory_total_bytes,
                "memory_used_bytes": item.memory_used_bytes,
                "memory_free_bytes": item.memory_free_bytes,
            }
            for item in history
        ],
    }
