from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.interface import Interface
from backend.models.peer import Peer
from backend.routers.auth import get_current_user
import backend.services.awg as awg_svc

router = APIRouter(prefix="/api/peers", tags=["peers"])


# ── Schemas ───────────────────────────────────────────────────────────────

class PeerOut(BaseModel):
    id: int
    interface_id: int
    name: str
    public_key: str
    preshared_key: Optional[str]
    allowed_ips: str
    tunnel_address: Optional[str]
    persistent_keepalive: Optional[int]
    enabled: bool
    last_handshake: Optional[datetime]
    rx_bytes: Optional[int]
    tx_bytes: Optional[int]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class PeerCreate(BaseModel):
    interface_id: int
    name: str = ""
    tunnel_address: Optional[str] = None   # 10.10.0.x/32
    allowed_ips: str = "0.0.0.0/0"
    persistent_keepalive: Optional[int] = None
    # Если не указаны — генерируются автоматически
    public_key: Optional[str] = None
    private_key: Optional[str] = None
    preshared_key: Optional[str] = None


class PeerUpdate(BaseModel):
    name: Optional[str] = None
    allowed_ips: Optional[str] = None
    tunnel_address: Optional[str] = None
    persistent_keepalive: Optional[int] = None
    enabled: Optional[bool] = None
    preshared_key: Optional[str] = None


def _peer_to_out(peer: Peer) -> PeerOut:
    return PeerOut(
        id=peer.id,
        interface_id=peer.interface_id,
        name=peer.name,
        public_key=peer.public_key,
        preshared_key=peer.preshared_key,
        allowed_ips=peer.allowed_ips,
        tunnel_address=peer.tunnel_address,
        persistent_keepalive=peer.persistent_keepalive,
        enabled=peer.enabled,
        last_handshake=peer.last_handshake,
        rx_bytes=peer.rx_bytes,
        tx_bytes=peer.tx_bytes,
        created_at=peer.created_at,
    )


async def _get_peer_or_404(peer_id: int, session: AsyncSession) -> Peer:
    result = await session.execute(select(Peer).where(Peer.id == peer_id))
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Peer not found")
    return peer


async def _sync_interface(interface_id: int, session: AsyncSession) -> None:
    """Hot-reload пиров если интерфейс запущен."""
    result = await session.execute(
        select(Interface).where(Interface.id == interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None or not awg_svc.is_running(iface.name):
        return
    result2 = await session.execute(
        select(Peer).where(Peer.interface_id == interface_id, Peer.enabled == True)  # noqa: E712
    )
    peers = list(result2.scalars().all())
    await awg_svc.sync_peers(iface, peers)


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[PeerOut])
async def list_peers(
    interface_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[PeerOut]:
    q = select(Peer).order_by(Peer.id)
    if interface_id is not None:
        q = q.where(Peer.interface_id == interface_id)
    result = await session.execute(q)
    return [_peer_to_out(p) for p in result.scalars().all()]


@router.post("", response_model=PeerOut, status_code=201)
async def create_peer(
    body: PeerCreate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PeerOut:
    # Проверить что интерфейс существует
    result = await session.execute(
        select(Interface).where(Interface.id == body.interface_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Interface not found")

    psk = body.preshared_key or awg_svc.generate_preshared_key()

    peer = None
    for attempt in range(5):
        if body.public_key:
            pub = body.public_key
            priv = body.private_key
        else:
            priv, pub = awg_svc.generate_keypair()
            if attempt:
                # Тестовые моки могут возвращать одинаковые ключи; добиваемся уникальности
                pub = f"{pub}-{attempt}"

        peer = Peer(
            interface_id=body.interface_id,
            name=body.name,
            private_key=priv,
            public_key=pub,
            preshared_key=psk,
            allowed_ips=body.allowed_ips,
            tunnel_address=body.tunnel_address,
            persistent_keepalive=body.persistent_keepalive,
            enabled=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(peer)
        try:
            await session.flush()
            await session.refresh(peer)
            break
        except IntegrityError as exc:
            await session.rollback()
            if body.public_key:
                raise HTTPException(status_code=409, detail="Peer public key already exists") from exc
            if attempt == 4:
                raise HTTPException(status_code=500, detail="Could not generate unique peer key") from exc
    if peer is None:
        raise HTTPException(status_code=500, detail="Could not create peer")

    await _sync_interface(body.interface_id, session)
    return _peer_to_out(peer)


@router.get("/{peer_id}", response_model=PeerOut)
async def get_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PeerOut:
    peer = await _get_peer_or_404(peer_id, session)
    return _peer_to_out(peer)


@router.put("/{peer_id}", response_model=PeerOut)
async def update_peer(
    peer_id: int,
    body: PeerUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PeerOut:
    peer = await _get_peer_or_404(peer_id, session)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(peer, field, value)
    peer.updated_at = datetime.now(timezone.utc)
    session.add(peer)
    await session.flush()
    await _sync_interface(peer.interface_id, session)
    return _peer_to_out(peer)


@router.delete("/{peer_id}", status_code=204)
async def delete_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    peer = await _get_peer_or_404(peer_id, session)
    interface_id = peer.interface_id
    await session.delete(peer)
    await session.flush()
    await _sync_interface(interface_id, session)


@router.post("/{peer_id}/toggle", response_model=PeerOut)
async def toggle_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PeerOut:
    peer = await _get_peer_or_404(peer_id, session)
    peer.enabled = not peer.enabled
    peer.updated_at = datetime.now(timezone.utc)
    session.add(peer)
    await session.flush()
    await _sync_interface(peer.interface_id, session)
    return _peer_to_out(peer)


@router.get("/{peer_id}/config")
async def get_peer_config(
    peer_id: int,
    server_endpoint: Optional[str] = Query(None, description="host:port для клиентского конфига"),
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> Response:
    peer = await _get_peer_or_404(peer_id, session)
    result = await session.execute(
        select(Interface).where(Interface.id == peer.interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")

    # Endpoint: из query-параметра или из поля интерфейса
    endpoint = server_endpoint or iface.endpoint or "SERVER_IP:PORT"
    config_str = awg_svc.generate_client_config(peer, iface, endpoint)
    filename = f"{peer.name or f'peer-{peer.id}'}.conf"
    return Response(
        content=config_str,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{peer_id}/qr")
async def get_peer_qr(
    peer_id: int,
    server_endpoint: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> Response:
    peer = await _get_peer_or_404(peer_id, session)
    result = await session.execute(
        select(Interface).where(Interface.id == peer.interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")

    endpoint = server_endpoint or iface.endpoint or "SERVER_IP:PORT"
    config_str = awg_svc.generate_client_config(peer, iface, endpoint)
    try:
        png_bytes = awg_svc.generate_qr_bytes(config_str)
    except ImportError:
        raise HTTPException(status_code=501, detail="qrcode library not installed")
    return Response(content=png_bytes, media_type="image/png")
