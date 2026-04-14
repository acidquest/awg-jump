from types import SimpleNamespace

import pytest

from app.services.traffic_sources import (
    default_allowed_source_cidrs,
    migrate_legacy_source_settings,
    normalize_allowed_source_cidrs,
)


def test_normalize_allowed_source_cidrs_converts_ip_to_host_prefix() -> None:
    assert normalize_allowed_source_cidrs(["127.0.0.1", "192.168.10.0/24"]) == ["127.0.0.1/32", "192.168.10.0/24"]


def test_normalize_allowed_source_cidrs_uses_localhost_by_default() -> None:
    assert normalize_allowed_source_cidrs([]) == []
    assert default_allowed_source_cidrs() == ["127.0.0.0/8"]


def test_normalize_allowed_source_cidrs_rejects_non_ip_hosts() -> None:
    with pytest.raises(ValueError, match="Invalid IPv4 or CIDR"):
        normalize_allowed_source_cidrs(["example.com"])


def test_migrate_legacy_source_settings_merges_hosts_into_cidrs() -> None:
    settings_row = SimpleNamespace(
        allowed_client_cidrs=["192.168.10.0/24"],
        allowed_client_hosts=["192.168.10.50"],
        traffic_source_mode="selected_hosts",
    )

    changed = migrate_legacy_source_settings(settings_row)

    assert changed is True
    assert settings_row.allowed_client_cidrs == ["192.168.10.0/24", "192.168.10.50/32"]
    assert settings_row.allowed_client_hosts == []
    assert settings_row.traffic_source_mode == "cidr_list"


def test_migrate_legacy_localhost_host_prefix_to_localhost_network() -> None:
    settings_row = SimpleNamespace(
        allowed_client_cidrs=["127.0.0.1/32"],
        allowed_client_hosts=[],
        traffic_source_mode="localhost",
    )

    changed = migrate_legacy_source_settings(settings_row)

    assert changed is True
    assert settings_row.allowed_client_cidrs == ["127.0.0.0/8"]
    assert settings_row.traffic_source_mode == "cidr_list"
