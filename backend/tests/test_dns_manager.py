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
        SimpleNamespace(domain="рф", enabled=True, upstream="yandex"),
        SimpleNamespace(domain="example.ru", enabled=True, upstream="yandex"),
    ]

    dns_manager._write_config(domains, ["77.88.8.8"], ["1.1.1.1"])

    content = conf_path.read_text()
    assert "server=/xn--p1ai/77.88.8.8" in content
    assert "server=/example.ru/77.88.8.8" in content


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
