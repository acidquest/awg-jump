from __future__ import annotations

from datetime import datetime, timezone

import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, EntryNode, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.dns_runtime import restart_dnsmasq
from app.services.geoip import refresh_policy_geoip
from app.services.routing import apply_routing_plan, build_prefix_summary, build_routing_plan, sync_prefix_ipset


router = APIRouter(prefix="/api/routing", tags=["routing"])


class RoutingPolicyUpdate(BaseModel):
    countries_enabled: bool
    geoip_countries: list[str]
    manual_prefixes_enabled: bool
    manual_prefixes: list[str] = []
    fqdn_prefixes_enabled: bool
    fqdn_prefixes: list[str] = []
    prefixes_route_local: bool
    kill_switch_enabled: bool
    strict_mode: bool


class CountryPayload(BaseModel):
    country_code: str


class PrefixPayload(BaseModel):
    prefix: str


class PrefixBulkPayload(BaseModel):
    prefixes: list[str]


class FqdnPayload(BaseModel):
    fqdn: str


class FqdnBulkPayload(BaseModel):
    fqdn_list: list[str]


def _normalize_prefix(prefix: str) -> str:
    value = prefix.strip()
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        return str(ipaddress.ip_network(f"{value}/32", strict=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP/CIDR prefix: {prefix}") from exc


def _normalize_fqdn(value: str) -> str:
    domain = value.strip().lower().strip(".")
    if not domain:
        raise HTTPException(status_code=400, detail="FQDN is required")
    labels = domain.split(".")
    if any(not label or len(label) > 63 for label in labels):
        raise HTTPException(status_code=400, detail=f"Invalid FQDN: {value}")
    allowed_chars = set("abcdefghijklmnopqrstuvwxyz0123456789-.")
    if any(char not in allowed_chars for char in domain):
        raise HTTPException(status_code=400, detail=f"Invalid FQDN: {value}")
    if ".." in domain or domain.startswith("-") or domain.endswith("-"):
        raise HTTPException(status_code=400, detail=f"Invalid FQDN: {value}")
    return domain


async def _reload_runtime(
    db: AsyncSession,
    policy: RoutingPolicy,
    *,
    refresh_geoip: bool = False,
    restart_dns: bool = False,
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    if refresh_geoip and policy.countries_enabled:
        await refresh_policy_geoip(policy)

    prefixes = sync_prefix_ipset(policy, settings_row, flush_fqdn=restart_dns)

    if restart_dns:
        await restart_dnsmasq(db)

    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    status = "synced"
    plan = build_routing_plan(settings_row, policy, active_node)
    if plan["safe_to_apply"]:
        try:
            plan = apply_routing_plan(settings_row, policy, active_node)
            policy.last_error = None
            status = "applied"
        except RuntimeError as exc:
            policy.last_error = str(exc)
            status = "error"
    db.add(policy)
    await db.flush()
    return {
        "status": status,
        "prefixes": prefixes,
        "plan": plan,
        "prefix_summary": build_prefix_summary(policy, settings_row),
    }


@router.get("")
async def get_policy(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    settings_row = await db.get(GatewaySettings, 1)
    return {
        "geoip_enabled": policy.geoip_enabled,
        "countries_enabled": policy.countries_enabled,
        "geoip_countries": policy.geoip_countries,
        "manual_prefixes_enabled": policy.manual_prefixes_enabled,
        "manual_prefixes": policy.manual_prefixes,
        "fqdn_prefixes_enabled": policy.fqdn_prefixes_enabled,
        "fqdn_prefixes": policy.fqdn_prefixes,
        "geoip_ipset_name": policy.geoip_ipset_name,
        "prefixes_route_local": policy.prefixes_route_local,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "strict_mode": policy.strict_mode,
        "prefix_summary": build_prefix_summary(policy, settings_row),
        "last_applied_at": policy.last_applied_at.isoformat() if policy.last_applied_at else None,
        "last_error": policy.last_error,
    }


@router.put("")
async def update_policy(
    payload: RoutingPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    prev_geoip_countries = sorted(policy.geoip_countries)
    next_geoip_countries = sorted(item.lower() for item in payload.geoip_countries)
    policy.geoip_enabled = payload.countries_enabled
    policy.countries_enabled = payload.countries_enabled
    policy.geoip_countries = next_geoip_countries
    policy.manual_prefixes_enabled = payload.manual_prefixes_enabled
    policy.manual_prefixes = [_normalize_prefix(item) for item in payload.manual_prefixes]
    policy.fqdn_prefixes_enabled = payload.fqdn_prefixes_enabled
    policy.fqdn_prefixes = sorted({_normalize_fqdn(item) for item in payload.fqdn_prefixes})
    policy.prefixes_route_local = payload.prefixes_route_local
    policy.kill_switch_enabled = payload.kill_switch_enabled
    policy.strict_mode = payload.strict_mode
    policy.geoip_ipset_name = "routing_prefixes"
    db.add(policy)
    await db.flush()
    return await _reload_runtime(
        db,
        policy,
        refresh_geoip=policy.countries_enabled and prev_geoip_countries != next_geoip_countries,
        restart_dns=True,
    )


@router.post("/countries")
async def add_country(
    payload: CountryPayload,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    country = payload.country_code.strip().lower()
    if not country:
        raise HTTPException(status_code=400, detail="country_code is required")
    if country not in policy.geoip_countries:
        policy.geoip_countries = sorted([*policy.geoip_countries, country])
        db.add(policy)
        await db.flush()
    runtime = await _reload_runtime(db, policy, refresh_geoip=policy.countries_enabled)
    return {"status": "added", "geoip_countries": policy.geoip_countries, **runtime}


@router.delete("/countries/{country_code}")
async def delete_country(
    country_code: str,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    policy.geoip_countries = [item for item in policy.geoip_countries if item != country_code.lower()]
    db.add(policy)
    await db.flush()
    runtime = await _reload_runtime(db, policy)
    return {"status": "deleted", "geoip_countries": policy.geoip_countries, **runtime}


@router.post("/manual-prefixes")
async def add_manual_prefix(
    payload: PrefixPayload,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    prefix = _normalize_prefix(payload.prefix)
    if prefix not in policy.manual_prefixes:
        policy.manual_prefixes = sorted([*policy.manual_prefixes, prefix])
        db.add(policy)
        await db.flush()
    runtime = await _reload_runtime(db, policy)
    return {"status": "added", "manual_prefixes": policy.manual_prefixes, **runtime}


@router.post("/manual-prefixes/bulk")
async def add_manual_prefixes_bulk(
    payload: PrefixBulkPayload,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    normalized = {_normalize_prefix(item) for item in payload.prefixes}
    policy.manual_prefixes = sorted({*policy.manual_prefixes, *normalized})
    db.add(policy)
    await db.flush()
    runtime = await _reload_runtime(db, policy)
    return {"status": "added", "manual_prefixes": policy.manual_prefixes, **runtime}


@router.delete("/manual-prefixes/{prefix:path}")
async def delete_manual_prefix(
    prefix: str,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    normalized = _normalize_prefix(prefix)
    policy = await db.get(RoutingPolicy, 1)
    policy.manual_prefixes = [item for item in policy.manual_prefixes if item != normalized]
    db.add(policy)
    await db.flush()
    runtime = await _reload_runtime(db, policy)
    return {"status": "deleted", "manual_prefixes": policy.manual_prefixes, **runtime}


@router.post("/fqdn-prefixes")
async def add_fqdn_prefix(
    payload: FqdnPayload,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    fqdn = _normalize_fqdn(payload.fqdn)
    if fqdn not in policy.fqdn_prefixes:
        policy.fqdn_prefixes = sorted([*policy.fqdn_prefixes, fqdn])
        db.add(policy)
        await db.flush()
    runtime = await _reload_runtime(db, policy, restart_dns=True)
    return {"status": "added", "fqdn_prefixes": policy.fqdn_prefixes, **runtime}


@router.post("/fqdn-prefixes/bulk")
async def add_fqdn_prefixes_bulk(
    payload: FqdnBulkPayload,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    normalized = {_normalize_fqdn(item) for item in payload.fqdn_list}
    policy.fqdn_prefixes = sorted({*policy.fqdn_prefixes, *normalized})
    db.add(policy)
    await db.flush()
    runtime = await _reload_runtime(db, policy, restart_dns=True)
    return {"status": "added", "fqdn_prefixes": policy.fqdn_prefixes, **runtime}


@router.delete("/fqdn-prefixes/{fqdn:path}")
async def delete_fqdn_prefix(
    fqdn: str,
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    normalized = _normalize_fqdn(fqdn)
    policy = await db.get(RoutingPolicy, 1)
    policy.fqdn_prefixes = [item for item in policy.fqdn_prefixes if item != normalized]
    db.add(policy)
    await db.flush()
    runtime = await _reload_runtime(db, policy, restart_dns=True)
    return {"status": "deleted", "fqdn_prefixes": policy.fqdn_prefixes, **runtime}


@router.post("/refresh-geoip")
async def refresh_geoip(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    result = await refresh_policy_geoip(policy)
    sync_prefix_ipset(policy, await db.get(GatewaySettings, 1))
    return result


@router.get("/plan")
async def get_plan(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    policy = await db.get(RoutingPolicy, 1)
    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    return build_routing_plan(settings_row, policy, active_node)


@router.post("/apply")
async def apply_plan(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    settings_row = await db.get(GatewaySettings, 1)
    policy = await db.get(RoutingPolicy, 1)
    active_node = await db.get(EntryNode, settings_row.active_entry_node_id) if settings_row.active_entry_node_id else None
    plan = build_routing_plan(settings_row, policy, active_node)
    policy.last_applied_at = datetime.now(timezone.utc)
    if not plan["safe_to_apply"]:
        policy.last_error = "Routing plan is not safe to apply"
        db.add(policy)
        await db.flush()
        return {"status": "blocked", "plan": plan}
    try:
        plan = apply_routing_plan(settings_row, policy, active_node)
        policy.last_error = None
    except RuntimeError as exc:
        policy.last_error = str(exc)
        db.add(policy)
        await db.flush()
        return {"status": "error", "error": policy.last_error, "plan": plan}
    db.add(policy)
    await db.flush()
    return {"status": "applied", "plan": plan}
