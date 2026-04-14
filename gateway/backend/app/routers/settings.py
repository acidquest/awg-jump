from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, EntryNode, GatewaySettings, RoutingPolicy, RuntimeMode, TrafficSourceMode
from app.security import get_current_user
from app.services.dns_runtime import restart_dnsmasq
from app.services.external_ip import refresh_external_ip_info, serialize_external_ip_info, validate_service_pair
from app.services.runtime import get_kernel_support_status
from app.services.routing import apply_routing_plan, build_routing_plan, sync_firewall_backend
from app.services.traffic_sources import migrate_legacy_source_settings, normalize_allowed_source_cidrs


router = APIRouter(prefix="/api/settings", tags=["settings"])


class GatewaySettingsUpdate(BaseModel):
    ui_language: str
    runtime_mode: str
    allowed_client_cidrs: list[str] = []
    dns_intercept_enabled: bool = True
    experimental_nftables: bool = False
    external_ip_local_service_url: str
    external_ip_vpn_service_url: str


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    if migrate_legacy_source_settings(settings_row):
        db.add(settings_row)
        await db.flush()
    policy = await db.get(RoutingPolicy, 1)
    kernel_available, kernel_message = get_kernel_support_status()
    return {
        "ui_language": settings_row.ui_language,
        "runtime_mode": settings_row.runtime_mode,
        "allowed_client_cidrs": settings_row.allowed_client_cidrs,
        "dns_intercept_enabled": settings_row.dns_intercept_enabled,
        "experimental_nftables": settings_row.experimental_nftables,
        "kernel_available": kernel_available,
        "kernel_message": kernel_message,
        "active_entry_node_id": settings_row.active_entry_node_id,
        "tunnel_status": settings_row.tunnel_status,
        "tunnel_last_error": settings_row.tunnel_last_error,
        "external_ip_info": serialize_external_ip_info(settings_row, policy),
    }


@router.put("")
async def update_settings(
    payload: GatewaySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    if payload.runtime_mode not in {mode.value for mode in RuntimeMode}:
        raise HTTPException(status_code=400, detail="Unsupported runtime_mode")
    try:
        local_service_url, vpn_service_url = validate_service_pair(
            payload.external_ip_local_service_url,
            payload.external_ip_vpn_service_url,
        )
        normalized_source_cidrs = normalize_allowed_source_cidrs(payload.allowed_client_cidrs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings_row = await db.get(GatewaySettings, 1)
    settings_row.ui_language = payload.ui_language
    settings_row.runtime_mode = payload.runtime_mode
    settings_row.traffic_source_mode = TrafficSourceMode.cidr_list.value
    settings_row.allowed_client_cidrs = normalized_source_cidrs
    settings_row.allowed_client_hosts = []
    settings_row.dns_intercept_enabled = payload.dns_intercept_enabled
    settings_row.experimental_nftables = payload.experimental_nftables
    settings_row.external_ip_local_service_url = local_service_url
    settings_row.external_ip_vpn_service_url = vpn_service_url
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
    external_ip_info = await refresh_external_ip_info(db, settings_row, policy, force=True)
    return {"status": "updated", "plan": plan, "external_ip_info": external_ip_info}
