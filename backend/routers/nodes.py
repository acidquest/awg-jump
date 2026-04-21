"""
Nodes router — управление upstream нодами.

Деплой запускается как фоновая задача, прогресс читается через SSE.
"""
import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal, get_db
from backend.models.interface import Interface
from backend.models.peer import Peer
from backend.models.upstream_node import DeployLog, DeployStatus, NodePeer, NodeStatus, ProvisioningMode, UpstreamNode
from backend.routers.auth import get_current_user
from backend.services.conf_parser import parse_peer_conf, render_peer_conf
from backend.services.node_deployer import (
    _finish_log,
    cleanup_deploy_queue,
    deployer,
    get_deploy_queue,
)
import backend.services.awg as awg_svc

router = APIRouter(prefix="/api/nodes", tags=["nodes"])
logger = logging.getLogger(__name__)
_UPSTREAM_ALLOWED_IPS = settings.awg1_allowed_ips or "0.0.0.0/0"


# ── Схемы ─────────────────────────────────────────────────────────────────

class NodeOut(BaseModel):
    id: int
    name: str
    host: str
    ssh_port: int
    awg_port: int
    provisioning_mode: str
    awg_address: Optional[str]
    public_key: Optional[str]
    status: str
    is_active: bool
    priority: int
    last_seen: Optional[datetime]
    last_deploy: Optional[datetime]
    rx_bytes: Optional[int]
    tx_bytes: Optional[int]
    latency_ms: Optional[float]
    created_at: Optional[datetime]
    can_redeploy: bool
    can_manage_peers: bool

    model_config = {"from_attributes": True}


class DeployLogOut(BaseModel):
    id: int
    node_id: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    status: str
    log_output: Optional[str]

    model_config = {"from_attributes": True}


class NodeDetailOut(NodeOut):
    last_deploy_log: Optional[DeployLogOut] = None
    raw_conf: Optional[str] = None


class NodePeerOut(BaseModel):
    id: int
    node_id: int
    name: str
    public_key: str
    preshared_key: Optional[str]
    tunnel_address: str
    allowed_ips: str
    persistent_keepalive: Optional[int]
    enabled: bool
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class NodeCreate(BaseModel):
    name: str
    host: str = ""
    ssh_port: int = 22
    awg_port: int = 51821
    awg_address: Optional[str] = None  # если None — выделяется автоматически при деплое
    priority: int = 100
    provisioning_mode: str = ProvisioningMode.managed.value
    conf_text: Optional[str] = None


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    awg_port: Optional[int] = None
    awg_address: Optional[str] = None
    priority: Optional[int] = None
    raw_conf: Optional[str] = None


class DeployRequest(BaseModel):
    node_id: int
    ssh_user: str
    ssh_password: str
    ssh_port: int = 22


class RedeployRequest(BaseModel):
    ssh_user: str
    ssh_password: str
    ssh_port: int = 22


class DeleteRequest(BaseModel):
    """SSH credentials для остановки контейнера на ноде (опционально)."""
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_port: int = 22


class NodePeerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tunnel_address: str = Field(min_length=3, max_length=64)
    allowed_ips: str = Field(default="0.0.0.0/0", max_length=256)
    persistent_keepalive: Optional[int] = Field(default=25, ge=0, le=65535)
    enabled: bool = True


class NodePeerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    tunnel_address: Optional[str] = Field(default=None, min_length=3, max_length=64)
    allowed_ips: Optional[str] = Field(default=None, max_length=256)
    persistent_keepalive: Optional[int] = Field(default=None, ge=0, le=65535)
    enabled: Optional[bool] = None


# ── Вспомогательные функции ───────────────────────────────────────────────

