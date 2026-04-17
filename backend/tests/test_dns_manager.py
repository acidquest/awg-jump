from types import SimpleNamespace
from unittest.mock import Mock

from backend.services import dns_manager


def test_to_dnsmasq_domain_converts_idn_to_idna():
    assert dns_manager._to_dnsmasq_domain("рф") == "xn--p1ai"
    assert dns_manager._to_dnsmasq_domain("ЯНДЕКС.РФ") == "xn--d1acpjx3f.xn--p1ai"


def test_write_config_uses_idna_domains(tmp_path, monkeypatch):
    conf_path = tmp_path / "dnsmasq-awg.conf"

    monkeypatch.setattr(dns_manager, "_CONF_FILE", str(conf_path))
    monkeypatch.setattr(dns_manager, "get_awg0_ip", lambda: "10.77.7.1")

    domains = [
        SimpleNamespace(domain="рф", enabled=True, upstream="local"),
        SimpleNamespace(domain="example.ru", enabled=True, upstream="local"),
    ]

    dns_manager._write_config(
        domains,
        {
            "local": {"protocol": "plain", "dns_servers": ["77.88.8.8"]},
            "vpn": {"protocol": "plain", "dns_servers": ["1.1.1.1"]},
        },
    )

    content = conf_path.read_text()
    assert "server=/xn--p1ai/77.88.8.8" in content
    assert "server=/example.ru/77.88.8.8" in content
    assert "# Special zone overrides" in content


def test_start_uses_non_blocking_process(monkeypatch):
    dns_manager._PROCESS = None

    monkeypatch.setattr(dns_manager, "is_running", lambda: False)
    monkeypatch.setattr(dns_manager, "_patch_resolv_conf", lambda: None)

    run_mock = Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr(dns_manager.subprocess, "run", run_mock)

    proc = SimpleNamespace(pid=1234, returncode=None, poll=lambda: None)
    popen_mock = Mock(return_value=proc)
    monkeypatch.setattr(dns_manager.subprocess, "Popen", popen_mock)

    checks = iter([False, True])
    monkeypatch.setattr(dns_manager, "is_running", lambda: next(checks))
    monkeypatch.setattr(dns_manager.time, "sleep", lambda _: None)

    dns_manager.start()

    popen_args = popen_mock.call_args.args[0]
    assert "--keep-in-foreground" in popen_args
    assert dns_manager._PROCESS is proc


def test_reload_process_restarts_running_dnsmasq(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(dns_manager, "is_running", lambda: True)
    monkeypatch.setattr(dns_manager, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(dns_manager, "start", lambda: calls.append("start"))

    dns_manager._reload_process()

    assert calls == ["stop", "start"]


def test_write_config_supports_custom_zone_overrides(tmp_path, monkeypatch):
    conf_path = tmp_path / "dnsmasq-awg.conf"

    monkeypatch.setattr(dns_manager, "_CONF_FILE", str(conf_path))
    monkeypatch.setattr(dns_manager, "get_awg0_ip", lambda: "10.77.7.1")

    domains = [
        SimpleNamespace(domain="gemini.com", enabled=True, upstream="gemini"),
    ]

    dns_manager._write_config(
        domains,
        {
            "vpn": {"protocol": "plain", "dns_servers": ["1.1.1.1"]},
            "gemini": {"protocol": "plain", "dns_servers": ["1.2.3.4"]},
        },
    )

    content = conf_path.read_text()
    assert "server=/gemini.com/1.2.3.4" in content


def test_write_config_supports_manual_replace_addresses(tmp_path, monkeypatch):
    conf_path = tmp_path / "dnsmasq-awg.conf"

    monkeypatch.setattr(dns_manager, "_CONF_FILE", str(conf_path))
    monkeypatch.setattr(dns_manager, "get_awg0_ip", lambda: "10.77.7.1")

    manual_addresses = [
        SimpleNamespace(domain="example.com", address="192.168.1.100", enabled=True),
        SimpleNamespace(domain="sub.example.com", address="192.168.1.101", enabled=True),
    ]

    dns_manager._write_config([], {"vpn": {"protocol": "plain", "dns_servers": ["1.1.1.1"]}}, manual_addresses)

    content = conf_path.read_text()
    assert "address=/example.com/192.168.1.100" in content
    assert "address=/sub.example.com/192.168.1.101" in content


def test_write_config_routes_dot_and_doh_zones_to_local_proxies(tmp_path, monkeypatch):
    conf_path = tmp_path / "dnsmasq-awg.conf"

    monkeypatch.setattr(dns_manager, "_CONF_FILE", str(conf_path))
    monkeypatch.setattr(dns_manager, "get_awg0_ip", lambda: "10.77.7.1")

    domains = [
        SimpleNamespace(domain="secure.example", enabled=True, upstream="dot-zone"),
        SimpleNamespace(domain="api.example", enabled=True, upstream="doh-zone"),
    ]

    dns_manager._write_config(
        domains,
        {
            "vpn": {"protocol": "plain", "dns_servers": ["1.1.1.1"]},
            "dot-zone": {"protocol": "dot", "dns_servers": []},
            "doh-zone": {"protocol": "doh", "dns_servers": []},
        },
    )

    content = conf_path.read_text()
    assert "server=/secure.example/127.0.0.1#5453" in content
    assert "server=/api.example/127.0.0.1#5053" in content
