"""
AWG Jump — FastAPI application entry point.

Startup sequence:
  1. DB tables (alembic fallback)
  2. AWG key generation + obfuscation params
  3. AWG interfaces up
  4. GeoIP cache → ipset
  5. Policy routing + iptables
  6. APScheduler start
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from backend.config import settings as _settings, validate_security_settings
from backend.database import AsyncSessionLocal, engine, Base
from backend.models.interface import Interface
from backend.models.geoip import GeoipSource
from backend.models.routing_settings import RoutingSettings
from backend.models.upstream_node import NodeStatus, UpstreamNode
from backend.models.dns_manual_address import DnsManualAddress
from backend.routers import auth, backup, dns, geoip, interfaces, nodes, peers, routing, system
from backend.scheduler import scheduler, setup_scheduler
import backend.services.awg as awg_svc
import backend.services.dns_manager as dns_mgr
import backend.services.geoip_fetcher as geoip_fetcher
import backend.services.ipset_manager as ipset_mgr
import backend.services.routing as routing_svc
from backend.services.system_metrics import collect_system_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Startup helpers ───────────────────────────────────────────────────────

async def _ensure_sqlite_columns() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(dns_zone_settings)"))
        zone_columns = {row[1] for row in result.fetchall()}
        if "name" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN name VARCHAR(128) NOT NULL DEFAULT ''"))
        if "is_builtin" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN is_builtin BOOLEAN NOT NULL DEFAULT 0"))
        if "protocol" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN protocol VARCHAR(16) NOT NULL DEFAULT 'plain'"))
        if "endpoint_host" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN endpoint_host VARCHAR(253) NOT NULL DEFAULT ''"))
        if "endpoint_port" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN endpoint_port INTEGER"))
        if "endpoint_url" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN endpoint_url VARCHAR(512) NOT NULL DEFAULT ''"))
        if "bootstrap_address" not in zone_columns:
            await conn.execute(text("ALTER TABLE dns_zone_settings ADD COLUMN bootstrap_address VARCHAR(64) NOT NULL DEFAULT ''"))
        await conn.execute(
            text(
                """
                UPDATE dns_zone_settings
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

        result = await conn.execute(text("PRAGMA table_info(dns_domains)"))
        result.fetchall()
        await conn.execute(
            text(
                """
                UPDATE dns_domains
                SET upstream = CASE
                    WHEN upstream = 'yandex' THEN 'local'
                    WHEN upstream = 'default' THEN 'vpn'
                    ELSE upstream
                END
                """
            )
        )
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='dns_manual_addresses'"))
        if result.first() is None:
            await conn.run_sync(lambda sync_conn: Base.metadata.tables["dns_manual_addresses"].create(sync_conn))

async def _init_keys_and_obfuscation() -> None:
    """Генерирует AWG ключи и параметры обфускации если отсутствуют."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Interface))
        ifaces = result.scalars().all()
        for iface in ifaces:
            changed = False
            if not iface.private_key:
                priv, pub = awg_svc.generate_keypair()
                iface.private_key = priv
                iface.public_key = pub
                iface.updated_at = datetime.now(timezone.utc)
                changed = True
                logger.info("Generated keys for interface %s", iface.name)
            if iface.obf_h1 is None:
                await awg_svc.ensure_obfuscation_params(iface, session)
                changed = True
                logger.info("Generated obfuscation params for %s", iface.name)
            if changed:
                session.add(iface)
        await session.commit()


async def _start_interfaces() -> None:
    """Поднимает все enabled AWG интерфейсы."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Interface).where(Interface.enabled == True)  # noqa: E712
        )
        for iface in result.scalars().all():
            if not iface.private_key:
                logger.warning("Skipping %s — no private key", iface.name)
                continue
            try:
                await awg_svc.load_interface(iface, session)
                logger.info("Interface %s started", iface.name)
            except Exception as e:
                logger.error("Failed to start %s: %s", iface.name, e, exc_info=True)


