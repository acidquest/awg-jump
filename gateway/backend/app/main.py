from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap import ensure_bootstrap_state
from app.config import ensure_directories, settings
from app.database import (
    AsyncSessionLocal,
    MetricsSessionLocal,
    commit_with_lock,
    engine,
    metrics_engine,
    prepare_session,
)
from app.models import BackupRecord, EntryNode, GatewaySettings, MAIN_DB_TABLES, METRICS_DB_TABLES, RoutingPolicy
from app.routers import access, auth, backup, devices, dns, nodes, routing, settings as settings_router, system
from app.services.backup import build_backup_filename, create_backup_file, prune_backup_files
from app.services.dns_runtime import restart_dnsmasq, stop_dnsmasq
from app.services.device_tracking import DEVICE_TRACKING_INTERVAL_SECONDS, collect_device_inventory
from app.services.external_ip import EXTERNAL_IP_REFRESH_INTERVAL_SECONDS, refresh_external_ip_info, validate_service_pair
from app.services.failover import evaluate_failover_health, failover_to_next_available, start_tunnel_with_retries
from app.services.maintenance import wait_until_ready
from app.services.routing import apply_local_passthrough, apply_routing_plan, sync_firewall_backend
from app.services.status_reporting import (
    STATUS_REPORT_POLL_SECONDS,
    maybe_report_gateway_status,
    reset_status_report_state,
)
from app.services.traffic_sources import migrate_legacy_source_settings
from app.services.runtime import reset_active_node_uptime, resolve_live_tunnel_status, stop_tunnel
from app.services.runtime_state import get_tunnel_runtime_state
from app.services.system_metrics import collect_system_metrics
from app.services.traffic_metrics import RAW_SAMPLE_INTERVAL_SECONDS, collect_traffic_metrics


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)
SQLITE_LOCK_RETRY_DELAYS = (0.2, 0.5, 1.0)
METRICS_LOOP_START_DELAY_SECONDS = 0
TRAFFIC_LOOP_START_DELAY_SECONDS = 5
EXTERNAL_IP_LOOP_START_DELAY_SECONDS = 11
FAILOVER_LOOP_START_DELAY_SECONDS = 17
DEVICE_TRACKING_LOOP_START_DELAY_SECONDS = 23
BACKUP_LOOP_START_DELAY_SECONDS = 29
STATUS_REPORT_LOOP_START_DELAY_SECONDS = 7


def _is_sqlite_lock_error(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


async def _run_db_cycle_with_retry(label: str, session_factory, action, *, metrics: bool = False) -> None:
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *SQLITE_LOCK_RETRY_DELAYS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await wait_until_ready()
            async with session_factory() as session:
                prepare_session(session, metrics=metrics)
                await action(session)
                if session.dirty or session.new or session.deleted:
                    await commit_with_lock(session, metrics=metrics)
            return
        except Exception as exc:
            last_exc = exc
            if _is_sqlite_lock_error(exc) and attempt <= len(SQLITE_LOCK_RETRY_DELAYS):
                logger.warning("[%s] sqlite lock on attempt %s, retrying: %s", label, attempt, exc)
                continue
            logger.error("[%s] cycle failed: %s", label, exc)
            return
    if last_exc is not None:
        logger.error("[%s] cycle failed after retries: %s", label, last_exc)


async def _initial_loop_delay(stop_event: asyncio.Event, delay_seconds: int) -> bool:
    if delay_seconds <= 0:
        return False
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _ensure_current_baseline_columns() -> None:
    local_service_url, vpn_service_url = validate_service_pair(
        settings.external_ip_local_service_url,
        settings.external_ip_vpn_service_url,
    )
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(gateway_settings)")
        columns = {row[1] for row in result.fetchall()}
        if "backup_enabled" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE gateway_settings ADD COLUMN backup_enabled BOOLEAN NOT NULL DEFAULT 1"
            )
        if "backup_schedule_time" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE gateway_settings ADD COLUMN backup_schedule_time VARCHAR(5) NOT NULL DEFAULT '03:00'"
            )
        if "backup_retention_count" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE gateway_settings ADD COLUMN backup_retention_count INTEGER NOT NULL DEFAULT 14"
            )
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        settings_row = await session.get(GatewaySettings, 1)
        if settings_row is None:
            return
        settings_row.external_ip_local_service_url = settings_row.external_ip_local_service_url or local_service_url
        settings_row.external_ip_vpn_service_url = settings_row.external_ip_vpn_service_url or vpn_service_url
        session.add(settings_row)
        await commit_with_lock(session)
    async with metrics_engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(tracked_devices)")
        columns = {row[1] for row in result.fetchall()}
        if "forced_route_target" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE tracked_devices ADD COLUMN forced_route_target VARCHAR(16) NOT NULL DEFAULT 'none'"
            )
        await conn.exec_driver_sql("UPDATE tracked_devices SET total_bytes = 0 WHERE total_bytes IS NULL")
        await conn.exec_driver_sql("UPDATE tracked_devices SET is_marked = 0 WHERE is_marked IS NULL")
        await conn.exec_driver_sql("UPDATE tracked_devices SET forced_route_target = 'none' WHERE forced_route_target IS NULL OR forced_route_target = ''")
        await conn.exec_driver_sql("UPDATE tracked_devices SET manual_alias = '' WHERE manual_alias IS NULL")
        await conn.exec_driver_sql("UPDATE tracked_devices SET last_route_target = 'unknown' WHERE last_route_target IS NULL OR last_route_target = ''")


