from datetime import datetime, timezone

import pytest

from backend.models.routing_settings import RoutingSettings
from backend.models.upstream_node import NodeStatus, ProvisioningMode, UpstreamNode
from backend.scheduler import _node_health_check
from backend.services.node_deployer import _health_fail_counts
from backend.tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_failover_settings_roundtrip(client, auth_headers) -> None:
    resp = await client.get("/api/nodes/failover", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True}

    resp = await client.put(
        "/api/nodes/failover",
        headers=auth_headers,
        json={"enabled": False},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": False}

    async with TestSessionLocal() as session:
        settings_row = await session.get(RoutingSettings, 1)
        assert settings_row is not None
        assert settings_row.failover_enabled is False


@pytest.mark.asyncio
async def test_node_health_check_does_not_failover_when_disabled(monkeypatch) -> None:
    async with TestSessionLocal() as session:
        settings_row = await session.get(RoutingSettings, 1)
        assert settings_row is not None
        settings_row.failover_enabled = False

        node = UpstreamNode(
            name="active-node",
            host="203.0.113.50",
            ssh_port=22,
            awg_port=51821,
            provisioning_mode=ProvisioningMode.managed,
            awg_address="10.20.0.9/32",
            public_key="active-node-public-key",
            status=NodeStatus.online,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(node)
        await session.commit()
        node_id = node.id

    async def fake_check_health(_node_id: int) -> dict:
        return {"node_id": _node_id, "alive": False, "latency_ms": None}

    async def fake_failover(_failed_node_id: int) -> bool:
        raise AssertionError("failover must stay disabled")

    monkeypatch.setattr("backend.services.node_deployer.deployer.check_health", fake_check_health)
    monkeypatch.setattr("backend.services.node_deployer.deployer.failover", fake_failover)

    _health_fail_counts[node_id] = 2
    await _node_health_check()

    assert _health_fail_counts[node_id] == 0

