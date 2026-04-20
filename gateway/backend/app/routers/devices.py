from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import commit_with_lock, get_db, get_metrics_db
from app.models import AdminUser, EntryNode, GatewaySettings, RoutingPolicy, TrackedDevice
from app.security import get_current_user
from app.services.device_tracking import get_devices_payload
from app.services.routing import apply_local_passthrough, apply_routing_plan, build_routing_plan


router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceUpdatePayload(BaseModel):
    manual_alias: str | None = None
    is_marked: bool | None = None
    forced_route_target: str | None = None


@router.get("")
async def list_devices(
    scope: str = Query("all", pattern="^(all|marked)$"),
    status: str = Query("all", pattern="^(all|active|present|inactive)$"),
    search: str = Query("", max_length=255),
    db: AsyncSession = Depends(get_metrics_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    return await get_devices_payload(
        db,
        scope=scope,
        status=status,
        search=search,
        include_ip_history=True,
    )


@router.patch("/{device_id}")
async def update_device(
    device_id: int,
    payload: DeviceUpdatePayload,
    db: AsyncSession = Depends(get_metrics_db),
    main_db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    device = await db.get(TrackedDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if payload.manual_alias is not None:
        device.manual_alias = payload.manual_alias.strip()
    if payload.is_marked is not None:
        device.is_marked = payload.is_marked
    runtime_status = "unchanged"
    runtime_error: str | None = None
    if payload.forced_route_target is not None:
        next_target = payload.forced_route_target.strip().lower()
        if next_target not in {"none", "local", "vpn"}:
            raise HTTPException(status_code=400, detail="forced_route_target must be one of: none, local, vpn")
        device.forced_route_target = next_target
    db.add(device)
    await db.flush()
    if payload.forced_route_target is not None:
        await commit_with_lock(db, metrics=True)
        settings_row = await main_db.get(GatewaySettings, 1)
        policy = await main_db.get(RoutingPolicy, 1)
        if settings_row is not None and policy is not None:
            active_node = await main_db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
            if not settings_row.gateway_enabled:
                apply_local_passthrough(settings_row)
                runtime_status = "disabled"
            else:
                plan = build_routing_plan(settings_row, policy, active_node)
                if plan["safe_to_apply"]:
                    try:
                        apply_routing_plan(settings_row, policy, active_node)
                        policy.last_error = None
                        runtime_status = "applied"
                    except RuntimeError as exc:
                        policy.last_error = str(exc)
                        runtime_status = "error"
                        runtime_error = str(exc)
                    main_db.add(policy)
                    await main_db.flush()
                else:
                    runtime_status = "pending"
    return {
        "status": "updated",
        "runtime_status": runtime_status,
        "runtime_error": runtime_error,
        "device": {
            "id": device.id,
            "manual_alias": device.manual_alias,
            "is_marked": device.is_marked,
            "forced_route_target": device.forced_route_target,
        },
    }
