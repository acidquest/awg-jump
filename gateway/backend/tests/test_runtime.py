from app.models import EntryNode
from app.services.runtime import probe_node_latency_details, probe_udp_endpoint, resolve_tunnel_probe_target, settings


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
