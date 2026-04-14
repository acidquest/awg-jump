from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, EntryNode, GatewaySettings, RoutingPolicy, RuntimeMode, TrafficSourceMode
from app.security import get_current_user
from app.services.dns_runtime import restart_dnsmasq
from app.services.runtime import get_kernel_support_status
from app.services.routing import apply_routing_plan, build_routing_plan, sync_firewall_backend


router = APIRouter(prefix="/api/settings", tags=["settings"])


class GatewaySettingsUpdate(BaseModel):
    ui_language: str
    runtime_mode: str
    traffic_source_mode: str
    allowed_client_cidrs: list[str] = []
    allowed_client_hosts: list[str] = []
    dns_intercept_enabled: bool = True
    experimental_nftables: bool = False


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    kernel_available, kernel_message = get_kernel_support_status()
    return {
        "ui_language": settings_row.ui_language,
        "runtime_mode": settings_row.runtime_mode,
        "traffic_source_mode": settings_row.traffic_source_mode,
        "allowed_client_cidrs": settings_row.allowed_client_cidrs,
        "allowed_client_hosts": settings_row.allowed_client_hosts,
        "dns_intercept_enabled": settings_row.dns_intercept_enabled,
        "experimental_nftables": settings_row.experimental_nftables,
        "kernel_available": kernel_available,
        "kernel_message": kernel_message,
        "active_entry_node_id": settings_row.active_entry_node_id,
        "tunnel_status": settings_row.tunnel_status,
        "tunnel_last_error": settings_row.tunnel_last_error,
    }


@router.put("")
async def update_settings(
    payload: GatewaySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    if payload.traffic_source_mode not in {mode.value for mode in TrafficSourceMode}:
        raise HTTPException(status_code=400, detail="Unsupported traffic_source_mode")
    if payload.runtime_mode not in {mode.value for mode in RuntimeMode}:
        raise HTTPException(status_code=400, detail="Unsupported runtime_mode")
    settings_row = await db.get(GatewaySettings, 1)
    settings_row.ui_language = payload.ui_language
    settings_row.runtime_mode = payload.runtime_mode
    settings_row.traffic_source_mode = payload.traffic_source_mode
    settings_row.allowed_client_cidrs = payload.allowed_client_cidrs
    settings_row.allowed_client_hosts = payload.allowed_client_hosts
    settings_row.dns_intercept_enabled = payload.dns_intercept_enabled
    settings_row.experimental_nftables = payload.experimental_nftables
    db.add(settings_row)
    await db.flush()
    policy = await db.get(RoutingPolicy, 1)
    if policy:
        sync_firewall_backend(settings_row, policy)
    await restart_dnsmasq(db)
    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    plan = build_routing_plan(settings_row, policy, active_node) if policy else None
    if policy and active_node and plan and plan["safe_to_apply"]:
        try:
            apply_routing_plan(settings_row, policy, active_node)
        except RuntimeError as exc:
            settings_row.tunnel_last_error = str(exc)
            db.add(settings_row)
            await db.flush()
            return {"status": "error", "error": str(exc), "plan": plan}
    return {"status": "updated", "plan": plan}
