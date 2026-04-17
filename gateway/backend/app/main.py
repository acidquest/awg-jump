from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap import ensure_bootstrap_state
from app.config import ensure_directories, settings
from app.database import AsyncSessionLocal, Base, engine
from app.models import EntryNode, GatewaySettings, RoutingPolicy
from app.routers import access, auth, backup, devices, dns, nodes, routing, settings as settings_router, system
from app.services.dns_runtime import restart_dnsmasq, stop_dnsmasq
from app.services.device_tracking import DEVICE_TRACKING_INTERVAL_SECONDS, collect_device_inventory
from app.services.external_ip import EXTERNAL_IP_REFRESH_INTERVAL_SECONDS, refresh_external_ip_info, validate_service_pair
from app.services.failover import evaluate_failover_health, failover_to_next_available, start_tunnel_with_retries
from app.services.routing import apply_local_passthrough, apply_routing_plan, sync_firewall_backend
from app.services.traffic_sources import migrate_legacy_source_settings
from app.services.runtime import reset_active_node_uptime, resolve_live_tunnel_status, stop_tunnel
from app.services.system_metrics import collect_system_metrics
from app.services.traffic_metrics import RAW_SAMPLE_INTERVAL_SECONDS, collect_traffic_metrics


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


async def _ensure_sqlite_columns() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(routing_policies)"))
        columns = {row[1] for row in result.fetchall()}
        if "manual_prefixes" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN manual_prefixes JSON NOT NULL DEFAULT '[]'")
            )
        if "countries_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN countries_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "manual_prefixes_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN manual_prefixes_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
        if "fqdn_prefixes_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN fqdn_prefixes_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
        if "fqdn_prefixes" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN fqdn_prefixes JSON NOT NULL DEFAULT '[]'")
            )
        if "prefixes_route_local" not in columns:
            await conn.execute(
                text("ALTER TABLE routing_policies ADD COLUMN prefixes_route_local BOOLEAN NOT NULL DEFAULT 1")
            )
        await conn.execute(
            text("UPDATE routing_policies SET geoip_ipset_name = 'routing_prefixes' WHERE geoip_ipset_name = 'gateway_geoip_local'")
        )
        result = await conn.execute(text("PRAGMA table_info(gateway_settings)"))
        columns = {row[1] for row in result.fetchall()}
        if "runtime_mode" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN runtime_mode VARCHAR(16) NOT NULL DEFAULT 'auto'")
            )
        if "dns_intercept_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN dns_intercept_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "gateway_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN gateway_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "experimental_nftables" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN experimental_nftables BOOLEAN NOT NULL DEFAULT 0")
            )
        if "failover_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN failover_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
        if "failover_unhealthy_since" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN failover_unhealthy_since DATETIME"))
        if "failover_last_event_at" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN failover_last_event_at DATETIME"))
        if "failover_last_error" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN failover_last_error TEXT"))
        if "api_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN api_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
        if "api_access_key" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN api_access_key VARCHAR(64)"))
        if "api_control_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN api_control_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
        if "api_allowed_client_cidrs" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN api_allowed_client_cidrs JSON NOT NULL DEFAULT '[]'")
            )
        if "device_tracking_enabled" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN device_tracking_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "device_activity_timeout_seconds" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN device_activity_timeout_seconds INTEGER NOT NULL DEFAULT 300")
            )
        if "device_api_default_scope" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN device_api_default_scope VARCHAR(16) NOT NULL DEFAULT 'all'")
            )
        if "external_ip_local_service_url" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE gateway_settings ADD COLUMN external_ip_local_service_url VARCHAR(512) NOT NULL DEFAULT 'https://ipinfo.io/ip'"
                )
            )
        if "external_ip_vpn_service_url" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE gateway_settings ADD COLUMN external_ip_vpn_service_url VARCHAR(512) NOT NULL DEFAULT 'https://ifconfig.me/ip'"
                )
            )
        if "external_ip_local_value" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_local_value VARCHAR(64)"))
        if "external_ip_vpn_value" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_vpn_value VARCHAR(64)"))
        if "external_ip_local_error" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_local_error TEXT"))
        if "external_ip_vpn_error" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_vpn_error TEXT"))
        if "external_ip_local_checked_at" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_local_checked_at DATETIME"))
        if "external_ip_vpn_checked_at" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN external_ip_vpn_checked_at DATETIME"))
        if "active_node_connected_at_epoch" not in columns:
            await conn.execute(text("ALTER TABLE gateway_settings ADD COLUMN active_node_connected_at_epoch INTEGER"))
        local_service_url, vpn_service_url = validate_service_pair(
            settings.external_ip_local_service_url,
            settings.external_ip_vpn_service_url,
        )
        await conn.execute(
            text(
                """
                UPDATE gateway_settings
                SET external_ip_local_service_url = COALESCE(NULLIF(external_ip_local_service_url, ''), :local_url),
                    external_ip_vpn_service_url = COALESCE(NULLIF(external_ip_vpn_service_url, ''), :vpn_url)
                """
            ),
            {"local_url": local_service_url, "vpn_url": vpn_service_url},
        )
        result = await conn.execute(text("PRAGMA table_info(entry_nodes)"))
        columns = {row[1] for row in result.fetchall()}
        if "probe_ip" not in columns:
            await conn.execute(
                text("ALTER TABLE entry_nodes ADD COLUMN probe_ip VARCHAR(64)")
            )
        if "position" not in columns:
            await conn.execute(
                text("ALTER TABLE entry_nodes ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            )
            await conn.execute(
                text(
                    """
                    WITH ordered AS (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY id) - 1 AS rn
                        FROM entry_nodes
                    )
                    UPDATE entry_nodes
                    SET position = (SELECT rn FROM ordered WHERE ordered.id = entry_nodes.id)
                    """
                )
            )
        result = await conn.execute(text("PRAGMA table_info(dns_upstreams)"))
        dns_upstream_columns = {row[1] for row in result.fetchall()}
        if "name" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN name VARCHAR(128) NOT NULL DEFAULT ''"))
        if "is_builtin" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN is_builtin BOOLEAN NOT NULL DEFAULT 0"))
        if "protocol" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN protocol VARCHAR(16) NOT NULL DEFAULT 'plain'"))
        if "endpoint_host" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN endpoint_host VARCHAR(253) NOT NULL DEFAULT ''"))
        if "endpoint_port" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN endpoint_port INTEGER"))
        if "endpoint_url" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN endpoint_url VARCHAR(512) NOT NULL DEFAULT ''"))
        if "bootstrap_address" not in dns_upstream_columns:
            await conn.execute(text("ALTER TABLE dns_upstreams ADD COLUMN bootstrap_address VARCHAR(64) NOT NULL DEFAULT ''"))
        await conn.execute(
            text(
                """
                UPDATE dns_upstreams
                SET name = CASE
                    WHEN zone = 'local' AND (name IS NULL OR name = '') THEN 'Local'
                    WHEN zone = 'vpn' AND (name IS NULL OR name = '') THEN 'Upstream'
                    WHEN name IS NULL OR name = '' THEN zone
                    ELSE name
                END,
                    is_builtin = CASE WHEN zone IN ('local', 'vpn') THEN 1 ELSE is_builtin END,
                    protocol = COALESCE(NULLIF(protocol, ''), 'plain'),
                    endpoint_host = COALESCE(endpoint_host, ''),
                    endpoint_url = COALESCE(endpoint_url, ''),
                    bootstrap_address = COALESCE(bootstrap_address, '')
                """
            )
        )
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dns_manual_addresses'"))
        if result.first() is None:
            await conn.execute(
                text(
                    """
                    CREATE TABLE dns_manual_addresses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        domain VARCHAR(253) NOT NULL UNIQUE,
                        address VARCHAR(64) NOT NULL,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
        result = await conn.execute(text("PRAGMA table_info(system_metrics)"))
        metric_columns = {row[1] for row in result.fetchall()}
        if not metric_columns:
            await conn.execute(
                text(
                    """
                    CREATE TABLE system_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        collected_at DATETIME NOT NULL,
                        cpu_usage_percent FLOAT NOT NULL DEFAULT 0,
                        cpu_total_ticks INTEGER NOT NULL DEFAULT 0,
                        cpu_idle_ticks INTEGER NOT NULL DEFAULT 0,
                        memory_total_bytes INTEGER NOT NULL DEFAULT 0,
                        memory_used_bytes INTEGER NOT NULL DEFAULT 0,
                        memory_free_bytes INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_system_metrics_collected_at ON system_metrics (collected_at)")
            )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS traffic_metric_state (
                    id INTEGER PRIMARY KEY,
                    collected_at DATETIME NOT NULL,
                    local_interface_name VARCHAR(64),
                    vpn_interface_name VARCHAR(64) NOT NULL,
                    local_rx_raw_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_raw_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_raw_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_raw_bytes BIGINT NOT NULL DEFAULT 0,
                    local_rx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_total_bytes BIGINT NOT NULL DEFAULT 0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS traffic_metric_raw (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at DATETIME NOT NULL,
                    local_interface_name VARCHAR(64),
                    vpn_interface_name VARCHAR(64) NOT NULL,
                    local_rx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_total_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_total_bytes BIGINT NOT NULL DEFAULT 0
                )
                """
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_traffic_metric_raw_collected_at ON traffic_metric_raw (collected_at)")
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS traffic_metric_minute (
                    bucket_start DATETIME PRIMARY KEY,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    local_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_bytes BIGINT NOT NULL DEFAULT 0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS traffic_metric_hour (
                    bucket_start DATETIME PRIMARY KEY,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    local_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_bytes BIGINT NOT NULL DEFAULT 0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS traffic_metric_day (
                    bucket_start DATETIME PRIMARY KEY,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    local_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    local_tx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_rx_bytes BIGINT NOT NULL DEFAULT 0,
                    vpn_tx_bytes BIGINT NOT NULL DEFAULT 0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS first_node_bootstrap_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_host VARCHAR(256) NOT NULL,
                    ssh_user VARCHAR(128) NOT NULL,
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    remote_dir VARCHAR(512) NOT NULL,
                    docker_namespace VARCHAR(256) NOT NULL,
                    image_tag VARCHAR(128) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'running',
                    log_output TEXT NOT NULL DEFAULT '',
                    finished_at DATETIME,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tracked_devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_key VARCHAR(128) NOT NULL UNIQUE,
                    identity_source VARCHAR(16) NOT NULL DEFAULT 'ip',
                    mac_address VARCHAR(32),
                    current_ip VARCHAR(64),
                    hostname VARCHAR(255),
                    manual_alias VARCHAR(255) NOT NULL DEFAULT '',
                    is_marked BOOLEAN NOT NULL DEFAULT 0,
                    first_seen_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,
                    last_traffic_at DATETIME,
                    last_presence_check_at DATETIME,
                    last_present_at DATETIME,
                    last_absent_at DATETIME,
                    is_active BOOLEAN NOT NULL DEFAULT 0,
                    is_present BOOLEAN NOT NULL DEFAULT 0,
                    last_route_target VARCHAR(16) NOT NULL DEFAULT 'unknown',
                    total_bytes BIGINT NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_devices_identity_key ON tracked_devices (identity_key)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_devices_mac_address ON tracked_devices (mac_address)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_devices_current_ip ON tracked_devices (current_ip)"))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tracked_device_ips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL REFERENCES tracked_devices(id) ON DELETE CASCADE,
                    ip_address VARCHAR(64) NOT NULL,
                    is_current BOOLEAN NOT NULL DEFAULT 0,
                    first_seen_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_device_ips_device_id ON tracked_device_ips (device_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_device_ips_ip_address ON tracked_device_ips (ip_address)"))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS tracked_device_flow_state (
                    flow_key VARCHAR(255) PRIMARY KEY,
                    device_id INTEGER REFERENCES tracked_devices(id) ON DELETE SET NULL,
                    source_ip VARCHAR(64) NOT NULL,
                    route_target VARCHAR(16) NOT NULL DEFAULT 'unknown',
                    last_bytes BIGINT NOT NULL DEFAULT 0,
                    last_seen_at DATETIME NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracked_device_flow_state_source_ip ON tracked_device_flow_state (source_ip)"))


async def _restore_runtime_state(session: AsyncSession) -> None:
    settings_row = await session.get(GatewaySettings, 1)
    if settings_row is None:
        return
    if not settings_row.gateway_enabled:
        await stop_tunnel(session, settings_row)
        apply_local_passthrough(settings_row)
        await session.commit()
        return
    if settings_row.active_entry_node_id is None:
        return

    active_node = await session.get(EntryNode, settings_row.active_entry_node_id)
    if active_node is None:
        settings_row.active_entry_node_id = None
        settings_row.tunnel_status = "stopped"
        settings_row.tunnel_last_error = "Previously selected entry node no longer exists"
        session.add(settings_row)
        await session.commit()
        return

    logger.info("[gateway-startup] restoring tunnel for active node id=%s name=%s", active_node.id, active_node.name)
    result, _probe = await start_tunnel_with_retries(session, active_node, settings_row)
    if result.get("status") == "running":
        policy = await session.get(RoutingPolicy, 1)
        try:
            apply_routing_plan(settings_row, policy, active_node)
            settings_row.tunnel_last_error = None
            logger.info("[gateway-startup] routing restore applied for active node id=%s", active_node.id)
        except RuntimeError as exc:
            settings_row.tunnel_last_error = str(exc)
            logger.error("[gateway-startup] routing restore failed: %s", exc)
        session.add(settings_row)
        await session.commit()
        return

    if settings_row.failover_enabled:
        replacement = await failover_to_next_available(
            session,
            settings_row,
            reason=f"Startup restore failed for node {active_node.name}: {settings_row.tunnel_last_error or 'unknown error'}",
            failed_node_id=active_node.id,
        )
        if replacement is not None:
            await session.commit()
            return
    await session.commit()


async def _metrics_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as session:
                await collect_system_metrics(session)
        except Exception as exc:
            logger.error("[gateway-metrics] sample failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def _traffic_metrics_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as session:
                await collect_traffic_metrics(session)
        except Exception as exc:
            logger.error("[gateway-traffic-metrics] sample failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RAW_SAMPLE_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _external_ip_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as session:
                await refresh_external_ip_info(session, force=True)
                await session.commit()
        except Exception as exc:
            logger.error("[gateway-external-ip] refresh failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=EXTERNAL_IP_REFRESH_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _failover_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as session:
                settings_row = await session.get(GatewaySettings, 1)
                if settings_row is not None:
                    live_status, live_error = resolve_live_tunnel_status(settings_row)
                    settings_row.tunnel_status = live_status
                    settings_row.tunnel_last_error = live_error
                    if live_status != "running":
                        reset_active_node_uptime(settings_row)
                    session.add(settings_row)
                    await session.flush()
                    await evaluate_failover_health(session, settings_row)
                    await session.commit()
        except Exception as exc:
            logger.error("[gateway-failover] cycle failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            continue


async def _device_tracking_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as session:
                await collect_device_inventory(session)
                await session.commit()
        except Exception as exc:
            logger.error("[gateway-device-tracking] cycle failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=DEVICE_TRACKING_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    metrics_stop = asyncio.Event()
    traffic_metrics_stop = asyncio.Event()
    external_ip_stop = asyncio.Event()
    failover_stop = asyncio.Event()
    device_tracking_stop = asyncio.Event()
    metrics_task: asyncio.Task | None = None
    traffic_metrics_task: asyncio.Task | None = None
    external_ip_task: asyncio.Task | None = None
    failover_task: asyncio.Task | None = None
    device_tracking_task: asyncio.Task | None = None
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_sqlite_columns()
    async with AsyncSessionLocal() as session:
        await ensure_bootstrap_state(session)
    async with AsyncSessionLocal() as session:
        gateway_settings = await session.get(GatewaySettings, 1)
        if gateway_settings and migrate_legacy_source_settings(gateway_settings):
            session.add(gateway_settings)
            await session.commit()
    async with AsyncSessionLocal() as session:
        gateway_settings = await session.get(GatewaySettings, 1)
        routing_policy = await session.get(RoutingPolicy, 1)
        if gateway_settings and routing_policy:
            if gateway_settings.gateway_enabled:
                sync_firewall_backend(gateway_settings, routing_policy)
            else:
                apply_local_passthrough(gateway_settings)
    async with AsyncSessionLocal() as session:
        try:
            await restart_dnsmasq(session)
        except RuntimeError as exc:
            logger.error("[gateway-startup] dnsmasq start failed: %s", exc)
    async with AsyncSessionLocal() as session:
        await _restore_runtime_state(session)
    async with AsyncSessionLocal() as session:
        try:
            await refresh_external_ip_info(session, force=True)
            await session.commit()
        except Exception as exc:
            logger.error("[gateway-startup] external IP refresh failed: %s", exc)
    metrics_task = asyncio.create_task(_metrics_loop(metrics_stop))
    traffic_metrics_task = asyncio.create_task(_traffic_metrics_loop(traffic_metrics_stop))
    external_ip_task = asyncio.create_task(_external_ip_loop(external_ip_stop))
    failover_task = asyncio.create_task(_failover_loop(failover_stop))
    device_tracking_task = asyncio.create_task(_device_tracking_loop(device_tracking_stop))
    yield
    metrics_stop.set()
    traffic_metrics_stop.set()
    external_ip_stop.set()
    failover_stop.set()
    device_tracking_stop.set()
    if metrics_task is not None:
        await metrics_task
    if traffic_metrics_task is not None:
        await traffic_metrics_task
    if external_ip_task is not None:
        await external_ip_task
    if failover_task is not None:
        await failover_task
    if device_tracking_task is not None:
        await device_tracking_task
    async with AsyncSessionLocal() as session:
        gateway_settings = await session.get(GatewaySettings, 1)
        if gateway_settings is not None:
            await stop_tunnel(session, gateway_settings)
            apply_local_passthrough(gateway_settings)
            await session.commit()
    stop_dnsmasq()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/docs" if settings.allow_api_docs else None,
    redoc_url="/api/redoc" if settings.allow_api_docs else None,
    openapi_url="/api/openapi.json" if settings.allow_api_docs else None,
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(access.router)
app.include_router(devices.router)
app.include_router(nodes.router)
app.include_router(routing.router)
app.include_router(dns.router)
app.include_router(backup.router)
app.include_router(system.router)


_STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/dist"))

if os.path.isdir(_STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="gateway-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
