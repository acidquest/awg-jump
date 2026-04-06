from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.database import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Таблицы создаются через alembic migrate, но оставим fallback
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="AWG Jump",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# SPA fallback — все остальные маршруты отдают index.html
app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
