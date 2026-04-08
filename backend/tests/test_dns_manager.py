from types import SimpleNamespace

from backend.services import dns_manager


def test_to_dnsmasq_domain_converts_idn_to_idna():
    assert dns_manager._to_dnsmasq_domain("рф") == "xn--p1ai"
    assert dns_manager._to_dnsmasq_domain("ЯНДЕКС.РФ") == "xn--d1acpjx3f.xn--p1ai"


def test_write_config_uses_idna_domains(tmp_path, monkeypatch):
    conf_path = tmp_path / "dnsmasq-awg.conf"

    monkeypatch.setattr(dns_manager, "_CONF_FILE", str(conf_path))
    monkeypatch.setattr(dns_manager, "get_awg0_ip", lambda: "10.77.7.1")

    domains = [
        SimpleNamespace(domain="рф", enabled=True, upstream="yandex"),
        SimpleNamespace(domain="example.ru", enabled=True, upstream="yandex"),
    ]

    dns_manager._write_config(domains, ["77.88.8.8"], ["1.1.1.1"])

    content = conf_path.read_text()
    assert "server=/xn--p1ai/77.88.8.8" in content
    assert "server=/example.ru/77.88.8.8" in content
