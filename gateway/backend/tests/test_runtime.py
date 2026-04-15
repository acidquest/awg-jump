import subprocess

import pytest

from app.models import EntryNode, GatewaySettings, RuntimeMode, TunnelStatus
from app.services.runtime import _setconf_with_retry, probe_node_latency_details, probe_udp_endpoint, resolve_tunnel_probe_target, settings, start_tunnel, stop_tunnel


def _make_node(**overrides) -> EntryNode:
    payload = {
        "name": "Node A",
        "raw_conf": "[Interface]\nPrivateKey = test\nAddress = 10.44.0.2/32\n\n[Peer]\nPublicKey = peer\nEndpoint = vpn.example.com:51820\nAllowedIPs = 0.0.0.0/0\n",
        "endpoint": "vpn.example.com:51820",
        "endpoint_host": "vpn.example.com",
        "endpoint_port": 51820,
        "probe_ip": None,
        "public_key": "peer-public",
        "private_key": "test-private",
        "preshared_key": None,
        "tunnel_address": "10.44.0.2/32",
        "dns_servers": [],
        "allowed_ips": ["0.0.0.0/0"],
        "persistent_keepalive": None,
        "obfuscation": {},
        "is_active": True,
    }
    payload.update(overrides)
    return EntryNode(**payload)


def test_resolve_tunnel_probe_target_prefers_explicit_probe_ip() -> None:
    node = _make_node(probe_ip="10.77.7.1")
    assert resolve_tunnel_probe_target(node) == "10.77.7.1"


def test_resolve_tunnel_probe_target_infers_entry_node_ip_from_peer_address() -> None:
    node = _make_node(tunnel_address="10.44.0.2/32", probe_ip=None)
    assert resolve_tunnel_probe_target(node) == "10.44.0.1"


def test_probe_node_latency_details_uses_inferred_target_on_tunnel(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_probe_latency(node: EntryNode, *, target: str | None = None, interface_name: str | None = None) -> float | None:
        calls.append((target or "", interface_name))
        return 12.5

    monkeypatch.setattr("app.services.runtime.probe_latency", fake_probe_latency)
    node = _make_node(tunnel_address="10.44.0.2/32", probe_ip=None)

    result = probe_node_latency_details(node, prefer_tunnel=True)

    assert result == {
        "latency_ms": 12.5,
        "target": "10.44.0.1",
        "via_interface": settings.tunnel_interface,
        "method": "icmp_ping",
    }
    assert calls == [("10.44.0.1", settings.tunnel_interface)]


def test_probe_udp_endpoint_timeout_is_available(monkeypatch) -> None:
    class FakeSocket:
        def settimeout(self, timeout_sec: float) -> None:
            pass

        def connect(self, target) -> None:
            pass

        def send(self, payload: bytes) -> None:
            pass

        def recv(self, size: int) -> bytes:
            raise TimeoutError

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.services.runtime.socket.socket", lambda *args, **kwargs: FakeSocket())
    status, detail = probe_udp_endpoint(_make_node())
    assert status == "available"
    assert detail is None


def test_probe_udp_endpoint_oserror_is_unavailable(monkeypatch) -> None:
    class FakeSocket:
        def settimeout(self, timeout_sec: float) -> None:
            pass

        def connect(self, target) -> None:
            raise OSError("network unreachable")

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.services.runtime.socket.socket", lambda *args, **kwargs: FakeSocket())
    status, detail = probe_udp_endpoint(_make_node())
    assert status == "unavailable"
    assert detail == "network unreachable"


@pytest.mark.asyncio
async def test_start_tunnel_sets_configured_mtu(monkeypatch) -> None:
    commands: list[list[str]] = []

    class FakeDb:
        def add(self, _obj) -> None:
            pass

        async def flush(self) -> None:
            pass

    monkeypatch.setattr("app.services.runtime.write_runtime_config", lambda _node: "/tmp/test.conf")
    monkeypatch.setattr("app.services.runtime.is_runtime_available", lambda: True)
    monkeypatch.setattr("app.services.runtime.stop_tunnel_process", lambda: None)
    monkeypatch.setattr("app.services.runtime._resolve_runtime_mode", lambda _mode: True)
    monkeypatch.setattr("app.services.runtime._ensure_interface_absent", lambda _iface: None)
    monkeypatch.setattr("app.services.runtime.current_pid", lambda: None)

    def fake_run_logged(args: list[str], *, context: str):
        commands.append(args)
        return None

    monkeypatch.setattr("app.services.runtime._run_logged", fake_run_logged)

    gateway_settings = GatewaySettings(runtime_mode=RuntimeMode.auto.value, tunnel_status=TunnelStatus.stopped.value)

    await start_tunnel(FakeDb(), _make_node(), gateway_settings)

    assert ["ip", "link", "set", "dev", settings.tunnel_interface, "mtu", str(settings.tunnel_mtu)] in commands


@pytest.mark.asyncio
async def test_start_tunnel_sets_active_node_uptime_epoch(monkeypatch) -> None:
    class FakeDb:
        def add(self, _obj) -> None:
            pass

        async def flush(self) -> None:
            pass

    monkeypatch.setattr("app.services.runtime.write_runtime_config", lambda _node: "/tmp/test.conf")
    monkeypatch.setattr("app.services.runtime.is_runtime_available", lambda: True)
    monkeypatch.setattr("app.services.runtime.stop_tunnel_process", lambda: None)
    monkeypatch.setattr("app.services.runtime._resolve_runtime_mode", lambda _mode: True)
    monkeypatch.setattr("app.services.runtime._ensure_interface_absent", lambda _iface: None)
    monkeypatch.setattr("app.services.runtime.current_pid", lambda: None)
    monkeypatch.setattr("app.services.runtime._run_logged", lambda _args, *, context: None)
    monkeypatch.setattr("app.services.runtime.time.time", lambda: 1_700_000_123)

    gateway_settings = GatewaySettings(
        runtime_mode=RuntimeMode.auto.value,
        tunnel_status=TunnelStatus.stopped.value,
        active_node_connected_at_epoch=12,
    )

    await start_tunnel(FakeDb(), _make_node(), gateway_settings)

    assert gateway_settings.active_node_connected_at_epoch == 1_700_000_123


@pytest.mark.asyncio
async def test_stop_tunnel_resets_active_node_uptime_epoch(monkeypatch) -> None:
    class FakeDb:
        def add(self, _obj) -> None:
            pass

        async def flush(self) -> None:
            pass

    monkeypatch.setattr("app.services.runtime.stop_tunnel_process", lambda: None)

    gateway_settings = GatewaySettings(
        tunnel_status=TunnelStatus.running.value,
        active_node_connected_at_epoch=1_700_000_123,
    )

    await stop_tunnel(FakeDb(), gateway_settings)

    assert gateway_settings.active_node_connected_at_epoch is None


def test_setconf_with_retry_recovers_from_transient_userspace_error(monkeypatch) -> None:
    calls: list[list[str]] = []
    attempts = {"count": 0}

    def fake_run_logged(args: list[str], *, context: str):
        calls.append(args)
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise subprocess.CalledProcessError(
                1,
                args,
                stderr="Unable to modify interface: Operation not supported",
            )
        return None

    monkeypatch.setattr("app.services.runtime._run_logged", fake_run_logged)
    monkeypatch.setattr("app.services.runtime.time.sleep", lambda _seconds: None)

    _setconf_with_retry("awg-gw0", "/tmp/test.conf", retries=3, delay_sec=0.01)

    assert attempts["count"] == 3
    assert calls[-1] == [settings.awg_binary, "setconf", "awg-gw0", "/tmp/test.conf"]
