from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from backend.models.upstream_node import (UpstreamNode, NodeStatus, NodeConnectionIncident, NodeConnectionAttempt)
from backend.models.routing_settings import RoutingSettings
from backend.services.node_connections import connection_statistics, observe_connection, utcnow
from backend.services.node_deployer import deployer, _health_fail_counts
from backend.scheduler import _node_health_check
from backend.tests.conftest import TestSessionLocal


@pytest.fixture
async def connection_nodes(monkeypatch):
    monkeypatch.setattr('backend.database.AsyncSessionLocal', TestSessionLocal)
    monkeypatch.setattr('backend.services.node_deployer.AsyncSessionLocal', TestSessionLocal)
    monkeypatch.setattr('backend.services.node_deployer.apply_node_to_awg1', AsyncMock())
    monkeypatch.setattr('backend.services.routing.update_vpn_route', lambda *args: None)
    monkeypatch.setattr('backend.services.routing.update_upstream_host_route', lambda *args: None)
    async with TestSessionLocal() as db:
        await db.execute(delete(NodeConnectionAttempt))
        await db.execute(delete(NodeConnectionIncident))
        # Other tests share the database; exclude their nodes from this scenario.
        originals = [(n.id, n.is_active, n.public_key) for n in (await db.scalars(select(UpstreamNode))).all()]
        old_failover = (await db.get(RoutingSettings, 1)).failover_enabled
        for node in (await db.scalars(select(UpstreamNode))).all():
            node.is_active = False
            node.public_key = None
        nodes = [UpstreamNode(name=f'retry-{i}', host=f'192.0.2.{i+1}', public_key=f'key-{i}',
            is_active=i == 0, is_geoip=False, status=NodeStatus.offline, priority=i) for i in range(3)]
        db.add_all(nodes)
        await db.commit()
        ids = [n.id for n in nodes]
    _health_fail_counts.clear()
    yield ids
    async with TestSessionLocal() as db:
        await db.execute(delete(NodeConnectionAttempt))
        await db.execute(delete(NodeConnectionIncident))
        for node_id in ids:
            await db.delete(await db.get(UpstreamNode, node_id))
        for node_id, active, key in originals:
            node = await db.get(UpstreamNode, node_id)
            node.is_active = active
            node.public_key = key
        (await db.get(RoutingSettings, 1)).failover_enabled = old_failover
        await db.commit()
    _health_fail_counts.clear()


@pytest.mark.asyncio
async def test_retry_wraps_even_when_all_nodes_offline(connection_nodes):
    ids = connection_nodes
    for source, target in [(ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[0]), (ids[0], ids[1])]:
        assert await deployer.failover(source)
        async with TestSessionLocal() as db:
            active = await db.scalar(select(UpstreamNode).where(UpstreamNode.is_active.is_(True)))
            assert active.id == target
    async with TestSessionLocal() as db:
        assert len((await db.scalars(select(NodeConnectionAttempt))).all()) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('single,rotate', [(True, True), (False, False)])
async def test_retry_keeps_selected_node(connection_nodes, single, rotate):
    if single:
        async with TestSessionLocal() as db:
            for node_id in connection_nodes[1:]:
                (await db.get(UpstreamNode, node_id)).public_key = None
            await db.commit()
    for _ in range(4):
        assert await deployer.failover(connection_nodes[0], rotate=rotate)
    async with TestSessionLocal() as db:
        assert (await db.get(UpstreamNode, connection_nodes[0])).is_active


@pytest.mark.asyncio
async def test_failed_apply_does_not_stop_rotation(connection_nodes, monkeypatch):
    monkeypatch.setattr('backend.services.node_deployer.apply_node_to_awg1', AsyncMock(side_effect=RuntimeError('network unavailable')))
    assert not await deployer.failover(connection_nodes[0])
    assert not await deployer.failover(connection_nodes[1])
    async with TestSessionLocal() as db:
        assert (await db.get(UpstreamNode, connection_nodes[2])).is_active
        rows = (await db.scalars(select(NodeConnectionAttempt))).all()
        assert all(a.error == 'network unavailable' for a in rows)


@pytest.mark.asyncio
async def test_scheduler_retries_offline_active_with_failover_disabled(connection_nodes, monkeypatch):
    async with TestSessionLocal() as db:
        (await db.get(RoutingSettings, 1)).failover_enabled = False
        await db.commit()
    check = AsyncMock(return_value={'alive': False})
    monkeypatch.setattr(deployer, 'check_health', check)
    _health_fail_counts[connection_nodes[0]] = 2
    await _node_health_check()
    async with TestSessionLocal() as db:
        attempts = (await db.scalars(select(NodeConnectionAttempt))).all()
        assert len(attempts) == 1
        assert attempts[0].node_id == connection_nodes[0]
        assert (await db.get(UpstreamNode, connection_nodes[0])).is_active


@pytest.mark.asyncio
async def test_outage_persists_and_period_duration_is_clipped(connection_nodes):
    async with TestSessionLocal() as db:
        node = await db.get(UpstreamNode, connection_nodes[0])
        await observe_connection(db, node, False)
        await observe_connection(db, node, False)
        incident = await db.scalar(select(NodeConnectionIncident))
        incident.started_at = utcnow() - timedelta(days=2)
        await db.commit()
    async with TestSessionLocal() as db:
        daily = await connection_statistics(db, 1, 1, 10)
        monthly = await connection_statistics(db, 30, 1, 10)
        assert daily['disconnects'] == 0
        assert daily['downtime_seconds'] == pytest.approx(86400, abs=1)
        assert monthly['disconnects'] == 1
        assert monthly['ongoing_since']
        node = await db.get(UpstreamNode, connection_nodes[1])
        await observe_connection(db, node, True)
        assert not (await connection_statistics(db, 30, 1, 10))['ongoing_since']


@pytest.mark.asyncio
async def test_statistics_api_auth_and_validation(client, auth_headers):
    assert (await client.get('/api/nodes/connection-statistics')).status_code == 401
    response = await client.get('/api/nodes/connection-statistics', headers=auth_headers)
    assert response.status_code == 200, response.text
    assert 'downtime_seconds' in response.json()
    assert (await client.get('/api/nodes/connection-statistics?days=0', headers=auth_headers)).status_code == 422


@pytest.mark.asyncio
async def test_scheduler_stops_reapplying_after_confirmed_recovery(connection_nodes, monkeypatch):
    async with TestSessionLocal() as db:
        (await db.get(RoutingSettings, 1)).failover_enabled = False
        await db.commit()
    health = {'alive': False, 'established': False}
    monkeypatch.setattr(deployer, 'check_health', AsyncMock(side_effect=lambda _: dict(health)))
    _health_fail_counts[connection_nodes[0]] = 2
    await _node_health_check()
    health.update(alive=True, established=True)
    for _ in range(4):
        await _node_health_check()
    async with TestSessionLocal() as db:
        assert len((await db.scalars(select(NodeConnectionAttempt))).all()) == 1
        incident = await db.scalar(select(NodeConnectionIncident))
        assert incident.ended_at is not None


@pytest.mark.asyncio
async def test_grace_period_does_not_close_existing_outage(connection_nodes, monkeypatch):
    async with TestSessionLocal() as db:
        await observe_connection(db, await db.get(UpstreamNode, connection_nodes[0]), False)
    monkeypatch.setattr(deployer, 'check_health', AsyncMock(return_value={'alive': True, 'established': False}))
    await _node_health_check()
    async with TestSessionLocal() as db:
        assert (await db.scalar(select(NodeConnectionIncident))).ended_at is None
