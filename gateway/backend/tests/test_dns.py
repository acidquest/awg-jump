from types import SimpleNamespace

from app.services.dns import _to_dnsmasq_domain, build_dnsmasq_config


def test_dnsmasq_config_uses_single_bind_mode() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"], protocol="plain"),
            SimpleNamespace(zone="local", servers=["9.9.9.9"], protocol="plain"),
        ],
        [SimpleNamespace(domain="example.com", zone="local", enabled=True)],
        fqdn_prefixes=["api.example.com"],
        ipset_name="routing_prefixes",
    )
    assert "bind-dynamic" in config
    assert "bind-interfaces" not in config
    assert "server=/example.com/9.9.9.9" in config
    assert "ipset=/api.example.com/routing_prefixes" in config
    assert "# Special zone overrides" in config


def test_to_dnsmasq_domain_converts_idn_to_idna() -> None:
    assert _to_dnsmasq_domain("рф") == "xn--p1ai"
    assert _to_dnsmasq_domain("ЯНДЕКС.РФ") == "xn--d1acpjx3f.xn--p1ai"


def test_dnsmasq_config_converts_idn_domains_and_fqdn_prefixes() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"], protocol="plain"),
            SimpleNamespace(zone="local", servers=["9.9.9.9"], protocol="plain"),
        ],
        [SimpleNamespace(domain="яндекс.рф", zone="local", enabled=True)],
        fqdn_prefixes=["почта.яндекс.рф"],
        ipset_name="routing_prefixes",
    )

    assert "server=/xn--d1acpjx3f.xn--p1ai/9.9.9.9" in config
    assert "ipset=/xn--80a1acny.xn--d1acpjx3f.xn--p1ai/routing_prefixes" in config


def test_dnsmasq_config_supports_custom_zone_overrides() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"], protocol="plain"),
            SimpleNamespace(zone="gemini", servers=["1.2.3.4"], protocol="plain"),
        ],
        [SimpleNamespace(domain="gemini.com", zone="gemini", enabled=True)],
        fqdn_prefixes=[],
        ipset_name="routing_prefixes",
    )

    assert "server=/gemini.com/1.2.3.4" in config


def test_dnsmasq_config_routes_protected_zones_to_local_proxies() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"], protocol="plain"),
            SimpleNamespace(zone="dot-zone", servers=[], protocol="dot"),
            SimpleNamespace(zone="doh-zone", servers=[], protocol="doh"),
        ],
        [
            SimpleNamespace(domain="secure.example", zone="dot-zone", enabled=True),
            SimpleNamespace(domain="api.example", zone="doh-zone", enabled=True),
        ],
        fqdn_prefixes=[],
        ipset_name="routing_prefixes",
    )

    assert "server=/secure.example/127.0.0.1#5453" in config
    assert "server=/api.example/127.0.0.1#5053" in config


def test_dnsmasq_config_supports_manual_replace_addresses() -> None:
    config = build_dnsmasq_config(
        [SimpleNamespace(zone="vpn", servers=["1.1.1.1"], protocol="plain")],
        [],
        manual_addresses=[
            SimpleNamespace(domain="example.com", address="192.168.1.100", enabled=True),
            SimpleNamespace(domain="sub.example.com", address="192.168.1.101", enabled=True),
        ],
        fqdn_prefixes=[],
        ipset_name="routing_prefixes",
    )

    assert "address=/example.com/192.168.1.100" in config
    assert "address=/sub.example.com/192.168.1.101" in config
