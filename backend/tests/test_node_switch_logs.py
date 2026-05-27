from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.models.upstream_node import NodeStatus, ProvisioningMode, UpstreamNode, UpstreamNodeSwitchLog
from backend.tests.conftest import TestSessionLocal


async def _clear_switch_nodes() -> None:
    async with TestSessionLocal() as session:
        rows = (await session.execute(select(UpstreamNode))).scalars().all()
        for node in rows:
            node.is_active = False
            node.status = NodeStatus.offline
            session.add(node)
        await session.commit()


async def _add_switch_node(name: str, *, is_active: bool = False, priority: int = 100) -> int:
    async with TestSessionLocal() as session:
        node = UpstreamNode(
            name=name,
            host=f"203.0.113.{priority}",
            ssh_port=22,
            awg_port=51821,
            provisioning_mode=ProvisioningMode.managed,
            awg_address=f"10.20.0.{priority}/32",
            tunnel_network=f"10.20.0.0/24",
            public_key=f"{name}-public-key",
            client_address=f"10.20.0.{priority + 100}/24",
            client_allowed_ips="0.0.0.0/0",
            client_keepalive=25,
            status=NodeStatus.online,
            is_active=is_active,
            priority=priority,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(node)
        await session.commit()
        return node.id


@pytest.mark.asyncio
async def test_manual_activation_records_switch_log(client, auth_headers) -> None:
    await _clear_switch_nodes()
    first_id = await _add_switch_node("switch-from", is_active=True, priority=51)
    second_id = await _add_switch_node("switch-to", priority=52)

    resp = await client.post(f"/api/nodes/{second_id}/activate", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    async with TestSessionLocal() as session:
        log = await session.scalar(select(UpstreamNodeSwitchLog).order_by(UpstreamNodeSwitchLog.id.desc()))
        assert log is not None
        assert log.from_node_id == first_id
        assert log.from_node_name == "switch-from"
        assert log.to_node_id == second_id
        assert log.to_node_name == "switch-to"
        assert log.switch_type == "manual"

    resp = await client.get("/api/nodes/switch-logs", headers=auth_headers, params={"page": 1, "page_size": 1})

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert payload["total"] >= 1
    assert payload["items"][0]["to_node_name"] == "switch-to"


@pytest.mark.asyncio
async def test_failover_records_switch_log(monkeypatch) -> None:
    await _clear_switch_nodes()
    failed_id = await _add_switch_node("failed-node", is_active=True, priority=61)
    next_id = await _add_switch_node("next-node", priority=62)

    async def fake_apply(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("backend.services.node_deployer.apply_node_to_awg1", fake_apply)
    monkeypatch.setattr("backend.services.routing.update_vpn_route", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("backend.services.routing.update_upstream_host_route", lambda *_args, **_kwargs: None)

    from backend.services.node_deployer import deployer

    switched = await deployer.failover(failed_id)

    assert switched is True
    async with TestSessionLocal() as session:
        log = await session.scalar(select(UpstreamNodeSwitchLog).order_by(UpstreamNodeSwitchLog.id.desc()))
        assert log is not None
        assert log.from_node_id == failed_id
        assert log.from_node_name == "failed-node"
        assert log.to_node_id == next_id
        assert log.to_node_name == "next-node"
        assert log.switch_type == "failover"
