"""
DNS router — управление split DNS (dnsmasq).
"""
import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.dns_domain import DnsDomain
from backend.models.dns_manual_address import DnsManualAddress
from backend.models.dns_zone_settings import DnsZoneSettings
from backend.routers.auth import get_current_user
import backend.services.dns_manager as dns_mgr

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dns", tags=["dns"])

_ZONE_KEY_PATTERN = re.compile(r"[^a-z0-9]+")
_SUPPORTED_PROTOCOLS = {"plain", "dot", "doh"}


class DomainCreate(BaseModel):
    domain: str
    zone: str = "local"
    enabled: bool = True


class DomainBulkCreate(BaseModel):
    domains: list[str] = Field(min_length=1)
    zone: str = "local"
    enabled: bool = True


class DomainUpdate(BaseModel):
    domain: Optional[str] = None
    zone: Optional[str] = None
    enabled: Optional[bool] = None


class ManualAddressCreate(BaseModel):
    domain: str
    address: str
    enabled: bool = True


class ManualAddressUpdate(BaseModel):
    domain: Optional[str] = None
    address: Optional[str] = None
    enabled: Optional[bool] = None


class DnsZoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    zone: str
    name: str
    dns_servers: list[str]
    description: str
    is_builtin: bool
    protocol: str
    endpoint_host: str
    endpoint_port: int | None
    endpoint_url: str
    bootstrap_address: str
    updated_at: datetime


class DnsZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = "plain"
    dns_servers: list[str] = Field(default_factory=list, max_length=3)
    endpoint_host: str = ""
    endpoint_port: int | None = None
    endpoint_url: str = ""
    bootstrap_address: str = ""
    domains: list[str] = Field(default_factory=list)

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_PROTOCOLS:
            raise ValueError(f"Unsupported DNS protocol: {value}")
        return normalized

    @field_validator("dns_servers")
    @classmethod
    def validate_dns_servers(cls, value: list[str]) -> list[str]:
        return _normalize_dns_servers(value) if value else []

    @model_validator(mode="after")
    def validate_protocol_fields(self) -> "DnsZoneCreate":
        _validate_zone_payload(
            protocol=self.protocol,
            dns_servers=self.dns_servers,
            endpoint_host=self.endpoint_host,
            endpoint_port=self.endpoint_port,
            endpoint_url=self.endpoint_url,
            bootstrap_address=self.bootstrap_address,
        )
        if self.protocol == "dot" and self.endpoint_port is None:
            self.endpoint_port = 853
        return self


class DnsZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    protocol: Optional[str] = None
    dns_servers: Optional[list[str]] = Field(default=None, max_length=3)
    endpoint_host: Optional[str] = None
    endpoint_port: Optional[int] = None
    endpoint_url: Optional[str] = None
    bootstrap_address: Optional[str] = None
    description: Optional[str] = None

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_PROTOCOLS:
            raise ValueError(f"Unsupported DNS protocol: {value}")
        return normalized

    @field_validator("dns_servers")
    @classmethod
    def validate_dns_servers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return value
        return _normalize_dns_servers(value)


def _normalize_dns_servers(servers: list[str]) -> list[str]:
    normalized: list[str] = []
    for server in servers:
        normalized_server = dns_mgr._normalize_dns_server(server)
        if normalized_server not in normalized:
            normalized.append(normalized_server)
    if not normalized:
        raise ValueError("dns_servers cannot be empty")
    return normalized


