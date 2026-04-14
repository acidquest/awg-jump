from __future__ import annotations

import ipaddress
import logging

from app.models import GatewaySettings, TrafficSourceMode


logger = logging.getLogger(__name__)

_LOCALHOST_CIDR = "127.0.0.0/8"


def default_allowed_source_cidrs() -> list[str]:
    return [_LOCALHOST_CIDR]


def normalize_source_entry(value: str) -> list[str]:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Traffic source is required")

    try:
        if "/" in candidate:
            return [str(ipaddress.ip_network(candidate, strict=False))]
        return [str(ipaddress.ip_network(f"{candidate}/32", strict=False))]
    except ValueError as exc:
        raise ValueError(f"Invalid IPv4 or CIDR: {candidate}") from exc


def normalize_allowed_source_cidrs(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in values:
        for cidr in normalize_source_entry(raw):
            if cidr in seen:
                continue
            normalized.append(cidr)
            seen.add(cidr)

    return normalized


def localhost_source_cidr() -> str:
    return _LOCALHOST_CIDR


def migrate_legacy_source_settings(settings_row: GatewaySettings) -> bool:
    raw_values = list(getattr(settings_row, "allowed_client_cidrs", []) or [])
    raw_values.extend(getattr(settings_row, "allowed_client_hosts", []) or [])

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        try:
            for cidr in normalize_source_entry(raw):
                if cidr == "127.0.0.1/32":
                    cidr = _LOCALHOST_CIDR
                if cidr in seen:
                    continue
                normalized.append(cidr)
                seen.add(cidr)
        except ValueError as exc:
            logger.warning("[traffic-sources] skipped legacy source %r: %s", raw, exc)

    if not normalized and getattr(settings_row, "traffic_source_mode", None) != TrafficSourceMode.cidr_list.value:
        normalized = default_allowed_source_cidrs()

    changed = False
    if list(getattr(settings_row, "allowed_client_cidrs", []) or []) != normalized:
        settings_row.allowed_client_cidrs = normalized
        changed = True
    if list(getattr(settings_row, "allowed_client_hosts", []) or []):
        settings_row.allowed_client_hosts = []
        changed = True
    if getattr(settings_row, "traffic_source_mode", None) != TrafficSourceMode.cidr_list.value:
        settings_row.traffic_source_mode = TrafficSourceMode.cidr_list.value
        changed = True
    return changed


def source_selectors(settings_row: GatewaySettings) -> list[str]:
    return list(getattr(settings_row, "allowed_client_cidrs", []) or [])


def localhost_selector_enabled(settings_row: GatewaySettings) -> bool:
    return _LOCALHOST_CIDR in source_selectors(settings_row)


def non_localhost_selectors(settings_row: GatewaySettings) -> list[str]:
    return [selector for selector in source_selectors(settings_row) if selector != _LOCALHOST_CIDR]
