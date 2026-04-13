from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, GatewaySettings, RuntimeMode, TrafficSourceMode
from app.security import get_current_user
from app.services.runtime import get_kernel_support_status


router = APIRouter(prefix="/api/settings", tags=["settings"])


class GatewaySettingsUpdate(BaseModel):
    ui_language: str
    runtime_mode: str
    traffic_source_mode: str
    allowed_client_cidrs: list[str] = []
    allowed_client_hosts: list[str] = []


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
    db.add(settings_row)
    await db.flush()
    return {"status": "updated"}
