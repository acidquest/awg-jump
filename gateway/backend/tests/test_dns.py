from types import SimpleNamespace

from app.services.dns import _to_dnsmasq_domain, build_dnsmasq_config


def test_dnsmasq_config_uses_single_bind_mode() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"]),
            SimpleNamespace(zone="local", servers=["9.9.9.9"]),
        ],
        [SimpleNamespace(domain="example.com", zone="local", enabled=True)],
        fqdn_prefixes=["api.example.com"],
        ipset_name="routing_prefixes",
    )
    assert "bind-dynamic" in config
    assert "bind-interfaces" not in config
    assert "server=/example.com/9.9.9.9" in config
    assert "ipset=/api.example.com/routing_prefixes" in config


def test_to_dnsmasq_domain_converts_idn_to_idna() -> None:
    assert _to_dnsmasq_domain("рф") == "xn--p1ai"
    assert _to_dnsmasq_domain("ЯНДЕКС.РФ") == "xn--d1acpjx3f.xn--p1ai"


def test_dnsmasq_config_converts_idn_domains_and_fqdn_prefixes() -> None:
    config = build_dnsmasq_config(
        [
            SimpleNamespace(zone="vpn", servers=["1.1.1.1"]),
            SimpleNamespace(zone="local", servers=["9.9.9.9"]),
        ],
        [SimpleNamespace(domain="яндекс.рф", zone="local", enabled=True)],
        fqdn_prefixes=["почта.яндекс.рф"],
        ipset_name="routing_prefixes",
    )

    assert "server=/xn--d1acpjx3f.xn--p1ai/9.9.9.9" in config
    assert "ipset=/xn--80a1acny.xn--d1acpjx3f.xn--p1ai/routing_prefixes" in config
