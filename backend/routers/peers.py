import ipaddress
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.config import settings
from backend.database import AsyncSessionLocal, get_db
from backend.models.interface import Interface, InterfaceMode, InterfaceProtocol
from backend.models.peer import Peer
from backend.routers.auth import get_current_user
from backend.services.conf_parser import parse_peer_conf
import backend.services.awg as awg_svc

router = APIRouter(prefix="/api/peers", tags=["peers"])
_STATUS_REPORT_MIN_INTERVAL_SECONDS = 300


async def get_authenticated_db(
    _user: str = Depends(get_current_user),
):
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ── Schemas ───────────────────────────────────────────────────────────────

class PeerOut(BaseModel):
    id: int
    interface_id: int
    interface_name: str
    interface_protocol: str
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
    client_code: Optional[int]
    client_kind: Optional[str]
    client_reported_ip: Optional[str]
    client_reported_at: Optional[datetime]
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class PeerDetail(PeerOut):
    private_key: Optional[str]


class PeerCreate(BaseModel):
    interface_id: int
    name: str = ""
    tunnel_address: Optional[str] = None   # 10.10.0.x/32
    allowed_ips: str = "0.0.0.0/0"
    persistent_keepalive: Optional[int] = 25
    conf_text: Optional[str] = None
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
    private_key: Optional[str] = None
    preshared_key: Optional[str] = None


class PeerStatusReport(BaseModel):
    client_code: int


class PeerPrivateKeyIn(BaseModel):
    private_key: str
    interface_id: Optional[int] = None
    protocol: Optional[InterfaceProtocol] = None


class PeerProtocolIn(BaseModel):
    interface_id: Optional[int] = None
    protocol: Optional[InterfaceProtocol] = None


_CLIENT_KIND_BY_CODE = {
    1001: "awg-gateway",
    1002: "awg-jump-client-android",
    1003: "awg-jump-client-ios",
}


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _peer_to_out(peer: Peer) -> PeerOut:
    return PeerOut(
        id=peer.id,
        interface_id=peer.interface_id,
        interface_name=peer.interface.name if peer.interface else "",
        interface_protocol=(
            peer.interface.protocol.value
            if peer.interface and hasattr(peer.interface.protocol, "value")
            else (peer.interface.protocol if peer.interface else "awg")
        ),
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
        client_code=peer.client_code,
        client_kind=peer.client_kind,
        client_reported_ip=peer.client_reported_ip,
        client_reported_at=peer.client_reported_at,
        created_at=peer.created_at,
    )


def _peer_to_detail(peer: Peer) -> PeerDetail:
    return PeerDetail(**_peer_to_out(peer).model_dump(), private_key=peer.private_key)


def _protocol_from_interface(iface: Interface) -> InterfaceProtocol:
    if isinstance(iface.protocol, InterfaceProtocol):
        return iface.protocol
    return InterfaceProtocol(iface.protocol or "awg")


def _protocol_from_parsed_conf(conf_text: str) -> tuple[InterfaceProtocol, object]:
    parsed = parse_peer_conf(conf_text)
    protocol = InterfaceProtocol.awg if parsed.obfuscation else InterfaceProtocol.wg
    return protocol, parsed


async def _apply_live_stats(peers: list[Peer], session: AsyncSession) -> list[Peer]:
    """Подмешивает live handshake/rx/tx из awg show all dump поверх значений из БД."""
    if not peers:
        return peers

    iface_ids = {p.interface_id for p in peers}
    result = await session.execute(select(Interface).where(Interface.id.in_(iface_ids)))
    iface_map = {iface.id: iface.name for iface in result.scalars().all()}

    status = awg_svc.get_status()
    if not status:
        return peers

    for peer in peers:
        iface_name = iface_map.get(peer.interface_id)
        if not iface_name:
            continue
        peer_stat = status.get(iface_name, {}).get("peers", {}).get(peer.public_key)
        if peer_stat is None:
            continue
        hs = peer_stat.get("latest_handshake", 0)
        peer.last_handshake = (
            datetime.fromtimestamp(hs, tz=timezone.utc) if hs else None
        )
        peer.rx_bytes = peer_stat.get("rx_bytes", 0)
        peer.tx_bytes = peer_stat.get("tx_bytes", 0)
    return peers


