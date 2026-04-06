from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.interface import Interface, InterfaceMode
from backend.models.peer import Peer
from backend.routers.auth import get_current_user
import backend.services.awg as awg_svc

router = APIRouter(prefix="/api/interfaces", tags=["interfaces"])


# ── Schemas ───────────────────────────────────────────────────────────────

class InterfaceOut(BaseModel):
    id: int
    name: str
    mode: str
    public_key: str
    listen_port: Optional[int]
    address: str
    dns: Optional[str]
    endpoint: Optional[str]
    allowed_ips: Optional[str]
    persistent_keepalive: Optional[int]
    enabled: bool
    running: bool
    # Обфускация
    obf_jc: Optional[int]
    obf_jmin: Optional[int]
    obf_jmax: Optional[int]
    obf_s1: Optional[int]
    obf_s2: Optional[int]
    obf_s3: Optional[int]
    obf_s4: Optional[int]
    obf_h1: Optional[int]
    obf_h2: Optional[int]
    obf_h3: Optional[int]
    obf_h4: Optional[int]
    obf_generated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class InterfaceUpdate(BaseModel):
    listen_port: Optional[int] = None
    address: Optional[str] = None
    dns: Optional[str] = None
    endpoint: Optional[str] = None
    preshared_key: Optional[str] = None
    allowed_ips: Optional[str] = None
    persistent_keepalive: Optional[int] = None
    enabled: Optional[bool] = None


def _iface_to_out(iface: Interface) -> InterfaceOut:
    return InterfaceOut(
        id=iface.id,
        name=iface.name,
        mode=iface.mode.value if hasattr(iface.mode, "value") else iface.mode,
        public_key=iface.public_key or "",
        listen_port=iface.listen_port,
        address=iface.address,
        dns=iface.dns,
        endpoint=iface.endpoint,
        allowed_ips=iface.allowed_ips,
        persistent_keepalive=iface.persistent_keepalive,
        enabled=iface.enabled,
        running=awg_svc.is_running(iface.name),
        obf_jc=iface.obf_jc,
        obf_jmin=iface.obf_jmin,
        obf_jmax=iface.obf_jmax,
        obf_s1=iface.obf_s1,
        obf_s2=iface.obf_s2,
        obf_s3=iface.obf_s3,
        obf_s4=iface.obf_s4,
        obf_h1=iface.obf_h1,
        obf_h2=iface.obf_h2,
        obf_h3=iface.obf_h3,
        obf_h4=iface.obf_h4,
        obf_generated_at=iface.obf_generated_at,
    )


async def _get_iface_or_404(iface_id: int, session: AsyncSession) -> Interface:
    result = await session.execute(
        select(Interface).where(Interface.id == iface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    return iface


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[InterfaceOut])
async def list_interfaces(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[InterfaceOut]:
    result = await session.execute(select(Interface).order_by(Interface.id))
    return [_iface_to_out(i) for i in result.scalars().all()]


@router.get("/{iface_id}", response_model=InterfaceOut)
async def get_interface(
    iface_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> InterfaceOut:
    iface = await _get_iface_or_404(iface_id, session)
    return _iface_to_out(iface)


@router.put("/{iface_id}", response_model=InterfaceOut)
async def update_interface(
    iface_id: int,
    body: InterfaceUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> InterfaceOut:
    iface = await _get_iface_or_404(iface_id, session)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(iface, field, value)
    iface.updated_at = datetime.now(timezone.utc)
    session.add(iface)
    await session.flush()
    return _iface_to_out(iface)


@router.post("/{iface_id}/apply", response_model=InterfaceOut)
async def apply_interface(
    iface_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> InterfaceOut:
    iface = await _get_iface_or_404(iface_id, session)
    if not iface.private_key:
        raise HTTPException(status_code=400, detail="Interface has no private key")
    result = await session.execute(
        select(Peer).where(Peer.interface_id == iface_id, Peer.enabled == True)  # noqa: E712
    )
    peers = list(result.scalars().all())
    try:
        await awg_svc.apply_interface(iface, peers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _iface_to_out(iface)


@router.post("/{iface_id}/stop", response_model=InterfaceOut)
async def stop_interface(
    iface_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> InterfaceOut:
    iface = await _get_iface_or_404(iface_id, session)
    await awg_svc.stop_interface(iface.name)
    return _iface_to_out(iface)


@router.get("/{iface_id}/status")
async def interface_status(
    iface_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    iface = await _get_iface_or_404(iface_id, session)
    all_status = awg_svc.get_status()
    iface_status = all_status.get(iface.name, {"name": iface.name, "running": False, "peers": {}})
    return iface_status


@router.post("/{iface_id}/regenerate-obfuscation", response_model=InterfaceOut)
async def regenerate_obfuscation(
    iface_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> InterfaceOut:
    """Перегенерировать параметры обфускации для интерфейса."""
    iface = await _get_iface_or_404(iface_id, session)
    params = awg_svc.generate_obfuscation_params()
    iface.obf_jc = params["jc"]
    iface.obf_jmin = params["jmin"]
    iface.obf_jmax = params["jmax"]
    iface.obf_s1 = params["s1"]
    iface.obf_s2 = params["s2"]
    iface.obf_s3 = params["s3"]
    iface.obf_s4 = params["s4"]
    iface.obf_h1 = params["h1"]
    iface.obf_h2 = params["h2"]
    iface.obf_h3 = params["h3"]
    iface.obf_h4 = params["h4"]
    iface.obf_generated_at = datetime.now(timezone.utc)
    iface.updated_at = datetime.now(timezone.utc)
    session.add(iface)
    await session.flush()
    return _iface_to_out(iface)