def _node_to_out(node: UpstreamNode) -> NodeOut:
    return NodeOut(
        id=node.id,
        name=node.name,
        host=node.host,
        ssh_port=node.ssh_port,
        awg_port=node.awg_port,
        provisioning_mode=node.provisioning_mode.value if hasattr(node.provisioning_mode, "value") else node.provisioning_mode,
        awg_address=node.awg_address,
        public_key=node.public_key,
        status=node.status.value if hasattr(node.status, "value") else node.status,
        is_active=node.is_active,
        priority=node.priority,
        last_seen=node.last_seen,
        last_deploy=node.last_deploy,
        rx_bytes=node.rx_bytes,
        tx_bytes=node.tx_bytes,
        latency_ms=node.latency_ms,
        created_at=node.created_at,
        can_redeploy=(node.provisioning_mode == ProvisioningMode.managed),
        can_manage_peers=(node.provisioning_mode == ProvisioningMode.managed),
    )


def _log_to_out(log: DeployLog) -> DeployLogOut:
    return DeployLogOut(
        id=log.id,
        node_id=log.node_id,
        started_at=log.started_at,
        finished_at=log.finished_at,
        status=log.status.value if hasattr(log.status, "value") else log.status,
        log_output=log.log_output,
    )


async def _get_node_or_404(node_id: int, session: AsyncSession) -> UpstreamNode:
    result = await session.execute(
        select(UpstreamNode)
        .options(selectinload(UpstreamNode.shared_peers))
        .where(UpstreamNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


async def _get_awg1_or_500(session: AsyncSession) -> Interface:
    result = await session.execute(select(Interface).where(Interface.name == "awg1"))
    iface = result.scalar_one_or_none()
    if iface is None:
        raise HTTPException(status_code=500, detail="awg1 interface not found")
    return iface


def _node_peer_to_out(peer: NodePeer) -> NodePeerOut:
    return NodePeerOut(
        id=peer.id,
        node_id=peer.node_id,
        name=peer.name,
        public_key=peer.public_key,
        preshared_key=peer.preshared_key,
        tunnel_address=peer.tunnel_address,
        allowed_ips=peer.allowed_ips,
        persistent_keepalive=peer.persistent_keepalive,
        enabled=peer.enabled,
        created_at=peer.created_at,
    )


def _derive_public_key(private_key: str) -> str:
    proc = subprocess.run(
        ["awg", "pubkey"],
        input=private_key.encode(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace") or "awg pubkey failed")
    return proc.stdout.decode().strip()


async def _apply_manual_node_to_awg1(session: AsyncSession, node: UpstreamNode) -> None:
    awg1 = await _get_awg1_or_500(session)
    awg1.private_key = node.private_key
    awg1.public_key = _derive_public_key(node.private_key or "")
    awg1.endpoint = f"{node.host}:{node.awg_port}"
    awg1.allowed_ips = node.client_allowed_ips or settings.awg1_allowed_ips
    awg1.persistent_keepalive = node.client_keepalive or settings.awg1_persistent_keepalive

    parsed = parse_peer_conf(node.raw_conf or "", name=node.name)
    for key, attr in {
        "JC": "obf_jc",
        "JMIN": "obf_jmin",
        "JMAX": "obf_jmax",
        "S1": "obf_s1",
        "S2": "obf_s2",
        "S3": "obf_s3",
        "S4": "obf_s4",
        "H1": "obf_h1",
        "H2": "obf_h2",
        "H3": "obf_h3",
        "H4": "obf_h4",
    }.items():
        if key in parsed.obfuscation:
            setattr(awg1, attr, parsed.obfuscation[key])

    session.add(awg1)
    await session.flush()

    synthetic_peer = Peer(
        interface_id=awg1.id,
        name=node.name,
        public_key=node.public_key or "",
        preshared_key=node.preshared_key,
        allowed_ips=node.client_allowed_ips or settings.awg1_allowed_ips,
        persistent_keepalive=node.client_keepalive or settings.awg1_persistent_keepalive,
        enabled=True,
    )
    await awg_svc.apply_interface(awg1, [synthetic_peer])


async def _create_deploy_log(node_id: int) -> int:
    """Создаёт запись DeployLog и возвращает её id."""
    async with AsyncSessionLocal() as session:
        log = DeployLog(
            node_id=node_id,
            started_at=datetime.now(timezone.utc),
            status=DeployStatus.running,
            log_output="",
        )
        session.add(log)
        await session.flush()
        log_id = log.id
        await session.commit()
    return log_id


# ── Фоновые задачи деплоя ─────────────────────────────────────────────────

async def _run_deploy(node_id: int, log_id: int, ssh_user: str, ssh_password: str, ssh_port: int) -> None:
    try:
        await deployer.deploy(node_id, log_id, ssh_user, ssh_password, ssh_port)
    finally:
        # Очистить очередь через 5 минут (дать время клиенту дочитать)
        await asyncio.sleep(300)
        cleanup_deploy_queue(log_id)


async def _run_redeploy(node_id: int, log_id: int, ssh_user: str, ssh_password: str, ssh_port: int) -> None:
    try:
        await deployer.redeploy(node_id, log_id, ssh_user, ssh_password, ssh_port)
    finally:
        await asyncio.sleep(300)
        cleanup_deploy_queue(log_id)


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[NodeOut])
async def list_nodes(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[NodeOut]:
    result = await session.execute(
        select(UpstreamNode).order_by(UpstreamNode.priority, UpstreamNode.id)
    )
    return [_node_to_out(n) for n in result.scalars().all()]


@router.post("", response_model=NodeOut, status_code=201)
async def create_node(
    body: NodeCreate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    try:
        provisioning_mode = ProvisioningMode(body.provisioning_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsupported provisioning_mode") from exc
    parsed = None
    if provisioning_mode == ProvisioningMode.manual:
        if not body.conf_text:
            raise HTTPException(status_code=400, detail="conf_text is required for manual nodes")
        try:
            parsed = parse_peer_conf(body.conf_text, name=body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    node = UpstreamNode(
        name=parsed.name if parsed else body.name,
        host=parsed.endpoint_host if parsed else body.host,
        ssh_port=body.ssh_port,
        awg_port=parsed.endpoint_port if parsed else body.awg_port,
        provisioning_mode=provisioning_mode,
        awg_address=parsed.tunnel_address if parsed else body.awg_address,
        public_key=parsed.public_key if parsed else None,
        private_key=parsed.private_key if parsed else None,
        preshared_key=parsed.preshared_key if parsed else None,
        raw_conf=parsed.raw_conf if parsed else body.conf_text,
        client_dns=",".join(parsed.dns_servers) if parsed else None,
        client_allowed_ips=",".join(parsed.allowed_ips) if parsed else None,
        client_keepalive=parsed.persistent_keepalive if parsed else None,
        priority=body.priority,
        status=NodeStatus.online if parsed else NodeStatus.pending,
        is_active=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(node)
    await session.flush()
    await session.refresh(node)
    return _node_to_out(node)


@router.post("/deploy", status_code=202)
async def deploy_node(
    body: DeployRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """
    Запускает деплой ноды как фоновую задачу.
    Возвращает deploy_log_id для подключения к SSE-стриму.
    SSH пароль не сохраняется.
    """
    node = await _get_node_or_404(body.node_id, session)

    if node.status == NodeStatus.deploying:
        raise HTTPException(status_code=409, detail="Node is already being deployed")
    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Manual nodes cannot be deployed via SSH")

    log_id = await _create_deploy_log(body.node_id)
    # Инициализировать очередь до старта задачи
    get_deploy_queue(log_id)

    background_tasks.add_task(
        _run_deploy,
        body.node_id,
        log_id,
        body.ssh_user,
        body.ssh_password,
        body.ssh_port,
    )

    return {"deploy_log_id": log_id, "node_id": body.node_id}


@router.get("/deploy/{log_id}/stream")
async def stream_deploy(
    log_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    """
    SSE-стрим вывода деплоя.
    Формат: data: {"step": N, "total": M, "message": "...", "status": "running|ok|error"}
    """
    log = await session.get(DeployLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Deploy log not found")

    already_done = log.status != DeployStatus.running
    stored_output = log.log_output or ""

    async def generate():
        # Если деплой уже завершён — отдать сохранённый лог
        if already_done:
            for line in stored_output.splitlines():
                line = line.strip()
                if not line:
                    continue
                payload = json.dumps({"message": line, "status": log.status.value})
                yield f"data: {payload}\n\n"
            yield 'data: {"status": "done"}\n\n'
            return

        # Деплой ещё идёт — читать из очереди
        queue = get_deploy_queue(log_id)
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=30.0)
                if item is None:
                    yield 'data: {"status": "done"}\n\n'
                    break
                yield f"data: {item}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{node_id}", response_model=NodeDetailOut)
async def get_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeDetailOut:
    node = await _get_node_or_404(node_id, session)
    last_log_result = await session.execute(
        select(DeployLog)
        .where(DeployLog.node_id == node_id)
        .order_by(DeployLog.started_at.desc())
        .limit(1)
    )
    last_log = last_log_result.scalar_one_or_none()

    out = NodeDetailOut(**_node_to_out(node).model_dump())
    out.raw_conf = node.raw_conf
    if last_log:
        out.last_deploy_log = _log_to_out(last_log)
    return out


@router.put("/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: int,
    body: NodeUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    node = await _get_node_or_404(node_id, session)
    if body.raw_conf and node.provisioning_mode == ProvisioningMode.manual:
        try:
            parsed = parse_peer_conf(body.raw_conf, name=body.name or node.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        node.name = parsed.name
        node.host = parsed.endpoint_host
        node.awg_port = parsed.endpoint_port
        node.awg_address = parsed.tunnel_address
        node.public_key = parsed.public_key
        node.private_key = parsed.private_key
        node.preshared_key = parsed.preshared_key
        node.raw_conf = parsed.raw_conf
        node.client_dns = ",".join(parsed.dns_servers)
        node.client_allowed_ips = ",".join(parsed.allowed_ips)
        node.client_keepalive = parsed.persistent_keepalive
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "raw_conf":
            continue
        setattr(node, field, value)
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    await session.flush()
    return _node_to_out(node)


@router.delete("/{node_id}", status_code=204)
async def delete_node(
    node_id: int,
    body: Optional[DeleteRequest] = None,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    """
    Удаляет ноду из БД. Если переданы SSH credentials — останавливает контейнер.
    """
    node = await _get_node_or_404(node_id, session)

    if node.provisioning_mode == ProvisioningMode.managed and body and body.ssh_user and body.ssh_password:
        try:
            await deployer.remove(
                node_id,
                ssh_user=body.ssh_user,
                ssh_password=body.ssh_password,
                ssh_port=body.ssh_port,
            )
        except Exception as exc:
            logger.warning("[delete_node] Remote cleanup error for node %d: %s", node_id, exc)
    elif node.public_key:
        # Убрать peer из awg1 без SSH
        from backend.services.awg import _run_cmd
        _run_cmd(["awg", "set", "awg1", "peer", node.public_key, "remove"])

    if node.is_active:
        from backend.services.routing import update_upstream_host_route, update_vpn_route
        update_vpn_route(None)
        update_upstream_host_route(None)

    await session.delete(node)
    await session.flush()


@router.post("/{node_id}/reset", response_model=NodeOut)
async def reset_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    """
    Сбросить статус ноды на online и пере-добавить peer в awg1.
    Используется для восстановления ноды после offline без повторного деплоя.
    """
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode == ProvisioningMode.manual:
        await _apply_manual_node_to_awg1(session, node)
        node.status = NodeStatus.online
        node.updated_at = datetime.now(timezone.utc)
        session.add(node)
        await session.flush()
        return _node_to_out(node)

    if not node.public_key or not node.awg_address:
        raise HTTPException(
            status_code=400,
            detail="Node has no AWG keypair — deploy first",
        )

    from backend.services.awg import _run_cmd

    # Пере-добавить peer в awg1 (может не существовать если awg1 не был запущен при деплое)
    rc, out = _run_cmd([
        "awg", "set", "awg1",
        "peer", node.public_key,
        "endpoint", f"{node.host}:{node.awg_port}",
        "allowed-ips", _UPSTREAM_ALLOWED_IPS,
        "persistent-keepalive", "25",
    ])
    if rc != 0:
        logger.warning("[reset_node] awg set awg1 peer rc=%d: %s", rc, out)

    # Сбросить статус
    node.status = NodeStatus.online
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    await session.flush()

    # Сбросить счётчик неудач
    from backend.services.node_deployer import _health_fail_counts
    _health_fail_counts.pop(node_id, None)

    if node.is_active:
        from backend.services.routing import update_upstream_host_route, update_vpn_route
        update_vpn_route("awg1")
        update_upstream_host_route(node.awg_address)

    return _node_to_out(node)


@router.post("/{node_id}/activate", response_model=NodeOut)
async def activate_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    """Переключить активную ноду вручную."""
    node = await _get_node_or_404(node_id, session)
    if node.status == NodeStatus.pending:
        raise HTTPException(
            status_code=400,
            detail="Node is not deployed yet",
        )

    # Деактивировать все остальные
    result = await session.execute(
        select(UpstreamNode).where(UpstreamNode.is_active == True)  # noqa: E712
    )
    for active in result.scalars().all():
        active.is_active = False
        session.add(active)

    node.is_active = True
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    await session.flush()

    # Переключить awg1 endpoint
    if node.provisioning_mode == ProvisioningMode.manual and node.raw_conf:
        await _apply_manual_node_to_awg1(session, node)
        from backend.services.routing import update_upstream_host_route, update_vpn_route
        update_vpn_route("awg1")
        if node.awg_address:
            update_upstream_host_route(node.awg_address)
    elif node.public_key and node.awg_address:
        from backend.services.awg import _run_cmd
        rc, out = _run_cmd([
            "awg", "set", "awg1",
            "peer", node.public_key,
            "endpoint", f"{node.host}:{node.awg_port}",
            "allowed-ips", _UPSTREAM_ALLOWED_IPS,
            "persistent-keepalive", "25",
        ])
        if rc != 0:
            logger.warning("[activate_node] awg set awg1 peer failed: %s", out)
        from backend.services.routing import update_upstream_host_route, update_vpn_route
        update_vpn_route("awg1")
        update_upstream_host_route(node.awg_address)

    return _node_to_out(node)


@router.post("/{node_id}/check", response_model=dict)
async def check_node_health(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """Принудительная проверка доступности ноды."""
    await _get_node_or_404(node_id, session)
    result = await deployer.check_health(node_id)
    return result


@router.get("/{node_id}/stats", response_model=dict)
async def get_node_stats(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """Метрики ноды: latency, трафик, last_seen, список deploy logs."""
    node = await _get_node_or_404(node_id, session)

    logs_result = await session.execute(
        select(DeployLog)
        .where(DeployLog.node_id == node_id)
        .order_by(DeployLog.started_at.desc())
        .limit(10)
    )
    logs = [_log_to_out(log) for log in logs_result.scalars().all()]

    return {
        "node_id": node.id,
        "status": node.status.value if hasattr(node.status, "value") else node.status,
        "is_active": node.is_active,
        "latency_ms": node.latency_ms,
        "rx_bytes": node.rx_bytes,
        "tx_bytes": node.tx_bytes,
        "last_seen": node.last_seen,
        "last_deploy": node.last_deploy,
        "provisioning_mode": node.provisioning_mode.value if hasattr(node.provisioning_mode, "value") else node.provisioning_mode,
        "shared_peers": [_node_peer_to_out(peer).model_dump() for peer in node.shared_peers],
        "deploy_logs": [log.model_dump() for log in logs],
    }


async def _get_node_peer_or_404(node_id: int, peer_id: int, session: AsyncSession) -> NodePeer:
    result = await session.execute(
        select(NodePeer).where(NodePeer.id == peer_id, NodePeer.node_id == node_id)
    )
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Node peer not found")
    return peer


@router.get("/{node_id}/peers", response_model=list[NodePeerOut])
async def list_node_peers(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[NodePeerOut]:
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode != ProvisioningMode.managed:
        return []
    return [_node_peer_to_out(peer) for peer in node.shared_peers]


@router.post("/{node_id}/peers", response_model=NodePeerOut, status_code=201)
async def create_node_peer(
    node_id: int,
    body: NodePeerCreate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodePeerOut:
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Only managed nodes support shared peers")
    priv, pub = awg_svc.generate_keypair()
    peer = NodePeer(
        node_id=node_id,
        name=body.name,
        private_key=priv,
        public_key=pub,
        tunnel_address=body.tunnel_address,
        allowed_ips=body.allowed_ips,
        persistent_keepalive=body.persistent_keepalive,
        enabled=body.enabled,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(peer)
    await session.flush()
    return _node_peer_to_out(peer)


@router.put("/{node_id}/peers/{peer_id}", response_model=NodePeerOut)
async def update_node_peer(
    node_id: int,
    peer_id: int,
    body: NodePeerUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodePeerOut:
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Only managed nodes support shared peers")
    peer = await _get_node_peer_or_404(node_id, peer_id, session)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(peer, field, value)
    peer.updated_at = datetime.now(timezone.utc)
    session.add(peer)
    await session.flush()
    return _node_peer_to_out(peer)


@router.delete("/{node_id}/peers/{peer_id}", status_code=204)
async def delete_node_peer(
    node_id: int,
    peer_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Only managed nodes support shared peers")
    peer = await _get_node_peer_or_404(node_id, peer_id, session)
    await session.delete(peer)
    await session.flush()


@router.get("/{node_id}/peers/{peer_id}/config")
async def export_node_peer_config(
    node_id: int,
    peer_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> Response:
    node = await _get_node_or_404(node_id, session)
    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Only managed nodes support shared peers")
    peer = await _get_node_peer_or_404(node_id, peer_id, session)
    awg1 = await _get_awg1_or_500(session)
    config = render_peer_conf(
        private_key=peer.private_key,
        tunnel_address=peer.tunnel_address,
        dns_servers=[],
        obfuscation={
            key: value
            for key, value in {
                "S1": awg1.obf_s1,
                "S2": awg1.obf_s2,
                "S3": awg1.obf_s3,
                "S4": awg1.obf_s4,
                "H1": awg1.obf_h1,
                "H2": awg1.obf_h2,
                "H3": awg1.obf_h3,
                "H4": awg1.obf_h4,
            }.items()
            if value is not None
        },
        public_key=node.public_key or "",
        endpoint=f"{node.host}:{node.awg_port}",
        allowed_ips=[peer.allowed_ips or "0.0.0.0/0"],
        preshared_key=peer.preshared_key,
        persistent_keepalive=peer.persistent_keepalive,
    )
    filename = f"{peer.name or f'node-peer-{peer.id}'}.conf"
    return Response(
        content=config,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{node_id}/redeploy", status_code=202)
async def redeploy_node(
    node_id: int,
    body: RedeployRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """
    Повторный деплой: обновляет исходники, пересобирает образ,
    перезапускает контейнер. Ключи не меняются.
    """
    node = await _get_node_or_404(node_id, session)

    if node.status == NodeStatus.deploying:
        raise HTTPException(status_code=409, detail="Node is already being deployed")

    if node.provisioning_mode != ProvisioningMode.managed:
        raise HTTPException(status_code=400, detail="Manual nodes do not support redeploy")

    if not node.private_key:
        raise HTTPException(
            status_code=400,
            detail="Node has not been deployed yet (no private key). Use /deploy first.",
        )

    log_id = await _create_deploy_log(node_id)
    get_deploy_queue(log_id)

    background_tasks.add_task(
        _run_redeploy,
        node_id,
        log_id,
        body.ssh_user,
        body.ssh_password,
        body.ssh_port,
    )

    return {"deploy_log_id": log_id, "node_id": node_id}
