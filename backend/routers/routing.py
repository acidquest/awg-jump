from fastapi import APIRouter, Depends, HTTPException

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
async def apply_routing(_user: str = Depends(get_current_user)) -> dict:
    try:
        routing_svc.setup_policy_routing()
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