def _backup_is_due(settings_row: GatewaySettings, *, now: datetime) -> bool:
    if not settings_row.backup_enabled:
        return False
    try:
        hour, minute = [int(part) for part in settings_row.backup_schedule_time.split(":", 1)]
    except (TypeError, ValueError):
        return False
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= scheduled


async def _backup_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, BACKUP_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            settings_row = await session.get(GatewaySettings, 1)
            if settings_row is None or not settings_row.backup_enabled:
                return
            now = datetime.now().astimezone()
            if not _backup_is_due(settings_row, now=now):
                return
            scheduled_filename = (
                f"awg-gateway-scheduled-{now.strftime('%Y%m%d')}_"
                f"{settings_row.backup_schedule_time.replace(':', '')}.zip"
            )
            existing = await session.scalar(select(BackupRecord).where(BackupRecord.filename == scheduled_filename))
            if existing is not None:
                return
            await create_backup_file(session, kind="scheduled", filename=scheduled_filename)
            await prune_backup_files(session, retention_count=settings_row.backup_retention_count)

        await _run_db_cycle_with_retry("gateway-backup", AsyncSessionLocal, action)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def _restore_runtime_state(session: AsyncSession) -> None:
    settings_row = await session.get(GatewaySettings, 1)
    if settings_row is None:
        return
    if not settings_row.gateway_enabled:
        await stop_tunnel(settings_row)
        apply_local_passthrough(settings_row)
        await commit_with_lock(session)
        return
    if settings_row.active_entry_node_id is None:
        return

    active_node = await session.get(EntryNode, settings_row.active_entry_node_id)
    if active_node is None:
        settings_row.active_entry_node_id = None
        session.add(settings_row)
        tunnel_state = get_tunnel_runtime_state()
        tunnel_state.status = "stopped"
        tunnel_state.last_error = "Previously selected entry node no longer exists"
        await commit_with_lock(session)
        return

    logger.info("[gateway-startup] restoring tunnel for active node id=%s name=%s", active_node.id, active_node.name)
    result, _probe = await start_tunnel_with_retries(session, active_node, settings_row)
    if result.get("status") == "running":
        policy = await session.get(RoutingPolicy, 1)
        try:
            apply_routing_plan(settings_row, policy, active_node)
            get_tunnel_runtime_state().last_error = None
            logger.info("[gateway-startup] routing restore applied for active node id=%s", active_node.id)
        except RuntimeError as exc:
            get_tunnel_runtime_state().last_error = str(exc)
            logger.error("[gateway-startup] routing restore failed: %s", exc)
        await commit_with_lock(session)
        return

    if settings_row.failover_enabled:
        replacement = await failover_to_next_available(
            session,
            settings_row,
            reason=f"Startup restore failed for node {active_node.name}: {get_tunnel_runtime_state().last_error or 'unknown error'}",
            failed_node_id=active_node.id,
        )
        if replacement is not None:
            await commit_with_lock(session)
            return
    await commit_with_lock(session)


