"""
DNS router — управление split DNS (dnsmasq).

Эндпоинты:
  GET  /api/dns/status           — статус dnsmasq
  GET  /api/dns/domains          — список доменов
  POST /api/dns/domains          — добавить домен
  PUT  /api/dns/domains/{id}     — обновить домен
  DELETE /api/dns/domains/{id}   — удалить домен
  POST /api/dns/domains/{id}/toggle — вкл/выкл домен
  POST /api/dns/reload           — перегенерировать конфиг и перезагрузить dnsmasq
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.dns_domain import DnsDomain, DnsUpstream
from backend.routers.auth import get_current_user
import backend.services.dns_manager as dns_mgr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dns", tags=["dns"])


# ── Schemas ───────────────────────────────────────────────────────────────

class DomainCreate(BaseModel):
    domain: str
    upstream: str = "yandex"
    enabled: bool = True


class DomainUpdate(BaseModel):
    domain: Optional[str] = None
    upstream: Optional[str] = None
    enabled: Optional[bool] = None


def _to_dict(d: DnsDomain) -> dict:
    return {
        "id": d.id,
        "domain": d.domain,
        "upstream": d.upstream.value if isinstance(d.upstream, DnsUpstream) else d.upstream,
        "enabled": d.enabled,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _normalize_domain(raw: str) -> str:
    """Приводит домен к нижнему регистру, убирает точку в начале и пробелы."""
    return raw.strip().lower().lstrip(".")


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(
    _user: str = Depends(get_current_user),
) -> dict:
    """Статус dnsmasq и параметры конфигурации."""
    return dns_mgr.get_status()


@router.get("/domains")
async def list_domains(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Список всех доменов в таблице split DNS."""
    result = await session.execute(
        select(DnsDomain).order_by(DnsDomain.domain)
    )
    return [_to_dict(d) for d in result.scalars().all()]


@router.post("/domains", status_code=201)
async def create_domain(
    body: DomainCreate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Добавить домен в таблицу split DNS."""
    domain = _normalize_domain(body.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")

    try:
        upstream = DnsUpstream(body.upstream)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid upstream: {body.upstream!r}")

    existing = await session.scalar(
        select(DnsDomain).where(DnsDomain.domain == domain)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Domain already exists")

    obj = DnsDomain(
        domain=domain,
        upstream=upstream,
        enabled=body.enabled,
        created_at=datetime.now(timezone.utc),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.apply_from_db()
    except Exception as e:
        logger.warning("DNS reload after create failed: %s", e)

    return _to_dict(obj)


@router.put("/domains/{domain_id}")
async def update_domain(
    domain_id: int,
    body: DomainUpdate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить домен (имя / upstream / enabled)."""
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    if body.domain is not None:
        obj.domain = _normalize_domain(body.domain)
    if body.upstream is not None:
        try:
            obj.upstream = DnsUpstream(body.upstream)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid upstream: {body.upstream!r}")
    if body.enabled is not None:
        obj.enabled = body.enabled

    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.apply_from_db()
    except Exception as e:
        logger.warning("DNS reload after update failed: %s", e)

    return _to_dict(obj)


@router.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Удалить домен из таблицы split DNS."""
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    await session.delete(obj)
    await session.commit()

    try:
        await dns_mgr.apply_from_db()
    except Exception as e:
        logger.warning("DNS reload after delete failed: %s", e)


@router.post("/domains/{domain_id}/toggle")
async def toggle_domain(
    domain_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Включить или отключить домен."""
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    obj.enabled = not obj.enabled
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.apply_from_db()
    except Exception as e:
        logger.warning("DNS reload after toggle failed: %s", e)

    return _to_dict(obj)


@router.post("/reload")
async def reload_dns(
    _user: str = Depends(get_current_user),
) -> dict:
    """Принудительно перегенерировать конфиг и перезагрузить dnsmasq."""
    try:
        await dns_mgr.apply_from_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return dns_mgr.get_status()
