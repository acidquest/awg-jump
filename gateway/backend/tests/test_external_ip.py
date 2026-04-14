from types import SimpleNamespace

import pytest

from app.services.external_ip import effective_fqdn_prefixes, external_ip_route_hosts, validate_service_pair


def make_settings(local_url: str = "https://ipinfo.io/ip", vpn_url: str = "https://ifconfig.me/ip"):
    return SimpleNamespace(
        external_ip_local_service_url=local_url,
        external_ip_vpn_service_url=vpn_url,
    )


def make_policy(prefixes_route_local: bool = True, fqdn_prefixes_enabled: bool = False, fqdn_prefixes: list[str] | None = None):
    return SimpleNamespace(
        prefixes_route_local=prefixes_route_local,
        fqdn_prefixes_enabled=fqdn_prefixes_enabled,
        fqdn_prefixes=fqdn_prefixes or [],
    )


def test_external_ip_route_hosts_follow_current_prefix_direction() -> None:
    settings = make_settings()
    assert external_ip_route_hosts(settings, make_policy(prefixes_route_local=True)) == ["ipinfo.io"]
    assert external_ip_route_hosts(settings, make_policy(prefixes_route_local=False)) == ["ifconfig.me"]


def test_effective_fqdn_prefixes_include_forced_service_host_even_when_user_block_disabled() -> None:
    prefixes = effective_fqdn_prefixes(
        make_policy(prefixes_route_local=True, fqdn_prefixes_enabled=False),
        make_settings(),
    )
    assert prefixes == ["ipinfo.io"]


def test_effective_fqdn_prefixes_merge_user_domains_with_forced_host() -> None:
    prefixes = effective_fqdn_prefixes(
        make_policy(prefixes_route_local=False, fqdn_prefixes_enabled=True, fqdn_prefixes=["example.com"]),
        make_settings(),
    )
    assert prefixes == ["example.com", "ifconfig.me"]


def test_validate_service_pair_rejects_same_hostname() -> None:
    with pytest.raises(ValueError, match="different hostnames"):
        validate_service_pair("https://ipinfo.io/ip", "https://ipinfo.io/json")
