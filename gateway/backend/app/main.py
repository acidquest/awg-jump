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
from app.routers import auth, backup, dns, nodes, routing, settings as settings_router, system


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_directories()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_sqlite_columns()
    async with AsyncSessionLocal() as session:
        await ensure_bootstrap_state(session)
    yield


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
