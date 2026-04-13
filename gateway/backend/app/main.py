from __future__ import annotations

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
from app.routers import auth, backup, dns, nodes, routing, settings as settings_router, system
from app.services.dns_runtime import restart_dnsmasq, stop_dnsmasq
from app.services.routing import apply_routing_plan
from app.services.runtime import start_tunnel


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
        result = await conn.execute(text("PRAGMA table_info(gateway_settings)"))
        columns = {row[1] for row in result.fetchall()}
        if "runtime_mode" not in columns:
            await conn.execute(
                text("ALTER TABLE gateway_settings ADD COLUMN runtime_mode VARCHAR(16) NOT NULL DEFAULT 'auto'")
            )
        result = await conn.execute(text("PRAGMA table_info(entry_nodes)"))
        columns = {row[1] for row in result.fetchall()}
        if "probe_ip" not in columns:
            await conn.execute(
                text("ALTER TABLE entry_nodes ADD COLUMN probe_ip VARCHAR(64)")
            )


async def _restore_runtime_state(session: AsyncSession) -> None:
    settings_row = await session.get(GatewaySettings, 1)
    if settings_row is None or settings_row.active_entry_node_id is None:
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
    result = await start_tunnel(session, active_node, settings_row)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_sqlite_columns()
    async with AsyncSessionLocal() as session:
        await ensure_bootstrap_state(session)
    async with AsyncSessionLocal() as session:
        try:
            await restart_dnsmasq(session)
        except RuntimeError as exc:
            logger.error("[gateway-startup] dnsmasq start failed: %s", exc)
    async with AsyncSessionLocal() as session:
        await _restore_runtime_state(session)
    yield
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
