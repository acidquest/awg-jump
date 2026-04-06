"""
GeoIP API — управление источниками, обновление, SSE прогресс.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db, AsyncSessionLocal
from backend.models.geoip import GeoipSource
from backend.routers.auth import get_current_user
from backend.services import geoip_fetcher, ipset_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/geoip", tags=["geoip"])

# ── SSE прогресс — очереди подписчиков ───────────────────────────────────
# Каждая активная SSE-сессия получает свою очередь.
_progress_subscribers: list[asyncio.Queue] = []
_update_running = False


def _broadcast(message: str) -> None:
    """Рассылает сообщение всем активным SSE-подписчикам."""
    for q in _progress_subscribers:
        q.put_nowait({"message": message, "ts": datetime.now(timezone.utc).isoformat()})


# ── Background job ────────────────────────────────────────────────────────

async def run_geoip_update() -> None:
    """Фоновая задача: скачать GeoIP → обновить ipset → записать в БД."""
    global _update_running
    if _update_running:
        _broadcast("Update already in progress, skipping")
        return

    _update_running = True
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GeoipSource).where(GeoipSource.enabled == True)  # noqa: E712
            )
            sources = result.scalars().all()

        if not sources:
            _broadcast("No enabled GeoIP sources configured")
            return

        for source in sources:
            try:
                _broadcast(f"Starting update for {source.country_code} ({source.name})")
                prefixes = await geoip_fetcher.fetch(source, progress_cb=_broadcast)

                _broadcast(f"Updating ipset {source.ipset_name}...")
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    ipset_manager.create_or_update,
                    source.ipset_name,
                    prefixes,
                )

                # Обновить метаданные в БД
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(GeoipSource).where(GeoipSource.id == source.id)
                    )
                    src = result.scalar_one_or_none()
                    if src:
                        src.last_updated = datetime.now(timezone.utc)
                        src.prefix_count = len(prefixes)
                        session.add(src)
                        await session.commit()

                _broadcast(
                    f"Done: {source.country_code} — {len(prefixes)} prefixes loaded"
                )
            except Exception as e:
                _broadcast(f"Error updating {source.country_code}: {e}")
                logger.error("GeoIP update error: %s", e)

        _broadcast("GeoIP update complete")
    finally:
        _update_running = False
        _broadcast("__done__")


# ── Schemas ───────────────────────────────────────────────────────────────

class GeoipSourceOut(BaseModel):
    id: int
    name: str
    url: str
    country_code: str
    ipset_name: str
    last_updated: datetime | None
    prefix_count: int | None
    enabled: bool

    model_config = {"from_attributes": True}


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/sources", response_model=list[GeoipSourceOut])
async def list_sources(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[GeoipSourceOut]:
    result = await session.execute(select(GeoipSource).order_by(GeoipSource.id))
    return [GeoipSourceOut.model_validate(s) for s in result.scalars().all()]


@router.post("/update", status_code=202)
async def trigger_update(
    background_tasks: BackgroundTasks,
    _user: str = Depends(get_current_user),
) -> dict:
    if _update_running:
        raise HTTPException(status_code=409, detail="Update already running")
    background_tasks.add_task(run_geoip_update)
    return {"status": "started"}


@router.get("/status")
async def get_status(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    result = await session.execute(select(GeoipSource).order_by(GeoipSource.id))
    sources = result.scalars().all()
    ipsets = {s["name"]: s for s in ipset_manager.list_sets()}
    return {
        "update_running": _update_running,
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "country_code": s.country_code,
                "ipset_name": s.ipset_name,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None,
                "prefix_count": s.prefix_count,
                "ipset_count": ipsets.get(s.ipset_name, {}).get("count", 0),
                "cache_fresh": geoip_fetcher._is_cache_fresh(s.country_code),
            }
            for s in sources
        ],
    }


@router.get("/progress")
async def stream_progress(
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    """SSE поток прогресса обновления GeoIP."""
    queue: asyncio.Queue = asyncio.Queue()
    _progress_subscribers.append(queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    data = json.dumps(event)
                    yield f"data: {data}\n\n"
                    if event.get("message") == "__done__":
                        break
                except asyncio.TimeoutError:
                    # keepalive
                    yield ": keepalive\n\n"
        finally:
            _progress_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
