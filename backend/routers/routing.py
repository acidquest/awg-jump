from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.upstream_node import NodeStatus, UpstreamNode
from backend.routers.auth import get_current_user
from backend.services import routing as routing_svc

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.get("/status")
async def get_status(_user: str = Depends(get_current_user)) -> dict:
    try:
        return routing_svc.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply")
async def apply_routing(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    try:
        active_node = await session.scalar(
            select(UpstreamNode).where(
                UpstreamNode.is_active == True,  # noqa: E712
                UpstreamNode.status == NodeStatus.online,
            )
        )
        routing_svc.setup_policy_routing()
        routing_svc.update_vpn_route("awg1" if active_node else None)
        routing_svc.setup_iptables()
        return {"status": "applied", **routing_svc.get_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_routing(_user: str = Depends(get_current_user)) -> dict:
    try:
        routing_svc.teardown()
        return {"status": "reset"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
