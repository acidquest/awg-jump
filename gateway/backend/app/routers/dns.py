from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, DnsDomainRule, DnsUpstream, RoutingPolicy
from app.security import get_current_user
from app.services.dns_runtime import restart_dnsmasq, status as dns_status
from app.services.dns import build_dnsmasq_preview
from app.services.routing import fqdn_ipset_name


router = APIRouter(prefix="/api/dns", tags=["dns"])


class DnsUpstreamUpdate(BaseModel):
    servers: list[str] = Field(min_length=1)
    description: str = ""


class DnsDomainCreate(BaseModel):
    domain: str
    zone: str
    enabled: bool = True


class DnsDomainBulkCreate(BaseModel):
    domains: list[str] = Field(min_length=1)
    zone: str = "local"
    enabled: bool = True


@router.get("")
async def get_dns_state(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    upstreams = (await db.execute(select(DnsUpstream).order_by(DnsUpstream.zone))).scalars().all()
    rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    policy = await db.get(RoutingPolicy, 1)
    return {
        "upstreams": [
            {"zone": item.zone, "servers": item.servers, "description": item.description}
            for item in upstreams
        ],
        "domains": [
            {"id": item.id, "domain": item.domain, "zone": item.zone, "enabled": item.enabled}
            for item in rules
        ],
        **dns_status(),
        "preview": build_dnsmasq_preview(
            upstreams,
            rules,
            fqdn_prefixes=policy.fqdn_prefixes if policy and policy.fqdn_prefixes_enabled else [],
            ipset_name=fqdn_ipset_name(policy) if policy else "routing_prefixes_fqdn",
        ),
    }


@router.put("/upstreams/{zone}")
async def update_upstream(
    zone: str,
    payload: DnsUpstreamUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = await db.scalar(select(DnsUpstream).where(DnsUpstream.zone == zone))
    if item is None:
        raise HTTPException(status_code=404, detail="DNS zone not found")
    item.servers = payload.servers
    item.description = payload.description
    db.add(item)
    await db.flush()
    await restart_dnsmasq(db)
    return {"status": "updated"}


@router.post("/domains", status_code=201)
async def create_domain(
    payload: DnsDomainCreate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    item = DnsDomainRule(domain=payload.domain.lower().strip("."), zone=payload.zone, enabled=payload.enabled)
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
    normalized = {
        item.lower().strip().strip(".")
        for item in payload.domains
        if item and item.strip().strip(".")
    }
    if not normalized:
        raise HTTPException(status_code=400, detail="At least one domain is required")

    existing = (
        await db.execute(select(DnsDomainRule.domain).where(DnsDomainRule.domain.in_(normalized)))
    ).scalars().all()
    existing_set = set(existing)

    created_ids: list[int] = []
    for domain in sorted(normalized - existing_set):
        item = DnsDomainRule(domain=domain, zone=payload.zone, enabled=payload.enabled)
        db.add(item)
        await db.flush()
        created_ids.append(item.id)

    await restart_dnsmasq(db)
    return {"status": "added", "created": len(created_ids), "ids": created_ids}


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
