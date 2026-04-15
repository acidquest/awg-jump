from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import AuditEvent, EntryNode, GatewaySettings, RoutingPolicy, TunnelStatus
from app.services import failover


def _make_node(node_id: int, name: str, position: int, *, is_active: bool = False) -> EntryNode:
    return EntryNode(
        id=node_id,
        name=name,
        raw_conf="[Interface]\nPrivateKey = test\nAddress = 10.44.0.2/32\n\n[Peer]\nPublicKey = peer\nEndpoint = vpn.example.com:51820\nAllowedIPs = 0.0.0.0/0\n",
        endpoint=f"{name.lower()}.example.com:51820",
        endpoint_host=f"{name.lower()}.example.com",
        endpoint_port=51820,
        public_key=f"{name.lower()}-pub",
        private_key=f"{name.lower()}-priv",
        preshared_key=None,
        tunnel_address="10.44.0.2/32",
        dns_servers=[],
        allowed_ips=["0.0.0.0/0"],
        persistent_keepalive=None,
        obfuscation={},
        position=position,
        is_active=is_active,
    )


class FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalarResult(self._items)


class FakeSession:
    def __init__(self, *, nodes: list[EntryNode] | None = None, settings: GatewaySettings | None = None, policy: RoutingPolicy | None = None):
        self.nodes = list(nodes or [])
        self.settings = settings or GatewaySettings(id=1)
        self.policy = policy or RoutingPolicy(id=1)
        self.audit_events: list[AuditEvent] = []

    async def execute(self, _statement):
        return FakeExecuteResult(sorted(self.nodes, key=lambda node: (node.position, node.id)))

    async def scalar(self, _statement):
        if not self.nodes:
            return None
        return max(node.position for node in self.nodes)

    async def get(self, model, key):
        if model is GatewaySettings:
            return self.settings if key == self.settings.id else None
        if model is RoutingPolicy:
            return self.policy if key == self.policy.id else None
        if model is EntryNode:
            return next((node for node in self.nodes if node.id == key), None)
        return None

    def add(self, obj):
        if isinstance(obj, EntryNode):
            if all(existing.id != obj.id for existing in self.nodes):
                self.nodes.append(obj)
        elif isinstance(obj, AuditEvent):
            self.audit_events.append(obj)
        elif isinstance(obj, GatewaySettings):
            self.settings = obj
        elif isinstance(obj, RoutingPolicy):
            self.policy = obj

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_assign_active_node_moves_node_to_first() -> None:
    node_a = _make_node(1, "NodeA", 0, is_active=True)
    node_b = _make_node(2, "NodeB", 1)
    node_c = _make_node(3, "NodeC", 2)
    settings_row = GatewaySettings(id=1, active_entry_node_id=node_a.id)
    db = FakeSession(nodes=[node_a, node_b, node_c], settings=settings_row)

    await failover.assign_active_node(db, settings_row, node_c, record_event=False)

    ordered = await failover.list_nodes_in_order(db)
    assert [node.name for node in ordered] == ["NodeC", "NodeA", "NodeB"]
    assert ordered[0].is_active is True
    assert ordered[0].position == 0
    assert settings_row.active_entry_node_id == node_c.id


@pytest.mark.asyncio
async def test_move_node_does_not_jump_over_active_node() -> None:
    node_a = _make_node(1, "NodeA", 0, is_active=True)
    node_b = _make_node(2, "NodeB", 1)
    node_c = _make_node(3, "NodeC", 2)
    db = FakeSession(nodes=[node_a, node_b, node_c])

    await failover.move_node_by_direction(db, node_b, "up")

    ordered = await failover.list_nodes_in_order(db)
    assert [node.name for node in ordered] == ["NodeA", "NodeB", "NodeC"]


@pytest.mark.asyncio
async def test_start_tunnel_with_retries_succeeds_on_third_probe(monkeypatch) -> None:
    node = _make_node(1, "NodeA", 0, is_active=True)
    settings_row = GatewaySettings(id=1)
    db = FakeSession(nodes=[node], settings=settings_row)
    calls = {"start": 0, "probe": 0}

    async def fake_start_tunnel(_db, _node_obj, gateway_settings):
        calls["start"] += 1
        gateway_settings.tunnel_status = TunnelStatus.running.value
        gateway_settings.tunnel_last_error = None
        return {"status": TunnelStatus.running.value}

    def fake_probe(_node: EntryNode, *, prefer_tunnel: bool = False):
        calls["probe"] += 1
        if calls["probe"] < 3:
            return {"latency_ms": None, "target": "10.44.0.1", "via_interface": "awg-gw0", "method": "icmp_ping"}
        return {"latency_ms": 11.5, "target": "10.44.0.1", "via_interface": "awg-gw0", "method": "icmp_ping"}

    monkeypatch.setattr("app.services.failover.start_tunnel", fake_start_tunnel)
    monkeypatch.setattr("app.services.failover.probe_node_latency_details", fake_probe)

    result, probe = await failover.start_tunnel_with_retries(db, node, settings_row)

    assert result["status"] == TunnelStatus.running.value
    assert probe["latency_ms"] == 11.5
    assert calls == {"start": 3, "probe": 3}
    assert node.latest_latency_ms == 11.5


@pytest.mark.asyncio
async def test_evaluate_failover_health_switches_after_grace_period(monkeypatch) -> None:
    node_a = _make_node(1, "NodeA", 0, is_active=True)
    node_b = _make_node(2, "NodeB", 1)
    settings_row = GatewaySettings(
        id=1,
        gateway_enabled=True,
        failover_enabled=True,
        active_entry_node_id=node_a.id,
        tunnel_status=TunnelStatus.running.value,
        failover_unhealthy_since=failover.utcnow() - failover.FAILOVER_DISCONNECT_GRACE - timedelta(seconds=1),
    )
    db = FakeSession(nodes=[node_a, node_b], settings=settings_row)

    def fake_probe(_node: EntryNode, *, prefer_tunnel: bool = False):
        return {"latency_ms": None, "target": "10.44.0.1", "via_interface": "awg-gw0", "method": "icmp_ping"}

    calls: list[tuple[int | None, str]] = []

    async def fake_failover_to_next_available(_db, _settings, *, reason: str, failed_node_id: int | None = None):
        calls.append((failed_node_id, reason))
        return node_b

    monkeypatch.setattr("app.services.failover.probe_node_latency_details", fake_probe)
    monkeypatch.setattr("app.services.failover.failover_to_next_available", fake_failover_to_next_available)

    await failover.evaluate_failover_health(db, settings_row)

    assert calls
    assert calls[0][0] == node_a.id
    assert "NodeA" in calls[0][1]