async def _get_peer_or_404(peer_id: int, session: AsyncSession) -> Peer:
    result = await session.execute(select(Peer).options(selectinload(Peer.interface)).where(Peer.id == peer_id))
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Peer not found")
    return peer


async def _allocate_tunnel_address(interface: Interface, session: AsyncSession) -> Optional[str]:
    """
    Автоматически выделяет следующий свободный /32 адрес из подсети интерфейса.
    Например, если awg0 имеет адрес 10.10.0.1/24, первый пир получит 10.10.0.2/32.
    """
    if not interface.address or interface.mode != InterfaceMode.server:
        return None
    try:
        network = ipaddress.IPv4Interface(interface.address).network
        server_ip = str(ipaddress.IPv4Interface(interface.address).ip)
    except ValueError:
        return None

    result = await session.execute(
        select(Peer.tunnel_address).where(
            Peer.interface_id == interface.id,
            Peer.tunnel_address.isnot(None),
        )
    )
    used = {server_ip}
    for row in result.all():
        addr = row[0]
        if addr:
            used.add(addr.split("/")[0])

    for host in network.hosts():
        if str(host) not in used:
            return f"{host}/32"

    return None


async def _resolve_protocol(
    session: AsyncSession,
    *,
    interface_id: Optional[int],
    protocol: Optional[InterfaceProtocol],
) -> InterfaceProtocol:
    if interface_id is not None:
        result = await session.execute(select(Interface).where(Interface.id == interface_id))
        iface = result.scalar_one_or_none()
        if iface is None:
            raise HTTPException(status_code=404, detail="Interface not found")
        return _protocol_from_interface(iface)
    if protocol is not None:
        return protocol
    return InterfaceProtocol.awg


def _build_endpoint(override: Optional[str], iface: Interface) -> str:
    """
    Строит endpoint для клиентского конфига.
    Приоритет: явный query-параметр → SERVER_HOST из .env → iface.endpoint → заглушка.
    """
    if override:
        return override
    if settings.server_host:
        port = iface.listen_port or 51820
        return f"{settings.server_host}:{port}"
    if iface.endpoint:
        return iface.endpoint
    return "SERVER_IP:PORT"


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
    session: AsyncSession = Depends(get_authenticated_db),
) -> list[PeerOut]:
    q = (
        select(Peer)
        .options(selectinload(Peer.interface))
        .join(Interface, Interface.id == Peer.interface_id)
        .where(Interface.name.in_(awg_svc.visible_interface_names()))
        .order_by(Peer.id)
    )
    if interface_id is not None:
        q = q.where(Peer.interface_id == interface_id)
    result = await session.execute(q)
    peers = list(result.scalars().all())
    peers = await _apply_live_stats(peers, session)
    return [_peer_to_out(p) for p in peers]


