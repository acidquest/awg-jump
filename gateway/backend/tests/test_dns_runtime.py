from pathlib import Path

from app.services import dns_runtime


def test_stop_dnsmasq_terminates_process_from_pidfile(monkeypatch, tmp_path: Path) -> None:
    pidfile = tmp_path / "dnsmasq.pid"
    pidfile.write_text("4321\n", encoding="utf-8")
    calls: list[tuple[int, int]] = []
    checks = {"count": 0}

    monkeypatch.setattr(dns_runtime, "pid_path", lambda: pidfile)
    monkeypatch.setattr(dns_runtime, "_DNS_PROCESS", None)
    monkeypatch.setattr(dns_runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(dns_runtime.time, "monotonic", lambda: checks["count"])

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            checks["count"] += 1
            if checks["count"] > 1:
                raise ProcessLookupError

    monkeypatch.setattr(dns_runtime.os, "kill", fake_kill)

    dns_runtime.stop_dnsmasq()

    assert calls == [(4321, dns_runtime.signal.SIGTERM), (4321, 0), (4321, 0)]
    assert not pidfile.exists()


def test_stop_dnsmasq_removes_stale_pidfile(monkeypatch, tmp_path: Path) -> None:
    pidfile = tmp_path / "dnsmasq.pid"
    pidfile.write_text("5555\n", encoding="utf-8")

    monkeypatch.setattr(dns_runtime, "pid_path", lambda: pidfile)
    monkeypatch.setattr(dns_runtime, "_DNS_PROCESS", None)

    def fake_kill(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(dns_runtime.os, "kill", fake_kill)

    dns_runtime.stop_dnsmasq()

    assert not pidfile.exists()