def _to_dict(d: DnsDomain) -> dict:
    zone = getattr(d, "upstream", "local")
    return {
        "id": d.id,
        "domain": d.domain,
        "zone": zone,
        "upstream": zone,
        "enabled": d.enabled,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _manual_address_to_dict(item: DnsManualAddress) -> dict:
    return {
        "id": item.id,
        "domain": item.domain,
        "address": item.address,
        "enabled": item.enabled,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _normalize_domain(raw: str) -> str:
    return raw.strip().lower().lstrip(".")


def _normalize_address(raw: str) -> str:
    candidate = raw.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP address: {raw}") from exc


def _normalize_domain_list(raw_domains: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in raw_domains:
        value = _normalize_domain(item)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_zone_key(name: str) -> str:
    normalized = _ZONE_KEY_PATTERN.sub("-", name.strip().lower()).strip("-")
    return normalized[:64] if normalized else "zone"


def _zone_to_response(zone: DnsZoneSettings) -> DnsZoneResponse:
    return DnsZoneResponse(
        zone=zone.zone,
        name=zone.name or zone.zone.title(),
        dns_servers=json.loads(zone.dns_servers),
        description=zone.description,
        is_builtin=zone.is_builtin,
        protocol=zone.protocol,
        endpoint_host=zone.endpoint_host,
        endpoint_port=zone.endpoint_port,
        endpoint_url=zone.endpoint_url,
        bootstrap_address=zone.bootstrap_address,
        updated_at=zone.updated_at,
    )


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _normalize_bootstrap_address(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise ValueError(f"Invalid bootstrap IP address: {raw}") from exc


def _extract_hostname_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("DoH endpoint must be a valid https:// URL")
    return parsed.hostname.rstrip(".").lower()


def _validate_zone_payload(
    *,
    protocol: str,
    dns_servers: list[str],
    endpoint_host: str,
    endpoint_port: int | None,
    endpoint_url: str,
    bootstrap_address: str,
) -> None:
    normalized_host = endpoint_host.strip().rstrip(".").lower()
    normalized_url = endpoint_url.strip()
    normalized_bootstrap = _normalize_bootstrap_address(bootstrap_address)

    if protocol == "plain":
        if not dns_servers:
            raise ValueError("At least one DNS server is required for plain DNS zones")
        return

    if protocol == "dot":
        if not normalized_host:
            raise ValueError("DoT zones require an endpoint host")
        if endpoint_port is not None and not (1 <= endpoint_port <= 65535):
            raise ValueError("DoT port must be in range 1..65535")
        if not _is_ip_address(normalized_host) and not normalized_bootstrap:
            raise ValueError("DoT zones with hostname endpoints require a bootstrap IP")
        return

    if protocol == "doh":
        doh_host = _extract_hostname_from_url(normalized_url)
        if not _is_ip_address(doh_host) and not normalized_bootstrap:
            raise ValueError("DoH zones with hostname endpoints require a bootstrap IP")
        return


async def _ensure_protocol_slot_available(session: AsyncSession, protocol: str, current_zone: str | None = None) -> None:
    if protocol not in {"dot", "doh"}:
        return
    result = await session.execute(select(DnsZoneSettings).where(DnsZoneSettings.protocol == protocol))
    for item in result.scalars().all():
        if item.is_builtin:
            continue
        if current_zone is not None and item.zone == current_zone:
            continue
        raise HTTPException(status_code=409, detail=f"Only one {protocol.upper()} zone can exist")


async def _get_zone_or_404(session: AsyncSession, zone_key: str) -> DnsZoneSettings:
    zone = await session.scalar(select(DnsZoneSettings).where(DnsZoneSettings.zone == zone_key))
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return zone


async def _ensure_domain_zone_exists(session: AsyncSession, zone_key: str) -> None:
    zone = await session.scalar(select(DnsZoneSettings.id).where(DnsZoneSettings.zone == zone_key))
    if zone is None:
        raise HTTPException(status_code=400, detail=f"Unknown zone: {zone_key}")


@router.get("/status")
async def get_status(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    status = dns_mgr.get_status()
    try:
        status["local_zone_dns"] = await dns_mgr.get_zone_dns(session, "local")
        status["vpn_zone_dns"] = await dns_mgr.get_zone_dns(session, "vpn")
    except Exception as exc:
        logger.warning("Could not load DNS zone settings for status: %s", exc)
    return status


@router.get("/domains")
async def list_domains(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await session.execute(select(DnsDomain).order_by(DnsDomain.domain))
    return [_to_dict(d) for d in result.scalars().all()]


@router.get("/manual-addresses")
async def list_manual_addresses(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await session.execute(select(DnsManualAddress).order_by(DnsManualAddress.domain))
    return [_manual_address_to_dict(item) for item in result.scalars().all()]


@router.get("/zones", response_model=list[DnsZoneResponse])
async def list_zones(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[DnsZoneResponse]:
    await dns_mgr.get_zones(session)
    result = await session.execute(
        select(DnsZoneSettings).order_by(
            case((DnsZoneSettings.zone == "local", 0), (DnsZoneSettings.zone == "vpn", 1), else_=2),
            DnsZoneSettings.name,
            DnsZoneSettings.zone,
        )
    )
    return [_zone_to_response(zone) for zone in result.scalars().all()]


@router.post("/zones", response_model=DnsZoneResponse, status_code=201)
async def create_zone(
    body: DnsZoneCreate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DnsZoneResponse:
    await dns_mgr.get_zones(session)
    await _ensure_protocol_slot_available(session, body.protocol)

    zone_key_base = _normalize_zone_key(body.name)
    zone_key = zone_key_base
    suffix = 2
    while await session.scalar(select(DnsZoneSettings.id).where(DnsZoneSettings.zone == zone_key)) is not None:
        zone_key = f"{zone_key_base[:56]}-{suffix}"
        suffix += 1

    zone = DnsZoneSettings(
        zone=zone_key,
        name=body.name.strip(),
        dns_servers=json.dumps(body.dns_servers),
        description="",
        is_builtin=False,
        protocol=body.protocol,
        endpoint_host=body.endpoint_host.strip().rstrip(".").lower() if body.protocol == "dot" else "",
        endpoint_port=body.endpoint_port if body.protocol == "dot" else None,
        endpoint_url=body.endpoint_url.strip() if body.protocol == "doh" else "",
        bootstrap_address=_normalize_bootstrap_address(body.bootstrap_address) if body.protocol in {"dot", "doh"} else "",
        updated_at=datetime.utcnow(),
    )
    session.add(zone)
    await session.flush()

    for domain in _normalize_domain_list(body.domains):
        existing = await session.scalar(select(DnsDomain.id).where(DnsDomain.domain == domain))
        if existing is not None:
            raise HTTPException(status_code=409, detail=f"Domain already exists: {domain}")
        session.add(
            DnsDomain(
                domain=domain,
                upstream=zone_key,
                enabled=True,
                created_at=datetime.now(timezone.utc),
            )
        )

    await session.commit()
    await session.refresh(zone)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after zone create failed: %s", exc)

    return _zone_to_response(zone)


@router.put("/zones/{zone}", response_model=DnsZoneResponse)
async def update_zone(
    zone: str,
    body: DnsZoneUpdate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DnsZoneResponse:
    await dns_mgr.get_zones(session)
    obj = await _get_zone_or_404(session, zone)

    protocol = body.protocol if body.protocol is not None else obj.protocol
    dns_servers = body.dns_servers if body.dns_servers is not None else json.loads(obj.dns_servers)
    endpoint_host = body.endpoint_host if body.endpoint_host is not None else obj.endpoint_host
    endpoint_port = body.endpoint_port if body.endpoint_port is not None else obj.endpoint_port
    endpoint_url = body.endpoint_url if body.endpoint_url is not None else obj.endpoint_url
    bootstrap_address = body.bootstrap_address if body.bootstrap_address is not None else obj.bootstrap_address

    if obj.is_builtin and protocol != "plain":
        raise HTTPException(status_code=400, detail="Built-in DNS zones must stay plain")
    await _ensure_protocol_slot_available(session, protocol, current_zone=obj.zone)
    try:
        _validate_zone_payload(
            protocol=protocol,
            dns_servers=dns_servers,
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            endpoint_url=endpoint_url,
            bootstrap_address=bootstrap_address,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.name is not None:
        obj.name = body.name.strip()
    if body.dns_servers is not None:
        obj.dns_servers = json.dumps(body.dns_servers)
    obj.protocol = protocol
    obj.endpoint_host = endpoint_host.strip().rstrip(".").lower() if protocol == "dot" else ""
    obj.endpoint_port = (853 if endpoint_port is None else endpoint_port) if protocol == "dot" else None
    obj.endpoint_url = endpoint_url.strip() if protocol == "doh" else ""
    obj.bootstrap_address = _normalize_bootstrap_address(bootstrap_address) if protocol in {"dot", "doh"} else ""
    if body.description is not None:
        obj.description = body.description
    obj.updated_at = datetime.utcnow()

    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after zone update failed: %s", exc)

    return _zone_to_response(obj)


@router.delete("/zones/{zone}", status_code=204)
async def delete_zone(
    zone: str,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    await dns_mgr.get_zones(session)
    obj = await _get_zone_or_404(session, zone)
    if obj.is_builtin or obj.zone in {"local", "vpn"}:
        raise HTTPException(status_code=400, detail="Built-in DNS zones cannot be deleted")

    rules = (await session.execute(select(DnsDomain).where(DnsDomain.upstream == zone))).scalars().all()
    for rule in rules:
        await session.delete(rule)
    await session.delete(obj)
    await session.commit()

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after zone delete failed: %s", exc)


@router.post("/domains", status_code=201)
async def create_domain(
    body: DomainCreate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    domain = _normalize_domain(body.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")

    await dns_mgr.get_zones(session)
    await _ensure_domain_zone_exists(session, body.zone)

    existing = await session.scalar(select(DnsDomain).where(DnsDomain.domain == domain))
    if existing:
        raise HTTPException(status_code=409, detail="Domain already exists")

    obj = DnsDomain(
        domain=domain,
        upstream=body.zone,
        enabled=body.enabled,
        created_at=datetime.now(timezone.utc),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after create failed: %s", exc)

    return _to_dict(obj)


@router.post("/manual-addresses", status_code=201)
async def create_manual_address(
    body: ManualAddressCreate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    domain = _normalize_domain(body.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")
    address = _normalize_address(body.address)
    existing = await session.scalar(select(DnsManualAddress).where(DnsManualAddress.domain == domain))
    if existing:
        raise HTTPException(status_code=409, detail="Manual replace address already exists")

    obj = DnsManualAddress(
        domain=domain,
        address=address,
        enabled=body.enabled,
        created_at=datetime.now(timezone.utc),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after manual address create failed: %s", exc)

    return _manual_address_to_dict(obj)


@router.post("/domains/bulk", status_code=201)
async def create_domains_bulk(
    body: DomainBulkCreate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    await dns_mgr.get_zones(session)
    await _ensure_domain_zone_exists(session, body.zone)

    domains = _normalize_domain_list(body.domains)
    if not domains:
        raise HTTPException(status_code=400, detail="At least one domain is required")

    existing_domains = (
        await session.execute(select(DnsDomain.domain).where(DnsDomain.domain.in_(domains)))
    ).scalars().all()
    duplicates = sorted(set(existing_domains))
    if duplicates:
        raise HTTPException(status_code=409, detail=f"Domains already exist: {', '.join(duplicates)}")

    created_ids: list[int] = []
    for domain in domains:
        obj = DnsDomain(
            domain=domain,
            upstream=body.zone,
            enabled=body.enabled,
            created_at=datetime.now(timezone.utc),
        )
        session.add(obj)
        await session.flush()
        created_ids.append(obj.id)

    await session.commit()

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after bulk create failed: %s", exc)

    return {"status": "added", "created": len(created_ids), "ids": created_ids}


@router.put("/domains/{domain_id}")
async def update_domain(
    domain_id: int,
    body: DomainUpdate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    if body.domain is not None:
        domain = _normalize_domain(body.domain)
        if not domain:
            raise HTTPException(status_code=400, detail="Domain cannot be empty")
        existing = await session.scalar(select(DnsDomain.id).where(DnsDomain.domain == domain, DnsDomain.id != domain_id))
        if existing is not None:
            raise HTTPException(status_code=409, detail="Domain already exists")
        obj.domain = domain
    if body.zone is not None:
        await _ensure_domain_zone_exists(session, body.zone)
        obj.upstream = body.zone
    if body.enabled is not None:
        obj.enabled = body.enabled

    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after update failed: %s", exc)

    return _to_dict(obj)


@router.put("/manual-addresses/{manual_address_id}")
async def update_manual_address(
    manual_address_id: int,
    body: ManualAddressUpdate,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    obj = await session.get(DnsManualAddress, manual_address_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manual replace address not found")

    if body.domain is not None:
        domain = _normalize_domain(body.domain)
        if not domain:
            raise HTTPException(status_code=400, detail="Domain cannot be empty")
        existing = await session.scalar(
            select(DnsManualAddress.id).where(DnsManualAddress.domain == domain, DnsManualAddress.id != manual_address_id)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Manual replace address already exists")
        obj.domain = domain
    if body.address is not None:
        obj.address = _normalize_address(body.address)
    if body.enabled is not None:
        obj.enabled = body.enabled

    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after manual address update failed: %s", exc)

    return _manual_address_to_dict(obj)


@router.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    await session.delete(obj)
    await session.commit()

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after delete failed: %s", exc)


@router.delete("/manual-addresses/{manual_address_id}", status_code=204)
async def delete_manual_address(
    manual_address_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    obj = await session.get(DnsManualAddress, manual_address_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manual replace address not found")

    await session.delete(obj)
    await session.commit()

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after manual address delete failed: %s", exc)


@router.post("/domains/{domain_id}/toggle")
async def toggle_domain(
    domain_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    obj = await session.get(DnsDomain, domain_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Domain not found")

    obj.enabled = not obj.enabled
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after toggle failed: %s", exc)

    return _to_dict(obj)


@router.post("/manual-addresses/{manual_address_id}/toggle")
async def toggle_manual_address(
    manual_address_id: int,
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    obj = await session.get(DnsManualAddress, manual_address_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Manual replace address not found")

    obj.enabled = not obj.enabled
    await session.commit()
    await session.refresh(obj)

    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        logger.warning("DNS reload after manual address toggle failed: %s", exc)

    return _manual_address_to_dict(obj)


@router.post("/reload")
async def reload_dns(
    _user: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await dns_mgr.reload(session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return dns_mgr.get_status()
