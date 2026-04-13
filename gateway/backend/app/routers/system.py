from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AdminUser, DnsDomainRule, EntryNode, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.runtime import current_pid, is_runtime_available


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@router.get("/status")
async def status(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    gateway_settings = await db.get(GatewaySettings, 1)
    routing_policy = await db.get(RoutingPolicy, 1)
    active_node = await db.get(EntryNode, gateway_settings.active_entry_node_id) if gateway_settings.active_entry_node_id else None
    entry_node_count = await db.scalar(select(func.count()).select_from(EntryNode))
    dns_rule_count = await db.scalar(select(func.count()).select_from(DnsDomainRule))
    return {
        "runtime_available": is_runtime_available(),
        "runtime_pid": current_pid(),
        "tunnel_status": gateway_settings.tunnel_status,
        "active_entry_node": {
            "id": active_node.id,
            "name": active_node.name,
            "endpoint": active_node.endpoint,
            "latest_latency_ms": active_node.latest_latency_ms,
        } if active_node else None,
        "entry_node_count": entry_node_count,
        "dns_rule_count": dns_rule_count,
        "traffic_source_mode": gateway_settings.traffic_source_mode,
        "runtime_mode": gateway_settings.runtime_mode,
        "ui_language": gateway_settings.ui_language,
        "kill_switch_enabled": routing_policy.kill_switch_enabled,
        "geoip_countries": routing_policy.geoip_countries,
    }
