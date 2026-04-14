from __future__ import annotations
import ipaddress
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.models import RoutingPolicy


logger = logging.getLogger(__name__)


def cache_path(country_code: str) -> Path:
    return Path(settings.geoip_cache_dir) / f"{country_code.lower()}.txt"


def parse_prefixes(payload: str) -> list[str]:
    prefixes: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        prefixes.append(line)
    return prefixes


async def fetch_country(country_code: str) -> list[str]:
    url = f"{settings.geoip_source.rstrip('/')}/{country_code.lower()}.zone"
    async with httpx.AsyncClient(timeout=settings.geoip_fetch_timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    payload = response.text
    prefixes = parse_prefixes(payload)
    cache_path(country_code).parent.mkdir(parents=True, exist_ok=True)
    cache_path(country_code).write_text(payload, encoding="utf-8")
    return prefixes


def load_cached_country(country_code: str) -> list[str]:
    path = cache_path(country_code)
    if not path.exists():
        return []
    return parse_prefixes(path.read_text(encoding="utf-8"))


async def refresh_policy_geoip(policy: RoutingPolicy) -> dict:
    countries = [country.lower() for country in policy.geoip_countries]
    fetched: dict[str, int] = {}
    merged: set[str] = set()
    for country in countries:
        try:
            prefixes = await fetch_country(country)
        except httpx.HTTPError as exc:
            prefixes = load_cached_country(country)
            if not prefixes:
                raise
            logger.warning(
                "GeoIP refresh failed for %s, using cached prefixes: %s",
                country,
                exc,
            )
        merged.update(prefixes)
        fetched[country] = len(prefixes)

    for prefix in policy.manual_prefixes:
        merged.add(prefix)

    return {
        "countries": fetched,
        "prefix_count": len(merged),
        "manual_prefixes": sorted(policy.manual_prefixes),
        "ipset_name": policy.geoip_ipset_name,
    }
