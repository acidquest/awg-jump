from __future__ import annotations

import logging
import re
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EntryNode, GatewaySettings
from app.services.runtime import resolve_live_tunnel_status, resolve_tunnel_probe_target
from app.services.runtime_state import get_tunnel_runtime_state


logger = logging.getLogger(__name__)

CLIENT_CODE_AWG_GATEWAY = 1001
STATUS_REPORT_INTERVAL_SECONDS = 600
STATUS_REPORT_POLL_SECONDS = 10
STATUS_REPORT_TIMEOUT_SECONDS = 5
DEFAULT_STATUS_API_PORT = 8080
_STATUS_URL_RE = re.compile(r"^\s*#\s*awg-jump-status-url\s*=\s*(\S+)\s*$", re.IGNORECASE)

_last_report_at: float | None = None
_last_report_key: tuple[int, int | None] | None = None
_last_success_url: str | None = None


def reset_status_report_state() -> None:
    global _last_report_at, _last_report_key, _last_success_url
    _last_report_at = None
    _last_report_key = None
    _last_success_url = None


def _extract_status_urls(node: EntryNode) -> list[str]:
    urls: list[str] = []
    raw_conf = node.raw_conf or ""
    for line in raw_conf.splitlines():
        match = _STATUS_URL_RE.match(line)
        if match:
            urls.append(match.group(1))
    if urls:
        return urls

    target = resolve_tunnel_probe_target(node)
    if not target:
        return []
    return [
        f"https://{target}:{DEFAULT_STATUS_API_PORT}/api/peers/status",
        f"http://{target}:{DEFAULT_STATUS_API_PORT}/api/peers/status",
    ]


async def _post_status(url: str) -> bool:
    try:
        async with httpx.AsyncClient(
            timeout=STATUS_REPORT_TIMEOUT_SECONDS,
            verify=False,
            follow_redirects=True,
        ) as client:
            response = await client.post(url, json={"client_code": CLIENT_CODE_AWG_GATEWAY})
    except httpx.HTTPError as exc:
        logger.debug("[gateway-status] report failed url=%s error=%s", url, exc)
        return False

    if response.status_code == 200:
        logger.info("[gateway-status] status accepted by awg-jump url=%s", url)
        return True

    logger.debug(
        "[gateway-status] status rejected url=%s code=%s body=%s",
        url,
        response.status_code,
        response.text[:200],
    )
    return False


async def maybe_report_gateway_status(session: AsyncSession) -> None:
    global _last_report_at, _last_report_key, _last_success_url

    settings_row = await session.get(GatewaySettings, 1)
    if settings_row is None or not settings_row.gateway_enabled or settings_row.active_entry_node_id is None:
        reset_status_report_state()
        return

    live_status, _live_error = resolve_live_tunnel_status(settings_row)
    if live_status != "running":
        reset_status_report_state()
        return

    node = await session.get(EntryNode, settings_row.active_entry_node_id)
    if node is None:
        reset_status_report_state()
        return

    connected_at_epoch = get_tunnel_runtime_state().connected_at_epoch
    report_key = (node.id, connected_at_epoch)
    now = time.time()
    should_report = (
        report_key != _last_report_key
        or _last_report_at is None
        or now - _last_report_at >= STATUS_REPORT_INTERVAL_SECONDS
    )
    if not should_report:
        return

    urls = _extract_status_urls(node)
    if not urls:
        logger.debug("[gateway-status] no status endpoint candidates for node=%s", node.name)
        return

    if _last_success_url and _last_success_url in urls:
        urls = [_last_success_url] + [url for url in urls if url != _last_success_url]

    for url in urls:
        if await _post_status(url):
            _last_report_at = now
            _last_report_key = report_key
            _last_success_url = url
            return
