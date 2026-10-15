"""Device API must respond while inventory probes wait on the network."""

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_metrics_db
from app.models import METRICS_DB_TABLES, TrackedDevice
from app.routers.access import router
from app.security import get_api_settings
from app.services import device_tracking


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["neighbors", "conntrack", "neighbor_ping", "hostname", "presence_ping"])
async def test_device_api_responds_during_slow_inventory_probe(tmp_path, monkeypatch, phase):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: [table.create(conn) for table in METRICS_DB_TABLES])
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as session:
        session.add(TrackedDevice(
            identity_key="mac:aa:bb:cc:dd:ee:ff", identity_source="mac",
            mac_address="aa:bb:cc:dd:ee:ff", current_ip="192.168.1.10",
            is_marked=True, is_present=True, is_active=False,
            first_seen_at=datetime.now(), last_seen_at=datetime.now(),
        ))
        await session.commit()

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def pause():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), "API did not respond while the probe was blocked"

    def run(args):
        if args[0] == "ip":
            if phase == "neighbors":
                pause()
            if phase == "presence_ping":
                return 0, ""
            state = "STALE" if phase == "neighbor_ping" else "REACHABLE"
            return 0, f"192.168.1.10 dev eth0 lladdr aa:bb:cc:dd:ee:ff {state}"
        if phase == "conntrack":
            pause()
        return 0, ""

    def ping(ip):
        if phase in {"neighbor_ping", "presence_ping"}:
            pause()
        return True

    def hostname(ip):
        if phase == "hostname":
            pause()
        return "phone"

    monkeypatch.setattr(device_tracking, "_run", run)
    monkeypatch.setattr(device_tracking, "_ping", ping)
    monkeypatch.setattr(device_tracking, "_resolve_hostname", hostname)
    settings = SimpleNamespace(device_tracking_enabled=True, device_activity_timeout_seconds=300,
                               allowed_client_cidrs=["192.168.1.0/24"], device_api_default_scope="marked")

    async def metrics_db():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_metrics_db] = metrics_db
    app.dependency_overrides[get_api_settings] = lambda: settings

    async def inventory():
        async with sessions() as session:
            await device_tracking.collect_device_inventory(session, settings)
            await session.commit()

    task = asyncio.create_task(inventory())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/access/devices?scope=marked"), timeout=1)
        assert not task.done()
        assert response.status_code == 200
        assert response.json()["devices"][0]["is_present"] is True
    finally:
        release.set()
        try:
            await task
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["external_ip", "failover", "traffic_metrics"])
async def test_periodic_network_probes_allow_other_requests(monkeypatch, service):
    from unittest.mock import AsyncMock

    from app.models import EntryNode, GatewaySettings
    from app.services import external_ip, failover, traffic_metrics

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def slow_result(value):
        def probe(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "Network probe blocked request processing"
            return value
        return probe

    session = AsyncMock()
    if service == "external_ip":
        settings = GatewaySettings(external_ip_local_service_url="https://local.test/ip",
                                   external_ip_vpn_service_url="https://vpn.test/ip")
        monkeypatch.setattr(external_ip, "resolve_live_tunnel_status", lambda _: ("running", None))
        monkeypatch.setattr(external_ip, "_curl_external_ip", slow_result("192.0.2.1"))
        action = external_ip.refresh_external_ip_info(settings, force=True)
    elif service == "failover":
        settings = GatewaySettings(failover_enabled=True, gateway_enabled=True, active_entry_node_id=1)
        session.get.return_value = EntryNode(id=1, name="test")
        monkeypatch.setattr(failover, "get_tunnel_runtime_state", lambda: SimpleNamespace(status="running"))
        monkeypatch.setattr(failover, "probe_node_latency_details", slow_result({"latency_ms": 1.0}))
        monkeypatch.setattr(failover, "mark_failover_healthy", AsyncMock())
        action = failover.evaluate_failover_health(session, settings)
    else:
        session.get.return_value = None
        # add() is synchronous, unlike DB queries.
        session.add = lambda _: None
        snapshot = traffic_metrics.InterfaceCounterSnapshot("eth0", "awg0", 10, 20, 30, 40)
        monkeypatch.setattr(traffic_metrics, "read_interface_counter_snapshot", slow_result(snapshot))
        monkeypatch.setattr(traffic_metrics, "_prune_traffic_metrics", AsyncMock())
        action = traffic_metrics.collect_traffic_metrics(session)

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    task = asyncio.create_task(action)
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/health"), timeout=1)
        assert not task.done()
        assert response.json() == {"ok": True}
    finally:
        release.set()
        await task