async def _metrics_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, METRICS_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                await collect_system_metrics(session)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-metrics", MetricsSessionLocal, action, metrics=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def _traffic_metrics_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, TRAFFIC_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                async with AsyncSessionLocal() as main_session:
                    settings_row = await main_session.get(GatewaySettings, 1)
                await collect_traffic_metrics(session, settings_row)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-traffic-metrics", MetricsSessionLocal, action, metrics=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RAW_SAMPLE_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _external_ip_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, EXTERNAL_IP_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                settings_row = await session.get(GatewaySettings, 1)
                await refresh_external_ip_info(settings_row, force=True)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-external-ip", AsyncSessionLocal, action)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=EXTERNAL_IP_REFRESH_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _status_report_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, STATUS_REPORT_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                await maybe_report_gateway_status(session)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-status-report", AsyncSessionLocal, action)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=STATUS_REPORT_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _failover_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, FAILOVER_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                settings_row = await session.get(GatewaySettings, 1)
                if settings_row is not None:
                    live_status, live_error = resolve_live_tunnel_status(settings_row)
                    if live_status != "running":
                        reset_active_node_uptime(settings_row)
                    await evaluate_failover_health(session, settings_row)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-failover", AsyncSessionLocal, action)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            continue


async def _device_tracking_loop(stop_event: asyncio.Event) -> None:
    if await _initial_loop_delay(stop_event, DEVICE_TRACKING_LOOP_START_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        async def action(session: AsyncSession) -> None:
            try:
                async with AsyncSessionLocal() as main_session:
                    settings_row = await main_session.get(GatewaySettings, 1)
                await collect_device_inventory(session, settings_row)
            except Exception:
                await session.rollback()
                raise

        await _run_db_cycle_with_retry("gateway-device-tracking", MetricsSessionLocal, action, metrics=True)
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
    backup_stop = asyncio.Event()
    status_report_stop = asyncio.Event()
    metrics_task: asyncio.Task | None = None
    traffic_metrics_task: asyncio.Task | None = None
    external_ip_task: asyncio.Task | None = None
    failover_task: asyncio.Task | None = None
    device_tracking_task: asyncio.Task | None = None
    backup_task: asyncio.Task | None = None
    status_report_task: asyncio.Task | None = None
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: [table.create(sync_conn, checkfirst=True) for table in MAIN_DB_TABLES])
    async with metrics_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: [table.create(sync_conn, checkfirst=True) for table in METRICS_DB_TABLES])
    await _ensure_current_baseline_columns()
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        await ensure_bootstrap_state(session)
        await commit_with_lock(session)
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        gateway_settings = await session.get(GatewaySettings, 1)
        if gateway_settings and migrate_legacy_source_settings(gateway_settings):
            session.add(gateway_settings)
            await commit_with_lock(session)
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        gateway_settings = await session.get(GatewaySettings, 1)
        routing_policy = await session.get(RoutingPolicy, 1)
        if gateway_settings and routing_policy:
            if gateway_settings.gateway_enabled:
                sync_firewall_backend(gateway_settings, routing_policy)
            else:
                apply_local_passthrough(gateway_settings)
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        try:
            await restart_dnsmasq(session)
        except RuntimeError as exc:
            logger.error("[gateway-startup] dnsmasq start failed: %s", exc)
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        await _restore_runtime_state(session)
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        try:
            settings_row = await session.get(GatewaySettings, 1)
            await refresh_external_ip_info(settings_row, force=True)
            await commit_with_lock(session)
        except Exception as exc:
            logger.error("[gateway-startup] external IP refresh failed: %s", exc)
    metrics_task = asyncio.create_task(_metrics_loop(metrics_stop))
    traffic_metrics_task = asyncio.create_task(_traffic_metrics_loop(traffic_metrics_stop))
    external_ip_task = asyncio.create_task(_external_ip_loop(external_ip_stop))
    failover_task = asyncio.create_task(_failover_loop(failover_stop))
    device_tracking_task = asyncio.create_task(_device_tracking_loop(device_tracking_stop))
    backup_task = asyncio.create_task(_backup_loop(backup_stop))
    status_report_task = asyncio.create_task(_status_report_loop(status_report_stop))
    yield
    metrics_stop.set()
    traffic_metrics_stop.set()
    external_ip_stop.set()
    failover_stop.set()
    device_tracking_stop.set()
    backup_stop.set()
    status_report_stop.set()
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
    if backup_task is not None:
        await backup_task
    if status_report_task is not None:
        await status_report_task
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        gateway_settings = await session.get(GatewaySettings, 1)
        if gateway_settings is not None:
            await stop_tunnel(gateway_settings)
            apply_local_passthrough(gateway_settings)
            await commit_with_lock(session)
    stop_dnsmasq()
    reset_status_report_state()


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
        candidate = os.path.abspath(os.path.join(_STATIC_DIR, full_path))
        if full_path and candidate.startswith(_STATIC_DIR + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        if full_path.startswith("api/"):
            return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
