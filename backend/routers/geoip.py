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
from pydantic import BaseModel, ConfigDict, field_validator
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
            try:
                sources = await geoip_fetcher.update_all_zones(session, progress_cb=_broadcast)
                if sources:
                    _broadcast(f"GeoIP update complete: {len(sources)} source(s) processed")
                else:
                    _broadcast("GeoIP update complete: no enabled sources")
            except Exception as e:
                _broadcast(f"GeoIP update failed: {e}")
                logger.error("GeoIP update error: %s", e, exc_info=True)
        _broadcast("GeoIP update complete")
    finally:
        _update_running = False
        _broadcast("__done__")


# ── Schemas ───────────────────────────────────────────────────────────────

class GeoIPSourceCreate(BaseModel):
    country_code: str
    display_name: str
    url: str | None = None

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"[a-z]{2}", value):
            raise ValueError("country_code must match [a-z]{2}")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be empty")
        return value

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class GeoIPSourceUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    url: str | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be empty")
        return value

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class GeoIPSourceResponse(BaseModel):
    id: int
    country_code: str
    display_name: str
    url: str
    enabled: bool
    last_updated: datetime | None
    prefix_count: int | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


async def _get_source_or_404(session: AsyncSession, source_id: int) -> GeoipSource:
    source = await session.get(GeoipSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="GeoIP source not found")
    return source


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/sources", response_model=list[GeoIPSourceResponse])
async def list_sources(
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[GeoIPSourceResponse]:
    result = await session.execute(select(GeoipSource).order_by(GeoipSource.id))
    return [GeoIPSourceResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/sources", response_model=GeoIPSourceResponse, status_code=201)
async def create_source(
    body: GeoIPSourceCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> GeoIPSourceResponse:
    existing = await session.scalar(
        select(GeoipSource).where(GeoipSource.country_code == body.country_code)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"GeoIP source for country_code '{body.country_code}' already exists",
        )

    url = body.url or geoip_fetcher.build_default_url(body.country_code)
    if body.url is None:
        try:
            await geoip_fetcher.validate_source_url(url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    source = GeoipSource(
        name=body.display_name,
        display_name=body.display_name,
        country_code=body.country_code,
        url=url,
        ipset_name=geoip_fetcher.LOCAL_GEOIP_IPSET_NAME,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    await session.refresh(source)
    background_tasks.add_task(run_geoip_update)
    return GeoIPSourceResponse.model_validate(source)


@router.put("/sources/{source_id}", response_model=GeoIPSourceResponse)
async def update_source(
    source_id: int,
    body: GeoIPSourceUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> GeoIPSourceResponse:
    source = await _get_source_or_404(session, source_id)
    should_recalculate = False

    if body.display_name is not None:
        source.display_name = body.display_name
        source.name = body.display_name

    if body.enabled is not None and source.enabled != body.enabled:
        source.enabled = body.enabled
        should_recalculate = True

    if "url" in body.model_fields_set:
        next_url = body.url or geoip_fetcher.build_default_url(source.country_code)
        if body.url is None:
            try:
                await geoip_fetcher.validate_source_url(next_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if source.url != next_url:
            source.url = next_url
            should_recalculate = True

    session.add(source)
    await session.flush()
    await session.refresh(source)

    if should_recalculate:
        background_tasks.add_task(run_geoip_update)

    return GeoIPSourceResponse.model_validate(source)


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    result = await session.execute(select(GeoipSource).order_by(GeoipSource.id))
    sources = result.scalars().all()
    if len(sources) <= 1:
        raise HTTPException(status_code=422, detail="At least one GeoIP source must remain")

    source = next((item for item in sources if item.id == source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="GeoIP source not found")

    await session.delete(source)
    background_tasks.add_task(run_geoip_update)
    return {"status": "deleted"}


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
                "display_name": s.display_name,
                "country_code": s.country_code,
                "ipset_name": geoip_fetcher.LOCAL_GEOIP_IPSET_NAME,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None,
                "prefix_count": s.prefix_count,
                "ipset_count": ipsets.get(geoip_fetcher.LOCAL_GEOIP_IPSET_NAME, {}).get("count", 0),
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
