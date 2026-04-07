"""
Nodes router — управление upstream нодами.

Деплой запускается как фоновая задача, прогресс читается через SSE.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal, get_db
from backend.models.upstream_node import DeployLog, DeployStatus, NodeStatus, UpstreamNode
from backend.routers.auth import get_current_user
from backend.services.node_deployer import (
    _finish_log,
    cleanup_deploy_queue,
    deployer,
    get_deploy_queue,
)

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


class NodeCreate(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    awg_port: int = 51821
    awg_address: Optional[str] = None  # если None — выделяется автоматически при деплое
    priority: int = 100


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    awg_port: Optional[int] = None
    awg_address: Optional[str] = None
    priority: Optional[int] = None


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


# ── Вспомогательные функции ───────────────────────────────────────────────

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
        select(UpstreamNode).where(UpstreamNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


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
    for field, value in body.model_dump(exclude_none=True).items():
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

    if body and body.ssh_user and body.ssh_password:
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
        from backend.services.routing import update_vpn_route
        update_vpn_route(None)

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
        from backend.services.routing import update_vpn_route
        update_vpn_route("awg1")

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
    if node.public_key and node.awg_address:
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
        from backend.services.routing import update_vpn_route
        update_vpn_route("awg1")

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
        "deploy_logs": [log.model_dump() for log in logs],
    }


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
