from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.models.upstream_node import NodeStatus, ProvisioningMode, UpstreamNode
from backend.tests.conftest import TestSessionLocal


async def _add_node(name: str, *, is_active: bool = False, status: NodeStatus = NodeStatus.online) -> int:
    async with TestSessionLocal() as session:
        node = UpstreamNode(
            name=name,
            host="203.0.113.80",
            ssh_port=22,
            awg_port=51821,
            provisioning_mode=ProvisioningMode.managed,
            awg_address="10.20.0.80/32",
            public_key=f"{name}-public-key",
            status=status,
            is_active=is_active,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(node)
        await session.commit()
        return node.id


@pytest.mark.asyncio
async def test_geoip_toggle_sets_single_geoip_node(client, auth_headers) -> None:
    first_id = await _add_node("geoip-one")
    second_id = await _add_node("geoip-two")

    resp = await client.post(f"/api/nodes/{first_id}/geoip", headers=auth_headers, json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_geoip"] is True

    resp = await client.post(f"/api/nodes/{second_id}/geoip", headers=auth_headers, json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_geoip"] is True

    async with TestSessionLocal() as session:
        rows = (await session.execute(select(UpstreamNode).where(UpstreamNode.id.in_([first_id, second_id])))).scalars().all()
        by_id = {row.id: row for row in rows}
        assert by_id[first_id].is_geoip is False
        assert by_id[second_id].is_geoip is True


@pytest.mark.asyncio
async def test_geoip_node_cannot_be_activated(client, auth_headers) -> None:
    node_id = await _add_node("geoip-not-active")

    resp = await client.post(f"/api/nodes/{node_id}/geoip", headers=auth_headers, json={"enabled": True})
    assert resp.status_code == 200, resp.text

    resp = await client.post(f"/api/nodes/{node_id}/activate", headers=auth_headers)
    assert resp.status_code == 400
    assert "GeoIP node" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_active_node_cannot_be_enabled_for_geoip(client, auth_headers) -> None:
    node_id = await _add_node("active-not-geoip", is_active=True)

    resp = await client.post(f"/api/nodes/{node_id}/geoip", headers=auth_headers, json={"enabled": True})
    assert resp.status_code == 400
    assert "Active node" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pending_node_cannot_be_enabled_for_geoip(client, auth_headers) -> None:
    node_id = await _add_node("pending-not-geoip", status=NodeStatus.pending)

    resp = await client.post(f"/api/nodes/{node_id}/geoip", headers=auth_headers, json={"enabled": True})
    assert resp.status_code == 400
    assert "deployed" in resp.json()["detail"]
