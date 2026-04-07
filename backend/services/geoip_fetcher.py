"""
GeoIP fetcher — загрузка CIDR-списков с кэшированием.
"""
import asyncio
import ipaddress
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.geoip import GeoipSource
from backend.services import ipset_manager

logger = logging.getLogger(__name__)
LOCAL_GEOIP_IPSET_NAME = "geoip_local"

# TTL кэша в секундах (23 часа)
_CACHE_TTL = 23 * 3600


def _cache_path(country_code: str) -> str:
    return os.path.join(settings.geoip_cache_dir, f"{country_code}.txt")


def build_default_url(country_code: str) -> str:
    return f"https://www.ipdeny.com/ipblocks/data/countries/{country_code}.zone"


def _is_cache_fresh(country_code: str) -> bool:
    path = _cache_path(country_code)
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < _CACHE_TTL


def _parse_prefixes(text: str) -> list[str]:
    """Парсит ipdeny формат: один CIDR на строку, # — комментарии."""
    prefixes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            # Строгая валидация: только корректные IPv4/IPv6 сети
            ipaddress.ip_network(line, strict=False)
            prefixes.append(line)
        except ValueError:
            logger.debug("Skipping invalid prefix: %r", line)
    return prefixes


def load_from_cache(country_code: str) -> list[str]:
    """Загружает префиксы из локального кэша."""
    path = _cache_path(country_code)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return _parse_prefixes(f.read())
    except OSError as e:
        logger.warning("Failed to read GeoIP cache %s: %s", path, e)
        return []


async def fetch(
    source: GeoipSource,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """
    Скачивает CIDR-список с повторными попытками (3x).
    Сохраняет в кэш. Возвращает список префиксов.
    """
    os.makedirs(settings.geoip_cache_dir, exist_ok=True)

    def _progress(msg: str) -> None:
        logger.info("[geoip] %s", msg)
        if progress_cb:
            progress_cb(msg)

    url = source.url or build_default_url(source.country_code)
    _progress(f"Fetching {source.country_code} from {url}")

    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(
                timeout=settings.geoip_fetch_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                text = response.text

            prefixes = _parse_prefixes(text)
            if not prefixes:
                raise ValueError("Empty prefix list received")

            # Сохранить в кэш
            cache_path = _cache_path(source.country_code)
            with open(cache_path, "w") as f:
                f.write(text)

            _progress(f"Downloaded {len(prefixes)} prefixes, cached to {cache_path}")
            return prefixes

        except Exception as e:
            last_error = e
            _progress(f"Attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                await asyncio.sleep(2 * attempt)

    raise RuntimeError(f"Failed to fetch GeoIP after 3 attempts: {last_error}")


async def validate_source_url(url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.head(url)
            response.raise_for_status()
    except Exception as exc:
        raise ValueError(f"GeoIP URL is not reachable: {url}") from exc


async def update_all_zones(
    db: AsyncSession,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[GeoipSource]:
    def _progress(msg: str) -> None:
        logger.info("[geoip] %s", msg)
        if progress_cb:
            progress_cb(msg)

    result = await db.execute(
        select(GeoipSource).where(GeoipSource.enabled == True).order_by(GeoipSource.id)  # noqa: E712
    )
    sources = result.scalars().all()
    if not sources:
        _progress("No enabled GeoIP sources configured, clearing geoip_local")
        await asyncio.get_running_loop().run_in_executor(
            None,
            ipset_manager.create_or_update,
            LOCAL_GEOIP_IPSET_NAME,
            [],
        )
        return []

    merged_prefixes: set[str] = set()
    fetched_sources: list[tuple[GeoipSource, list[str]]] = []
    for source in sources:
        prefixes = await fetch(source, progress_cb=_progress)
        fetched_sources.append((source, prefixes))
        merged_prefixes.update(prefixes)
        _progress(
            f"Fetched {len(prefixes)} prefixes for "
            f"{source.country_code} ({source.display_name or source.name})"
        )

    all_prefixes = sorted(merged_prefixes)
    _progress(f"Updating ipset {LOCAL_GEOIP_IPSET_NAME} with {len(all_prefixes)} unique prefixes")
    await asyncio.get_running_loop().run_in_executor(
        None,
        ipset_manager.create_or_update,
        LOCAL_GEOIP_IPSET_NAME,
        all_prefixes,
    )

    updated_at = datetime.now(timezone.utc)
    for source, prefixes in fetched_sources:
        source.last_updated = updated_at
        source.prefix_count = len(prefixes)
        source.ipset_name = LOCAL_GEOIP_IPSET_NAME
        db.add(source)
    await db.commit()
    return sources