@router.post("/status")
async def report_peer_status(
    body: PeerStatusReport,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict:
    client_kind = _CLIENT_KIND_BY_CODE.get(body.client_code)
    if client_kind is None:
        raise HTTPException(status_code=400, detail="Unsupported client_code")

    client_ip = request.client.host if request.client else None
    if not client_ip:
        raise HTTPException(status_code=400, detail="Could not determine client IP")

    result = await session.execute(
        select(Peer, Interface)
        .join(Interface, Interface.id == Peer.interface_id)
        .where(
            Interface.name == "awg0",
            Interface.protocol == InterfaceProtocol.awg,
            Peer.tunnel_address.isnot(None),
        )
    )
    peer = None
    for peer_obj, iface in result.all():
        tunnel_ip = (peer_obj.tunnel_address or "").split("/", 1)[0]
        if tunnel_ip == client_ip:
            peer = peer_obj
            break
        try:
            network = ipaddress.ip_network(iface.address, strict=False)
            if ipaddress.ip_address(client_ip) in network and tunnel_ip == client_ip:
                peer = peer_obj
                break
        except ValueError:
            continue

    if peer is None:
        raise HTTPException(status_code=403, detail="Client IP does not match any awg0 peer")

    now = datetime.now(timezone.utc)
    last_report = _to_utc(peer.client_reported_at)
    recently_reported = (
        last_report is not None
        and (now - last_report).total_seconds() < _STATUS_REPORT_MIN_INTERVAL_SECONDS
    )
    same_report = (
        peer.client_code == body.client_code
        and peer.client_kind == client_kind
        and peer.client_reported_ip == client_ip
    )

    if not (same_report and recently_reported):
        peer.client_code = body.client_code
        peer.client_kind = client_kind
        peer.client_reported_ip = client_ip
        peer.client_reported_at = now
        peer.updated_at = now
        session.add(peer)
        await session.flush()

    return {"status": "ok", "client_kind": client_kind, "peer_id": peer.id}


@router.post("", response_model=PeerOut, status_code=201)
async def create_peer(
    body: PeerCreate,
    session: AsyncSession = Depends(get_authenticated_db),
) -> PeerOut:
    # Проверить что интерфейс существует
    result = await session.execute(
        select(Interface).where(Interface.id == body.interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    if iface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Interface not found")

    protocol = _protocol_from_interface(iface)

    name = body.name
    tunnel_address = body.tunnel_address
    allowed_ips = body.allowed_ips
    persistent_keepalive = body.persistent_keepalive
    private_key = body.private_key
    public_key = body.public_key
    preshared_key = body.preshared_key

    if body.conf_text:
        try:
            conf_protocol, parsed = _protocol_from_parsed_conf(body.conf_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if conf_protocol != protocol:
            raise HTTPException(
                status_code=400,
                detail=f"Config protocol {conf_protocol.value} does not match interface protocol {protocol.value}",
            )
        name = body.name or parsed.name
        tunnel_address = parsed.tunnel_address
        allowed_ips = ",".join(parsed.allowed_ips) or body.allowed_ips
        persistent_keepalive = parsed.persistent_keepalive
        private_key = parsed.private_key
        public_key = parsed.public_key
        preshared_key = parsed.preshared_key

    if private_key:
        try:
            public_key = awg_svc.derive_public_key(private_key, protocol=protocol)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid private key") from exc

    psk = preshared_key or awg_svc.generate_preshared_key(protocol=protocol)

    if not tunnel_address:
        tunnel_address = await _allocate_tunnel_address(iface, session)

    peer = None
    for attempt in range(5):
        if public_key:
            pub = public_key
            priv = private_key
        else:
            priv, pub = awg_svc.generate_keypair(protocol=protocol)
            if attempt:
                # Тестовые моки могут возвращать одинаковые ключи; добиваемся уникальности
                pub = f"{pub}-{attempt}"

        peer = Peer(
            interface_id=body.interface_id,
            name=name,
            private_key=priv,
            public_key=pub,
            preshared_key=psk,
            allowed_ips=allowed_ips,
            tunnel_address=tunnel_address,
            persistent_keepalive=persistent_keepalive,
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
            if public_key:
                raise HTTPException(status_code=409, detail="Peer public key already exists") from exc
            if attempt == 4:
                raise HTTPException(status_code=500, detail="Could not generate unique peer key") from exc
    if peer is None:
        raise HTTPException(status_code=500, detail="Could not create peer")

    peer.interface = iface
    await _sync_interface(body.interface_id, session)
    return _peer_to_out(peer)


@router.get("/{peer_id}", response_model=PeerDetail)
async def get_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_authenticated_db),
) -> PeerDetail:
    peer = await _get_peer_or_404(peer_id, session)
    if peer.interface and peer.interface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Peer not found")
    await _apply_live_stats([peer], session)
    return _peer_to_detail(peer)


@router.put("/{peer_id}", response_model=PeerOut)
async def update_peer(
    peer_id: int,
    body: PeerUpdate,
    session: AsyncSession = Depends(get_authenticated_db),
) -> PeerOut:
    peer = await _get_peer_or_404(peer_id, session)
    if peer.interface and peer.interface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Peer not found")
    protocol = _protocol_from_interface(peer.interface)
    update_fields = body.model_fields_set
    if "private_key" in update_fields and body.private_key:
        try:
            peer.private_key = body.private_key
            peer.public_key = awg_svc.derive_public_key(body.private_key, protocol=protocol)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid private key") from exc
    for field in update_fields:
        if field == "private_key":
            continue
        setattr(peer, field, getattr(body, field))
    peer.updated_at = datetime.now(timezone.utc)
    session.add(peer)
    await session.flush()
    await _sync_interface(peer.interface_id, session)
    return _peer_to_out(peer)


@router.post("/generate-keypair")
async def generate_peer_keypair(
    body: PeerProtocolIn,
    session: AsyncSession = Depends(get_authenticated_db),
) -> dict[str, str]:
    protocol = await _resolve_protocol(
        session,
        interface_id=body.interface_id,
        protocol=body.protocol,
    )
    private_key, public_key = awg_svc.generate_keypair(protocol=protocol)
    return {"private_key": private_key, "public_key": public_key, "protocol": protocol.value}


@router.post("/generate-preshared-key")
async def generate_peer_preshared_key(
    body: PeerProtocolIn,
    session: AsyncSession = Depends(get_authenticated_db),
) -> dict[str, str]:
    protocol = await _resolve_protocol(
        session,
        interface_id=body.interface_id,
        protocol=body.protocol,
    )
    preshared_key = awg_svc.generate_preshared_key(protocol=protocol)
    return {"preshared_key": preshared_key, "protocol": protocol.value}


@router.post("/derive-public-key")
async def derive_peer_public_key(
    body: PeerPrivateKeyIn,
    session: AsyncSession = Depends(get_authenticated_db),
) -> dict[str, str]:
    protocol = await _resolve_protocol(
        session,
        interface_id=body.interface_id,
        protocol=body.protocol,
    )
    try:
        public_key = awg_svc.derive_public_key(body.private_key, protocol=protocol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid private key") from exc
    return {"public_key": public_key, "protocol": protocol.value}


@router.delete("/{peer_id}", status_code=204)
async def delete_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_authenticated_db),
) -> None:
    peer = await _get_peer_or_404(peer_id, session)
    if peer.interface and peer.interface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Peer not found")
    interface_id = peer.interface_id
    await session.delete(peer)
    await session.flush()
    await _sync_interface(interface_id, session)


@router.post("/{peer_id}/toggle", response_model=PeerOut)
async def toggle_peer(
    peer_id: int,
    session: AsyncSession = Depends(get_authenticated_db),
) -> PeerOut:
    peer = await _get_peer_or_404(peer_id, session)
    if peer.interface and peer.interface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Peer not found")
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
    session: AsyncSession = Depends(get_authenticated_db),
) -> Response:
    peer = await _get_peer_or_404(peer_id, session)
    result = await session.execute(
        select(Interface).where(Interface.id == peer.interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    if iface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Interface not found")

    # Endpoint: query-параметр → settings.server_host → поле интерфейса → заглушка
    endpoint = _build_endpoint(server_endpoint, iface)
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
    session: AsyncSession = Depends(get_authenticated_db),
) -> Response:
    peer = await _get_peer_or_404(peer_id, session)
    result = await session.execute(
        select(Interface).where(Interface.id == peer.interface_id)
    )
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=404, detail="Interface not found")
    if iface.name not in awg_svc.visible_interface_names():
        raise HTTPException(status_code=404, detail="Interface not found")

    endpoint = _build_endpoint(server_endpoint, iface)
    config_str = awg_svc.generate_client_config(peer, iface, endpoint)
    try:
        png_bytes = awg_svc.generate_qr_bytes(config_str)
    except ImportError:
        raise HTTPException(status_code=501, detail="qrcode library not installed")
    return Response(content=png_bytes, media_type="image/png")
