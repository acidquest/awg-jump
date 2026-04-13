from types import SimpleNamespace

from app.services.dns import build_dnsmasq_config


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
