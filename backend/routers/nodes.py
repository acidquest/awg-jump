"""
Nodes router — заглушка для этапа 6 (Node Deployer).
Базовые CRUD операции над upstream нодами уже доступны.
SSH деплой, health-check и failover реализуются в этапе 6.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.upstream_node import UpstreamNode, NodeStatus
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class NodeOut(BaseModel):
    id: int
    name: str
    host: str
    ssh_port: int
    awg_port: int
    awg_address: str
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

    model_config = {"from_attributes": True}


class NodeCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    awg_port: int = 51821
    awg_address: str
    priority: int = 100


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    awg_port: Optional[int] = None
    priority: Optional[int] = None


def _node_to_out(node: UpstreamNode) -> NodeOut:
    return NodeOut(
        id=node.id,
        name=node.name,
        host=node.host,
        ssh_port=node.ssh_port,
        awg_port=node.awg_port,
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
    )


async def _get_node_or_404(node_id: int, session: AsyncSession) -> UpstreamNode:
    result = await session.execute(
        select(UpstreamNode).where(UpstreamNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


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
    node = UpstreamNode(
        name=body.name,
        host=body.host,
        ssh_port=body.ssh_port,
        awg_port=body.awg_port,
        awg_address=body.awg_address,
        priority=body.priority,
        status=NodeStatus.pending,
        is_active=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(node)
    await session.flush()
    await session.refresh(node)
    return _node_to_out(node)


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    return _node_to_out(await _get_node_or_404(node_id, session))


@router.put("/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: int,
    body: NodeUpdate,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    node = await _get_node_or_404(node_id, session)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(node, field, value)
    node.updated_at = datetime.now(timezone.utc)
    session.add(node)
    await session.flush()
    return _node_to_out(node)


@router.delete("/{node_id}", status_code=204)
async def delete_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    node = await _get_node_or_404(node_id, session)
    await session.delete(node)
    await session.flush()


@router.post("/{node_id}/deploy", status_code=202)
async def deploy_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    """SSH деплой — реализуется в этапе 6 (node_deployer.py)."""
    await _get_node_or_404(node_id, session)
    raise HTTPException(status_code=501, detail="Deploy not yet implemented (stage 6)")


@router.post("/{node_id}/activate", response_model=NodeOut)
async def activate_node(
    node_id: int,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> NodeOut:
    """Сделать ноду активной (переключить awg1 endpoint)."""
    node = await _get_node_or_404(node_id, session)
    if node.status not in (NodeStatus.online, NodeStatus.degraded):
        raise HTTPException(
            status_code=400,
            detail=f"Node status is {node.status.value}, must be online or degraded",
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
    return _node_to_out(node)
