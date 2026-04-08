from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.routing_settings import RoutingSettings
from backend.models.upstream_node import NodeStatus, UpstreamNode
from backend.routers.auth import get_current_user
from backend.services import routing as routing_svc

router = APIRouter(prefix="/api/routing", tags=["routing"])


class RoutingSettingsPayload(BaseModel):
    invert_geoip: bool


async def _get_or_create_settings(session: AsyncSession) -> RoutingSettings:
    settings_row = await session.get(RoutingSettings, 1)
    if settings_row is None:
        settings_row = RoutingSettings(
            id=1,
            invert_geoip=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(settings_row)
        await session.flush()
    return settings_row


async def _get_active_node(session: AsyncSession) -> UpstreamNode | None:
    return await session.scalar(
        select(UpstreamNode).where(
            UpstreamNode.is_active == True,  # noqa: E712
            UpstreamNode.status == NodeStatus.online,
        )
    )


@router.get("/status")
async def get_status(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    try:
        settings_row = await _get_or_create_settings(session)
        return routing_svc.get_status(invert_geoip=settings_row.invert_geoip)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings")
async def update_settings(
    payload: RoutingSettingsPayload,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    try:
        settings_row = await _get_or_create_settings(session)
        settings_row.invert_geoip = payload.invert_geoip
        settings_row.updated_at = datetime.now(timezone.utc)
        session.add(settings_row)
        await session.flush()

        active_node = await _get_active_node(session)
        routing_svc.setup_policy_routing()
        routing_svc.update_vpn_route("awg1" if active_node else None)
        routing_svc.update_upstream_host_route(
            active_node.awg_address if active_node and active_node.awg_address else None
        )
        routing_svc.setup_iptables(invert_geoip=settings_row.invert_geoip)
        return {"status": "updated", **routing_svc.get_status(invert_geoip=settings_row.invert_geoip)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply")
async def apply_routing(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    try:
        settings_row = await _get_or_create_settings(session)
        active_node = await _get_active_node(session)
        routing_svc.setup_policy_routing()
        routing_svc.update_vpn_route("awg1" if active_node else None)
        routing_svc.update_upstream_host_route(
            active_node.awg_address if active_node and active_node.awg_address else None
        )
        routing_svc.setup_iptables(invert_geoip=settings_row.invert_geoip)
        return {"status": "applied", **routing_svc.get_status(invert_geoip=settings_row.invert_geoip)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_routing(_user: str = Depends(get_current_user)) -> dict:
    try:
        routing_svc.teardown()
        return {"status": "reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
