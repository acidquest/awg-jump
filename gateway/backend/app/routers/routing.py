from __future__ import annotations

from datetime import datetime, timezone

import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AdminUser, EntryNode, GatewaySettings, RoutingPolicy
from app.security import get_current_user
from app.services.geoip import refresh_policy_geoip
from app.services.routing import build_routing_plan


router = APIRouter(prefix="/api/routing", tags=["routing"])


class RoutingPolicyUpdate(BaseModel):
    geoip_enabled: bool
    geoip_countries: list[str]
    manual_prefixes: list[str] = []
    invert_geoip: bool
    default_policy: str
    kill_switch_enabled: bool
    strict_mode: bool


class CountryPayload(BaseModel):
    country_code: str


class PrefixPayload(BaseModel):
    prefix: str


def _normalize_prefix(prefix: str) -> str:
    value = prefix.strip()
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        return str(ipaddress.ip_network(f"{value}/32", strict=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP/CIDR prefix: {prefix}") from exc


@router.get("")
async def get_policy(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    return {
        "geoip_enabled": policy.geoip_enabled,
        "geoip_countries": policy.geoip_countries,
        "manual_prefixes": policy.manual_prefixes,
        "geoip_ipset_name": policy.geoip_ipset_name,
        "invert_geoip": policy.invert_geoip,
        "default_policy": policy.default_policy,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "strict_mode": policy.strict_mode,
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
    policy.geoip_enabled = payload.geoip_enabled
    policy.geoip_countries = [item.lower() for item in payload.geoip_countries]
    policy.manual_prefixes = [_normalize_prefix(item) for item in payload.manual_prefixes]
    policy.invert_geoip = payload.invert_geoip
    policy.default_policy = payload.default_policy
    policy.kill_switch_enabled = payload.kill_switch_enabled
    policy.strict_mode = payload.strict_mode
    db.add(policy)
    await db.flush()
    return {"status": "updated"}


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
    return {"status": "added", "geoip_countries": policy.geoip_countries}


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
    return {"status": "deleted", "geoip_countries": policy.geoip_countries}


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
    return {"status": "added", "manual_prefixes": policy.manual_prefixes}


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
    return {"status": "deleted", "manual_prefixes": policy.manual_prefixes}


@router.post("/refresh-geoip")
async def refresh_geoip(
    db: AsyncSession = Depends(get_db),
    user: AdminUser = Depends(get_current_user),
) -> dict:
    policy = await db.get(RoutingPolicy, 1)
    result = await refresh_policy_geoip(policy)
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
    policy.last_error = None if plan["safe_to_apply"] else "Routing plan is not safe to apply"
    db.add(policy)
    await db.flush()
    return {"status": "applied" if plan["safe_to_apply"] else "blocked", "plan": plan}
