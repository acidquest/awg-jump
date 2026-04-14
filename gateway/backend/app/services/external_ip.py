from __future__ import annotations

import ipaddress
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GatewaySettings, RoutingPolicy
from app.services.runtime import resolve_live_tunnel_status


logger = logging.getLogger(__name__)

EXTERNAL_IP_REFRESH_INTERVAL_SECONDS = 600
_DEFAULT_SCHEME = "https://"


def normalize_service_url(value: str | None) -> str:
    raw_value = (value or "").strip()
    if not raw_value:
        return ""
    candidate = raw_value if "://" in raw_value else f"{_DEFAULT_SCHEME}{raw_value}"
    parsed = urlsplit(candidate)
    if not parsed.hostname:
        raise ValueError("Service URL must include a hostname")
    return parsed.geturl()


def extract_service_host(value: str | None) -> str | None:
    normalized = normalize_service_url(value)
    if not normalized:
        return None
    return urlsplit(normalized).hostname


def external_ip_route_hosts(
    gateway_settings: GatewaySettings | None,
    policy: RoutingPolicy | None,
) -> list[str]:
    if gateway_settings is None or policy is None:
        return []
    local_host = extract_service_host(getattr(gateway_settings, "external_ip_local_service_url", ""))
    vpn_host = extract_service_host(getattr(gateway_settings, "external_ip_vpn_service_url", ""))
    selected = local_host if getattr(policy, "prefixes_route_local", True) else vpn_host
    return [selected] if selected else []


def effective_fqdn_prefixes(
    policy: RoutingPolicy | None,
    gateway_settings: GatewaySettings | None,
) -> list[str]:
    configured = []
    if policy is not None and getattr(policy, "fqdn_prefixes_enabled", False):
        configured.extend(getattr(policy, "fqdn_prefixes", []) or [])
    configured.extend(external_ip_route_hosts(gateway_settings, policy))
    return list(dict.fromkeys(item.strip().lower().strip(".") for item in configured if item and item.strip()))


def serialize_external_ip_info(
    gateway_settings: GatewaySettings | None,
    policy: RoutingPolicy | None = None,
) -> dict:
    forced_domains = external_ip_route_hosts(gateway_settings, policy)
    return {
        "refresh_interval_seconds": EXTERNAL_IP_REFRESH_INTERVAL_SECONDS,
        "forced_domains": forced_domains,
        "local": {
            "service_url": getattr(gateway_settings, "external_ip_local_service_url", ""),
            "service_host": extract_service_host(getattr(gateway_settings, "external_ip_local_service_url", "")),
            "value": getattr(gateway_settings, "external_ip_local_value", None),
            "error": getattr(gateway_settings, "external_ip_local_error", None),
            "checked_at": (
                gateway_settings.external_ip_local_checked_at.isoformat()
                if gateway_settings and gateway_settings.external_ip_local_checked_at
                else None
            ),
            "route_target": "local",
        },
        "vpn": {
            "service_url": getattr(gateway_settings, "external_ip_vpn_service_url", ""),
            "service_host": extract_service_host(getattr(gateway_settings, "external_ip_vpn_service_url", "")),
            "value": getattr(gateway_settings, "external_ip_vpn_value", None),
            "error": getattr(gateway_settings, "external_ip_vpn_error", None),
            "checked_at": (
                gateway_settings.external_ip_vpn_checked_at.isoformat()
                if gateway_settings and gateway_settings.external_ip_vpn_checked_at
                else None
            ),
            "route_target": "vpn",
        },
    }


def refresh_due(gateway_settings: GatewaySettings | None, *, force: bool = False) -> bool:
    if force or gateway_settings is None:
        return True
    timestamps = [
        ts
        for ts in [
            gateway_settings.external_ip_local_checked_at,
            gateway_settings.external_ip_vpn_checked_at,
        ]
        if ts is not None
    ]
    if not timestamps:
        return True
    last_checked = max(timestamps)
    return datetime.now(timezone.utc) - last_checked >= timedelta(seconds=EXTERNAL_IP_REFRESH_INTERVAL_SECONDS)


def validate_service_pair(local_url: str | None, vpn_url: str | None) -> tuple[str, str]:
    normalized_local = normalize_service_url(local_url)
    normalized_vpn = normalize_service_url(vpn_url)
    local_host = extract_service_host(normalized_local)
    vpn_host = extract_service_host(normalized_vpn)
    if not local_host or not vpn_host:
        raise ValueError("Both external IP services must be configured")
    if local_host == vpn_host:
        raise ValueError("External IP services must use different hostnames")
    return normalized_local, normalized_vpn


def _curl_external_ip(service_url: str) -> str:
    result = subprocess.run(
        [
            "curl",
            "-4",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--max-time",
            "10",
            service_url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"curl exited with code {result.returncode}")
    value = output.splitlines()[0].strip() if output else ""
    if not value:
        raise RuntimeError("Empty response")
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise RuntimeError(f"Unexpected response: {value}") from exc
    return value


def _store_probe_result(
    gateway_settings: GatewaySettings,
    *,
    target: str,
    value: str | None = None,
    error: str | None = None,
) -> None:
    checked_at = datetime.now(timezone.utc)
    if target == "local":
        gateway_settings.external_ip_local_value = value
        gateway_settings.external_ip_local_error = error
        gateway_settings.external_ip_local_checked_at = checked_at
        return
    gateway_settings.external_ip_vpn_value = value
    gateway_settings.external_ip_vpn_error = error
    gateway_settings.external_ip_vpn_checked_at = checked_at


async def refresh_external_ip_info(
    db: AsyncSession,
    gateway_settings: GatewaySettings | None = None,
    policy: RoutingPolicy | None = None,
    *,
    force: bool = False,
) -> dict:
    settings_row = gateway_settings or await db.get(GatewaySettings, 1)
    if settings_row is None:
        return serialize_external_ip_info(None, policy)
    routing_policy = policy or await db.get(RoutingPolicy, 1)
    if not refresh_due(settings_row, force=force):
        return serialize_external_ip_info(settings_row, routing_policy)

    live_status, live_error = resolve_live_tunnel_status(settings_row)
    settings_row.tunnel_status = live_status
    settings_row.tunnel_last_error = live_error

    local_url = normalize_service_url(settings_row.external_ip_local_service_url)
    vpn_url = normalize_service_url(settings_row.external_ip_vpn_service_url)

    try:
        local_value = _curl_external_ip(local_url)
        _store_probe_result(settings_row, target="local", value=local_value, error=None)
    except Exception as exc:
        logger.warning("[external-ip] local probe failed: %s", exc)
        _store_probe_result(settings_row, target="local", value=None, error=str(exc))

    if live_status == "running":
        try:
            vpn_value = _curl_external_ip(vpn_url)
            _store_probe_result(settings_row, target="vpn", value=vpn_value, error=None)
        except Exception as exc:
            logger.warning("[external-ip] vpn probe failed: %s", exc)
            _store_probe_result(settings_row, target="vpn", value=None, error=str(exc))
    else:
        _store_probe_result(settings_row, target="vpn", value=None, error="Tunnel is not running")

    db.add(settings_row)
    await db.flush()
    return serialize_external_ip_info(settings_row, routing_policy)
