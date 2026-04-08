"""
DNS router — управление split DNS (dnsmasq).

Эндпоинты:
  GET  /api/dns/status           — статус dnsmasq
  GET  /api/dns/domains          — список доменов
  GET  /api/dns/zones            — список DNS зон
  POST /api/dns/domains          — добавить домен
  PUT  /api/dns/domains/{id}     — обновить домен
  PUT  /api/dns/zones/{zone}     — обновить DNS зону
  DELETE /api/dns/domains/{id}   — удалить домен
  POST /api/dns/domains/{id}/toggle — вкл/выкл домен
  POST /api/dns/reload           — перегенерировать конфиг и перезагрузить dnsmasq
"""
import ipaddress
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.dns_domain import DnsDomain, DnsUpstream
from backend.models.dns_zone_settings import DnsZoneSettings
from backend.routers.auth import get_current_user
import backend.services.dns_manager as dns_mgr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dns", tags=["dns"])


# ── Schemas ───────────────────────────────────────────────────────────────

class DomainCreate(BaseModel):
    domain: str
    upstream: str = DnsUpstream.LOCAL.value
    enabled: bool = True


class DomainUpdate(BaseModel):
    domain: Optional[str] = None
    upstream: Optional[str] = None
    enabled: Optional[bool] = None


class DnsZoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    zone: str
    dns_servers: list[str]
    description: str
    updated_at: datetime


class DnsZoneUpdate(BaseModel):
    dns_servers: list[str] = Field(min_length=1, max_length=3)
    description: Optional[str] = None

    @field_validator("dns_servers")
    @classmethod
    def validate_dns_servers(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("dns_servers cannot be empty")

        validated: list[str] = []
        for server in value:
            try:
                validated.append(str(ipaddress.ip_address(server)))
            except ValueError as exc:
                raise ValueError(f"Invalid IP address: {server}") from exc
        return validated


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


def _zone_to_response(zone: DnsZoneSettings) -> DnsZoneResponse:
    return DnsZoneResponse(
        zone=zone.zone,
        dns_servers=json.loads(zone.dns_servers),
        description=zone.description,
        updated_at=zone.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Статус dnsmasq и параметры конфигурации."""
    status = dns_mgr.get_status()
    try:
        status["local_zone_dns"] = await dns_mgr.get_zone_dns(session, "local")
        status["vpn_zone_dns"] = await dns_mgr.get_zone_dns(session, "vpn")
    except Exception as e:
        logger.warning("Could not load DNS zone settings for status: %s", e)
    return status


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


@router.get("/zones", response_model=list[DnsZoneResponse])
async def list_zones(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[DnsZoneResponse]:
    await dns_mgr.get_zone_dns(session, "local")
    await dns_mgr.get_zone_dns(session, "vpn")

    result = await session.execute(
        select(DnsZoneSettings).order_by(DnsZoneSettings.zone)
    )
    return [_zone_to_response(zone) for zone in result.scalars().all()]


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
        await dns_mgr.reload(session)
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
        await dns_mgr.reload(session)
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
        await dns_mgr.reload(session)
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
        await dns_mgr.reload(session)
    except Exception as e:
        logger.warning("DNS reload after toggle failed: %s", e)

    return _to_dict(obj)


@router.put("/zones/{zone}", response_model=DnsZoneResponse)
async def update_zone(
    zone: str,
    body: DnsZoneUpdate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DnsZoneResponse:
    if zone not in {"local", "vpn"}:
        raise HTTPException(status_code=404, detail="Zone not found")

    await dns_mgr.get_zone_dns(session, zone)
    obj = await session.scalar(
        select(DnsZoneSettings).where(DnsZoneSettings.zone == zone)
    )
    if obj is None:
        raise HTTPException(status_code=404, detail="Zone not found")

    obj.dns_servers = json.dumps(body.dns_servers)
    if body.description is not None:
        obj.description = body.description
    obj.updated_at = datetime.utcnow()

    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as e:
        logger.warning("DNS reload after zone update failed: %s", e)

    return _zone_to_response(obj)


@router.post("/reload")
async def reload_dns(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Принудительно перегенерировать конфиг и перезагрузить dnsmasq."""
    try:
        await dns_mgr.reload(session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return dns_mgr.get_status()