async def _init_geoip_and_routing() -> None:
    """
    Загружает GeoIP из кэша → ipset.
    Настраивает policy routing и iptables.
    """
    # GeoIP → ipset
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(GeoipSource).where(GeoipSource.enabled == True)  # noqa: E712
            )
            sources = result.scalars().all()

        merged_prefixes: set[str] = set()
        for source in sources:
            source.ipset_name = geoip_fetcher.LOCAL_GEOIP_IPSET_NAME
            prefixes = geoip_fetcher.load_from_cache(source.country_code)
            if prefixes:
                logger.info(
                    "Loaded cache for %s (%s): %d prefixes",
                    source.country_code,
                    source.display_name or source.name,
                    len(prefixes),
                )
                merged_prefixes.update(prefixes)
            else:
                logger.warning("No GeoIP cache for %s", source.country_code)

        logger.info(
            "Loading aggregated ipset %s from cache: %d prefixes",
            geoip_fetcher.LOCAL_GEOIP_IPSET_NAME,
            len(merged_prefixes),
        )
        ipset_mgr.create_or_update(
            geoip_fetcher.LOCAL_GEOIP_IPSET_NAME,
            sorted(merged_prefixes),
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(GeoipSource))
            for source in result.scalars().all():
                source.ipset_name = geoip_fetcher.LOCAL_GEOIP_IPSET_NAME
                session.add(source)
            await session.commit()
    except Exception as e:
        logger.error("GeoIP/ipset init failed: %s", e)

    # Policy routing
    try:
        async with AsyncSessionLocal() as session:
            routing_settings = await session.get(RoutingSettings, 1)
            invert_geoip = routing_settings.invert_geoip if routing_settings else False
            active_node = await session.scalar(
                select(UpstreamNode).where(
                    UpstreamNode.is_active == True,  # noqa: E712
                    UpstreamNode.status == NodeStatus.online,
                )
            )
        routing_svc.setup_policy_routing()
        routing_svc.update_vpn_route("awg1" if active_node else None)
        routing_svc.update_upstream_host_route(
            active_node.awg_address if active_node and active_node.awg_address else None
        )
        routing_svc.setup_iptables(invert_geoip=invert_geoip)
        logger.info("Policy routing configured")
    except Exception as e:
        logger.error("Routing setup failed: %s", e)


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Step 0: Security warnings
    validate_security_settings()

    # Step 1: DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_sqlite_columns()

    # Step 2: AWG keys + obfuscation
    try:
        await _init_keys_and_obfuscation()
    except Exception as e:
        logger.error("Key init failed: %s", e)

    # Step 3: AWG interfaces
    try:
        await _start_interfaces()
    except Exception as e:
        logger.error("Interface startup failed: %s", e)

    # Step 4+5: GeoIP + routing
    try:
        await _init_geoip_and_routing()
    except Exception as e:
        logger.error("GeoIP/routing init failed: %s", e)

    # Step 6: Split DNS (dnsmasq)
    try:
        async with AsyncSessionLocal() as session:
            await dns_mgr.reload(session)
        logger.info("Split DNS started")
    except Exception as e:
        logger.error("Split DNS init failed: %s", e)

    # Step 7: Scheduler
    setup_scheduler()

    # Step 8: Initial system metrics sample
    try:
        async with AsyncSessionLocal() as session:
            await collect_system_metrics(session)
    except Exception as e:
        logger.error("Initial system metrics sample failed: %s", e)

    yield

    # ── Graceful shutdown ─────────────────────────────────────────────────
    if scheduler.running:
        scheduler.shutdown(wait=False)

    dns_mgr.stop()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Interface))
        for iface in result.scalars().all():
            try:
                await awg_svc.stop_interface(iface.name)
            except Exception:
                pass


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AWG Jump",
    version="1.0.0",
    docs_url="/api/docs" if _settings.enable_api_docs else None,
    redoc_url="/api/redoc" if _settings.enable_api_docs else None,
    openapi_url="/api/openapi.json" if _settings.enable_api_docs else None,
    lifespan=lifespan,
)

# ── Exception handlers ────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Routers ───────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(interfaces.router)
app.include_router(peers.router)
app.include_router(geoip.router)
app.include_router(routing.router)
app.include_router(dns.router)
app.include_router(system.router)
app.include_router(backup.router)
app.include_router(nodes.router)


# ── Health (no auth) ──────────────────────────────────────────────────────

@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


# ── SPA static files ─────────────────────────────────────────────────────

_STATIC_DIR = "/app/static"

if os.path.exists(_STATIC_DIR):
    # Монтируем статику для assets (JS/CSS/img) — точный match по файлам
    app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")

    # Catch-all: все остальные не-API запросы отдают index.html (SPA routing)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # Если запрошен реальный файл (favicon, robots.txt и т.п.) — отдаём его
        file_path = os.path.join(_STATIC_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
