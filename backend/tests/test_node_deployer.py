from datetime import datetime, timezone

import pytest

from sqlalchemy import select

from backend.models.interface import Interface
from backend.models.upstream_node import NodePeer, NodeStatus, ProvisioningMode, UpstreamNode
from backend.services.node_deployer import _get_node, _make_env_content, _make_node_server_config, deployer
from backend.services.upstream_nodes import apply_node_to_awg1
from backend.tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_get_node_eager_loads_shared_peers(db_session) -> None:
    node = UpstreamNode(
        name="test-node",
        host="203.0.113.10",
        ssh_port=22,
        awg_port=51821,
        provisioning_mode=ProvisioningMode.managed,
        awg_address="10.20.0.3/32",
        private_key="node-private-key",
        public_key="node-public-key",
        status=NodeStatus.online,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(node)
    await db_session.flush()

    peer = NodePeer(
        node_id=node.id,
        name="shared-peer-1",
        private_key="peer-private-key",
        public_key="peer-public-key",
        tunnel_address="10.30.0.2/32",
        allowed_ips="0.0.0.0/0",
        persistent_keepalive=25,
        enabled=True,
    )
    db_session.add(peer)
    await db_session.commit()

    loaded = await _get_node(node.id, db_session)

    shared_peers = list(loaded.shared_peers)
    assert len(shared_peers) == 1
    assert shared_peers[0].name == "shared-peer-1"
    assert shared_peers[0].tunnel_address == "10.30.0.2/32"


def test_make_env_content_uses_24_mask_for_node_interface() -> None:
    env_content = _make_env_content(
        private_key="node-private-key",
        awg_address="10.20.0.3/32",
        awg_port=51821,
    )

    assert "AWG_ADDRESS=10.20.0.3/24" in env_content
    assert "AWG_ADDRESS=10.20.0.3/32" not in env_content


@pytest.mark.asyncio
async def test_apply_node_to_awg1_updates_interface_address_and_obfuscation(db_session, monkeypatch) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg1"))
    awg1 = result.scalar_one()

    node = UpstreamNode(
        name="managed-node",
        host="203.0.113.30",
        ssh_port=22,
        awg_port=51821,
        provisioning_mode=ProvisioningMode.managed,
        awg_address="10.20.0.3/32",
        public_key="node-public-key",
        client_address="10.20.0.22/32",
        client_allowed_ips="0.0.0.0/0",
        client_keepalive=33,
        client_obf_jc=9,
        client_obf_jmin=50,
        client_obf_jmax=95,
        client_obf_s1=201,
        client_obf_s2=202,
        client_obf_s3=203,
        client_obf_s4=204,
        client_obf_h1=123456781,
        client_obf_h2=123456782,
        client_obf_h3=123456783,
        client_obf_h4=123456784,
        status=NodeStatus.online,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(node)
    await db_session.flush()

    captured: dict[str, object] = {}

    async def fake_apply_interface(iface, peers):
        captured["iface"] = iface
        captured["peers"] = peers

    monkeypatch.setattr("backend.services.upstream_nodes.awg_svc.apply_interface", fake_apply_interface)

    await apply_node_to_awg1(db_session, node)

    assert awg1.address == "10.20.0.22/32"
    assert awg1.endpoint == "203.0.113.30:51821"
    assert awg1.persistent_keepalive == 33
    assert awg1.obf_s1 == 201
    assert awg1.obf_h4 == 123456784
    assert len(captured["peers"]) == 1


def test_make_node_server_config_uses_node_client_address_for_jump_peer() -> None:
    awg1 = Interface(
        name="awg1",
        obf_s1=10,
        obf_s2=20,
        obf_s3=30,
        obf_s4=40,
        obf_h1=1001,
        obf_h2=1002,
        obf_h3=1003,
        obf_h4=1004,
    )

    config = _make_node_server_config(
        private_key="node-private-key",
        awg_address="10.20.0.3/32",
        awg_port=51821,
        awg1_public_key="jump-public-key",
        client_address="10.20.0.22/32",
        awg1=awg1,
        shared_peers=[],
    )

    assert "AllowedIPs = 10.20.0.22/32" in config


@pytest.mark.asyncio
async def test_check_health_for_active_node_uses_explicit_probe_ip(monkeypatch) -> None:
    async with TestSessionLocal() as session:
        node = UpstreamNode(
            name="active-node",
            host="203.0.113.10",
            ssh_port=22,
            awg_port=51821,
            provisioning_mode=ProvisioningMode.managed,
            awg_address="10.20.0.3/32",
            probe_ip="10.20.0.1",
            private_key="node-private-key",
            public_key="node-public-key",
            status=NodeStatus.online,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(node)
        await session.commit()
        node_id = node.id

    monkeypatch.setattr("backend.services.node_deployer.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr("backend.services.node_deployer._measure_ping_latency", lambda target: (target == "10.20.0.1", 12.5))
    monkeypatch.setattr(
        "backend.services.node_deployer._run_cmd",
        lambda args: (0, "node-public-key\tpsk\t203.0.113.10:51821\t0.0.0.0/0\t1\t100\t200\t25\n"),
    )

    result = await deployer.check_health(node_id)

    assert result["alive"] is True
    assert result["latency_ms"] == 12.5
    assert result["probe_ip"] == "10.20.0.1"

    async with TestSessionLocal() as session:
        refreshed = await session.get(UpstreamNode, node_id)
        assert refreshed is not None
        assert refreshed.latency_ms == 12.5
        assert refreshed.rx_bytes == 100
        assert refreshed.tx_bytes == 200


@pytest.mark.asyncio
async def test_check_health_for_inactive_node_uses_udp_only(monkeypatch) -> None:
    async with TestSessionLocal() as session:
        node = UpstreamNode(
            name="inactive-node",
            host="203.0.113.20",
            ssh_port=22,
            awg_port=51821,
            provisioning_mode=ProvisioningMode.managed,
            awg_address="10.20.0.4/32",
            private_key="node-private-key",
            public_key="node-public-key-2",
            status=NodeStatus.degraded,
            is_active=False,
            latency_ms=55.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(node)
        await session.commit()
        node_id = node.id

    monkeypatch.setattr("backend.services.node_deployer.AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr("backend.services.node_deployer._probe_udp_port", lambda host, port: (True, "no ICMP error received"))

    result = await deployer.check_health(node_id)

    assert result["alive"] is True
    assert result["udp_status"] == "online"
    assert result["latency_ms"] is None

    async with TestSessionLocal() as session:
        refreshed = await session.get(UpstreamNode, node_id)
        assert refreshed is not None
        assert refreshed.status == NodeStatus.online
        assert refreshed.latency_ms is None
