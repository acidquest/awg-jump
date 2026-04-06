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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from backend.database import AsyncSessionLocal, engine, Base
from backend.models.interface import Interface
from backend.models.geoip import GeoipSource
from backend.routers import auth, backup, geoip, interfaces, nodes, peers, routing, system
from backend.scheduler import scheduler, setup_scheduler
import backend.services.awg as awg_svc
import backend.services.geoip_fetcher as geoip_fetcher
import backend.services.ipset_manager as ipset_mgr
import backend.services.routing as routing_svc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Startup helpers ───────────────────────────────────────────────────────

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
                logger.error("Failed to start %s: %s", iface.name, e)


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

        for source in sources:
            prefixes = geoip_fetcher.load_from_cache(source.country_code)
            if prefixes:
                logger.info(
                    "Loading ipset %s from cache: %d prefixes",
                    source.ipset_name, len(prefixes),
                )
                ipset_mgr.create_or_update(source.ipset_name, prefixes)
            else:
                logger.warning(
                    "No GeoIP cache for %s — creating empty ipset %s",
                    source.country_code, source.ipset_name,
                )
                if not ipset_mgr.exists(source.ipset_name):
                    ipset_mgr.create(source.ipset_name)
    except Exception as e:
        logger.error("GeoIP/ipset init failed: %s", e)

    # Policy routing
    try:
        routing_svc.setup_policy_routing()
        routing_svc.setup_iptables()
        logger.info("Policy routing configured")
    except Exception as e:
        logger.error("Routing setup failed: %s", e)


# ── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Step 1: DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

    # Step 6: Scheduler
    setup_scheduler()

    yield

    # ── Graceful shutdown ─────────────────────────────────────────────────
    if scheduler.running:
        scheduler.shutdown(wait=False)

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
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
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
app.include_router(system.router)
app.include_router(backup.router)
app.include_router(nodes.router)


# ── Health (no auth) ──────────────────────────────────────────────────────

@app.get("/api/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


# ── SPA static files ─────────────────────────────────────────────────────

if os.path.exists("/app/static"):
    app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
