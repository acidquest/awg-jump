from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, DnsDomainRule, DnsManualAddress, DnsUpstream, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.dns import build_dnsmasq_preview
from app.services.dns_runtime import restart_dnsmasq, status as dns_status
from app.services.external_ip import effective_fqdn_prefixes
from app.services.nftables_manager import TABLE_NAME as NFT_TABLE_NAME
from app.services.routing import firewall_backend, fqdn_ipset_name


router = APIRouter(prefix="/api/dns", tags=["dns"])
_ZONE_KEY_PATTERN = re.compile(r"[^a-z0-9]+")
_HOSTNAME_REGEX = re.compile(r"^(?=.{1,253}$)(?!-)(?:[a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}\.?$", re.IGNORECASE)
_SUPPORTED_PROTOCOLS = {"plain", "dot", "doh"}


def _normalize_domain(domain: str) -> str:
    return domain.lower().strip().strip(".")


def _normalize_ip_address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP address: {value}") from exc


def _normalize_domains(domains: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in domains:
        value = _normalize_domain(item)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _normalize_zone_key(name: str) -> str:
    normalized = _ZONE_KEY_PATTERN.sub("-", name.strip().lower()).strip("-")
    return normalized[:64] if normalized else "zone"


def _is_valid_dns_server(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", candidate):
        return all(0 <= int(part) <= 255 for part in candidate.split("."))
    if ":" in candidate:
        return bool(re.match(r"^[0-9a-fA-F:]+$", candidate))
    return bool(_HOSTNAME_REGEX.match(candidate))


def _normalize_dns_servers(servers: list[str]) -> list[str]:
    normalized: list[str] = []
    for server in servers:
        candidate = server.strip().rstrip(".").lower()
        if not _is_valid_dns_server(candidate):
            raise ValueError(f"Invalid DNS server: {server}")
        if candidate not in normalized:
            normalized.append(candidate)
    if not normalized:
        raise ValueError("At least one DNS server is required")
    return normalized


def _normalize_bootstrap_address(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise ValueError(f"Invalid bootstrap IP address: {value}") from exc


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _extract_doh_hostname(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("DoH endpoint must be a valid https:// URL")
    return parsed.hostname.rstrip(".").lower()


def _validate_zone_payload(
    *,
    protocol: str,
    servers: list[str],
    endpoint_host: str,
    endpoint_port: int | None,
    endpoint_url: str,
    bootstrap_address: str,
) -> None:
    normalized_host = endpoint_host.strip().rstrip(".").lower()
    normalized_bootstrap = _normalize_bootstrap_address(bootstrap_address)
    if protocol == "plain":
        if not servers:
            raise ValueError("At least one DNS server is required")
        return
    if protocol == "dot":
        if not normalized_host:
            raise ValueError("DoT zones require an endpoint host")
        if endpoint_port is not None and not (1 <= endpoint_port <= 65535):
            raise ValueError("DoT port must be in range 1..65535")
        if not _is_ip_address(normalized_host) and not normalized_bootstrap:
            raise ValueError("DoT zones with hostname endpoints require a bootstrap IP")
        return
    doh_host = _extract_doh_hostname(endpoint_url)
    if not _is_ip_address(doh_host) and not normalized_bootstrap:
        raise ValueError("DoH zones with hostname endpoints require a bootstrap IP")


async def _ensure_protocol_slot_available(db: AsyncSession, protocol: str, *, current_zone: str | None = None) -> None:
    if protocol not in {"dot", "doh"}:
        return
    result = await db.execute(select(DnsUpstream).where(DnsUpstream.protocol == protocol))
    for item in result.scalars().all():
        if item.is_builtin:
            continue
        if current_zone is not None and item.zone == current_zone:
            continue
        raise HTTPException(status_code=409, detail=f"Only one {protocol.upper()} zone can exist")


class DnsZoneUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = "plain"
    servers: list[str] = Field(default_factory=list, max_length=3)
    endpoint_host: str = ""
    endpoint_port: int | None = None
    endpoint_url: str = ""
    bootstrap_address: str = ""
    description: str = ""

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_PROTOCOLS:
            raise ValueError(f"Unsupported DNS protocol: {value}")
        return normalized

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, value: list[str]) -> list[str]:
        return _normalize_dns_servers(value) if value else []

    @model_validator(mode="after")
    def validate_fields(self) -> "DnsZoneUpdate":
        _validate_zone_payload(
            protocol=self.protocol,
            servers=self.servers,
            endpoint_host=self.endpoint_host,
            endpoint_port=self.endpoint_port,
            endpoint_url=self.endpoint_url,
            bootstrap_address=self.bootstrap_address,
        )
        if self.protocol == "dot" and self.endpoint_port is None:
            self.endpoint_port = 853
        return self


class DnsZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = "plain"
    servers: list[str] = Field(default_factory=list, max_length=3)
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

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, value: list[str]) -> list[str]:
        return _normalize_dns_servers(value) if value else []

    @model_validator(mode="after")
    def validate_fields(self) -> "DnsZoneCreate":
        _validate_zone_payload(
            protocol=self.protocol,
            servers=self.servers,
            endpoint_host=self.endpoint_host,
            endpoint_port=self.endpoint_port,
            endpoint_url=self.endpoint_url,
            bootstrap_address=self.bootstrap_address,
        )
        if self.protocol == "dot" and self.endpoint_port is None:
            self.endpoint_port = 853
        return self


class DnsDomainCreate(BaseModel):
    domain: str
    zone: str = "local"
    enabled: bool = True


class DnsDomainBulkCreate(BaseModel):
    domains: list[str] = Field(min_length=1)
    zone: str = "local"
    enabled: bool = True


class DnsManualAddressCreate(BaseModel):
    domain: str
    address: str
    enabled: bool = True


class DnsManualAddressUpdate(BaseModel):
    domain: str | None = None
    address: str | None = None
    enabled: bool | None = None


async def _dns_payload(db: AsyncSession) -> dict:
    upstreams = (
        await db.execute(
            select(DnsUpstream).order_by(
                case((DnsUpstream.zone == "local", 0), (DnsUpstream.zone == "vpn", 1), else_=2),
                DnsUpstream.name,
                DnsUpstream.zone,
            )
        )
    ).scalars().all()
    rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    manual_addresses = (await db.execute(select(DnsManualAddress).order_by(DnsManualAddress.domain))).scalars().all()
    policy = await db.get(RoutingPolicy, 1)
    gateway_settings = await db.get(GatewaySettings, 1)
    return {
        "upstreams": [
            {
                "zone": item.zone,
                "name": item.name or item.zone,
                "servers": item.servers,
                "description": item.description,
                "is_builtin": item.is_builtin,
                "protocol": item.protocol,
                "endpoint_host": item.endpoint_host,
                "endpoint_port": item.endpoint_port,
                "endpoint_url": item.endpoint_url,
                "bootstrap_address": item.bootstrap_address,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in upstreams
        ],
        "domains": [
            {
                "id": item.id,
                "domain": item.domain,
                "zone": item.zone,
                "enabled": item.enabled,
            }
            for item in rules
        ],
        "manual_addresses": [
            {
                "id": item.id,
                "domain": item.domain,
                "address": item.address,
                "enabled": item.enabled,
            }
            for item in manual_addresses
        ],
        **dns_status(),
        "preview": build_dnsmasq_preview(
            upstreams,
            rules,
            manual_addresses=manual_addresses,
            fqdn_prefixes=effective_fqdn_prefixes(policy, gateway_settings),
            ipset_name=fqdn_ipset_name(policy) if policy else "routing_prefixes_fqdn",
            use_nftset=firewall_backend(gateway_settings) == "nftables",
            nft_table_name=NFT_TABLE_NAME,
        ),
    }


async def _get_zone_or_404(db: AsyncSession, zone_key: str) -> DnsUpstream:
    item = await db.scalar(select(DnsUpstream).where(DnsUpstream.zone == zone_key))
    if item is None:
        raise HTTPException(status_code=404, detail="DNS zone not found")
    return item


async def _ensure_zone_exists(db: AsyncSession, zone_key: str) -> None:
    if await db.scalar(select(DnsUpstream.id).where(DnsUpstream.zone == zone_key)) is None:
        raise HTTPException(status_code=400, detail=f"Unknown zone: {zone_key}")


@router.get("")
async def get_dns_state(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    return await _dns_payload(db)


@router.post("/reload")
async def reload_dns(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    await restart_dnsmasq(db)
    return await _dns_payload(db)


@router.post("/zones", status_code=201)
async def create_zone(
    payload: DnsZoneCreate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    await _ensure_protocol_slot_available(db, payload.protocol)
    zone_base = _normalize_zone_key(payload.name)
    zone_key = zone_base
    suffix = 2
    while await db.scalar(select(DnsUpstream.id).where(DnsUpstream.zone == zone_key)) is not None:
        zone_key = f"{zone_base[:56]}-{suffix}"
        suffix += 1

    item = DnsUpstream(
        zone=zone_key,
        name=payload.name.strip(),
        servers=payload.servers,
        description="",
        is_builtin=False,
        protocol=payload.protocol,
        endpoint_host=payload.endpoint_host.strip().rstrip(".").lower() if payload.protocol == "dot" else "",
        endpoint_port=payload.endpoint_port if payload.protocol == "dot" else None,
        endpoint_url=payload.endpoint_url.strip() if payload.protocol == "doh" else "",
        bootstrap_address=_normalize_bootstrap_address(payload.bootstrap_address) if payload.protocol in {"dot", "doh"} else "",
    )
    db.add(item)
    await db.flush()

    normalized_domains = _normalize_domains(payload.domains)
    existing_domains = (
        await db.execute(select(DnsDomainRule.domain).where(DnsDomainRule.domain.in_(normalized_domains)))
    ).scalars().all()
    if existing_domains:
        raise HTTPException(status_code=409, detail=f"Domains already exist: {', '.join(sorted(existing_domains))}")

    for domain in normalized_domains:
        db.add(DnsDomainRule(domain=domain, zone=zone_key, enabled=True))

    await restart_dnsmasq(db)
    return {"status": "created", "zone": zone_key}


@router.put("/zones/{zone}")
async def update_zone(
    zone: str,
    payload: DnsZoneUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await _get_zone_or_404(db, zone)
    if item.is_builtin and payload.protocol != "plain":
        raise HTTPException(status_code=400, detail="Built-in DNS zones must stay plain")
    await _ensure_protocol_slot_available(db, payload.protocol, current_zone=item.zone)
    item.name = payload.name.strip()
    item.servers = payload.servers
    item.description = payload.description
    item.protocol = payload.protocol
    item.endpoint_host = payload.endpoint_host.strip().rstrip(".").lower() if payload.protocol == "dot" else ""
    item.endpoint_port = payload.endpoint_port if payload.protocol == "dot" else None
    item.endpoint_url = payload.endpoint_url.strip() if payload.protocol == "doh" else ""
    item.bootstrap_address = _normalize_bootstrap_address(payload.bootstrap_address) if payload.protocol in {"dot", "doh"} else ""
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "updated"}


@router.delete("/zones/{zone}")
async def delete_zone(
    zone: str,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await _get_zone_or_404(db, zone)
    if item.is_builtin or item.zone in {"local", "vpn"}:
        raise HTTPException(status_code=400, detail="Built-in DNS zones cannot be deleted")

    rules = (await db.execute(select(DnsDomainRule).where(DnsDomainRule.zone == zone))).scalars().all()
    for rule in rules:
        await db.delete(rule)
    await db.delete(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "deleted", "domains_removed": len(rules)}


@router.post("/domains", status_code=201)
async def create_domain(
    payload: DnsDomainCreate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    await _ensure_zone_exists(db, payload.zone)
    domain = _normalize_domain(payload.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Domain is required")
    if await db.scalar(select(DnsDomainRule.id).where(DnsDomainRule.domain == domain)) is not None:
        raise HTTPException(status_code=409, detail="Domain already exists")
    item = DnsDomainRule(domain=domain, zone=payload.zone, enabled=payload.enabled)
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"id": item.id}


@router.post("/manual-addresses", status_code=201)
async def create_manual_address(
    payload: DnsManualAddressCreate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    domain = _normalize_domain(payload.domain)
    if not domain:
        raise HTTPException(status_code=400, detail="Domain is required")
    if await db.scalar(select(DnsManualAddress.id).where(DnsManualAddress.domain == domain)) is not None:
        raise HTTPException(status_code=409, detail="Manual replace address already exists")
    item = DnsManualAddress(domain=domain, address=_normalize_ip_address(payload.address), enabled=payload.enabled)
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"id": item.id}


@router.post("/domains/bulk", status_code=201)
async def create_domains_bulk(
    payload: DnsDomainBulkCreate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    await _ensure_zone_exists(db, payload.zone)
    normalized = _normalize_domains(payload.domains)
    if not normalized:
        raise HTTPException(status_code=400, detail="At least one domain is required")

    existing = (
        await db.execute(select(DnsDomainRule.domain).where(DnsDomainRule.domain.in_(normalized)))
    ).scalars().all()
    existing_set = set(existing)
    if existing_set:
        raise HTTPException(status_code=409, detail=f"Domains already exist: {', '.join(sorted(existing_set))}")

    created_ids: list[int] = []
    for domain in normalized:
        item = DnsDomainRule(domain=domain, zone=payload.zone, enabled=payload.enabled)
        db.add(item)
        await db.flush()
        created_ids.append(item.id)

    await restart_dnsmasq(db)
    return {"status": "added", "created": len(created_ids), "ids": created_ids}


@router.post("/domains/{rule_id}/toggle")
async def toggle_domain(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.get(DnsDomainRule, rule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="DNS rule not found")
    item.enabled = not item.enabled
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "updated", "enabled": item.enabled}


@router.post("/manual-addresses/{item_id}/toggle")
async def toggle_manual_address(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.get(DnsManualAddress, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual replace address not found")
    item.enabled = not item.enabled
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "updated", "enabled": item.enabled}


@router.delete("/domains/{rule_id}")
async def delete_domain(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.get(DnsDomainRule, rule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="DNS rule not found")
    await db.delete(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "deleted"}


@router.put("/manual-addresses/{item_id}")
async def update_manual_address(
    item_id: int,
    payload: DnsManualAddressUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.get(DnsManualAddress, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual replace address not found")
    if payload.domain is not None:
        domain = _normalize_domain(payload.domain)
        if not domain:
            raise HTTPException(status_code=400, detail="Domain is required")
        existing = await db.scalar(
            select(DnsManualAddress.id).where(DnsManualAddress.domain == domain, DnsManualAddress.id != item_id)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="Manual replace address already exists")
        item.domain = domain
    if payload.address is not None:
        item.address = _normalize_ip_address(payload.address)
    if payload.enabled is not None:
        item.enabled = payload.enabled
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "updated"}


@router.delete("/manual-addresses/{item_id}")
async def delete_manual_address(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.get(DnsManualAddress, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Manual replace address not found")
    await db.delete(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "deleted"}
