from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_metrics_db
from app.models import AdminUser, TrackedDevice
from app.security import get_current_user
from app.services.device_tracking import get_devices_payload


router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceUpdatePayload(BaseModel):
    manual_alias: str | None = None
    is_marked: bool | None = None


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
    user: AdminUser = Depends(get_current_user),
) -> dict:
    device = await db.get(TrackedDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if payload.manual_alias is not None:
        device.manual_alias = payload.manual_alias.strip()
    if payload.is_marked is not None:
        device.is_marked = payload.is_marked
    db.add(device)
    await db.flush()
    return {
        "status": "updated",
        "device": {
            "id": device.id,
            "manual_alias": device.manual_alias,
            "is_marked": device.is_marked,
        },
    }
