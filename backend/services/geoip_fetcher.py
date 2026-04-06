"""
GeoIP fetcher — загрузка CIDR-списков с кэшированием.
"""
import asyncio
import logging
import os
import time
from typing import Callable, Optional

import httpx

from backend.config import settings
from backend.models.geoip import GeoipSource

logger = logging.getLogger(__name__)

# TTL кэша в секундах (23 часа)
_CACHE_TTL = 23 * 3600


def _cache_path(country_code: str) -> str:
    return os.path.join(settings.geoip_cache_dir, f"{country_code}.txt")


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
        # Базовая валидация: должен содержать точку (IPv4)
        if "." in line or ":" in line:
            prefixes.append(line)
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

    _progress(f"Fetching {source.country_code} from {source.url}")

    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(
                timeout=settings.geoip_fetch_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(source.url)
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
