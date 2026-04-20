import asyncio
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.services.maintenance import wait_until_ready


DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"
METRICS_DATABASE_URL = f"sqlite+aiosqlite:///{settings.metrics_db_path}"


def _create_engine(database_url: str):
    return create_async_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )


engine = _create_engine(DATABASE_URL)
metrics_engine = _create_engine(METRICS_DATABASE_URL)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


@event.listens_for(metrics_engine.sync_engine, "connect")
def set_metrics_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

MetricsSessionLocal = async_sessionmaker(
    metrics_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

main_write_lock = asyncio.Lock()
metrics_write_lock = asyncio.Lock()


class Base(DeclarativeBase):
    pass


def prepare_session(session: AsyncSession, *, metrics: bool = False) -> AsyncSession:
    if session.info.get("write_lock_prepared"):
        return session

    lock = metrics_write_lock if metrics else main_write_lock
    original_flush = session.flush

    async def locked_flush(*args, **kwargs):
        async with lock:
            try:
                return await original_flush(*args, **kwargs)
            except Exception:
                await session.rollback()
                raise

    session.flush = locked_flush  # type: ignore[method-assign]
    session.info["write_lock_prepared"] = True
    return session


def _should_commit_request(request: Request) -> bool:
    return request.method.upper() not in {"GET", "HEAD", "OPTIONS"}


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    await wait_until_ready()
    async with AsyncSessionLocal() as session:
        prepare_session(session)
        try:
            yield session
            if _should_commit_request(request) and (session.dirty or session.new or session.deleted):
                await commit_with_lock(session)
        except Exception:
            await session.rollback()
            raise


async def get_metrics_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    await wait_until_ready()
    async with MetricsSessionLocal() as session:
        prepare_session(session, metrics=True)
        try:
            yield session
            if _should_commit_request(request) and (session.dirty or session.new or session.deleted):
                await commit_with_lock(session, metrics=True)
        except Exception:
            await session.rollback()
            raise


async def commit_with_lock(session: AsyncSession, *, metrics: bool = False) -> None:
    if not hasattr(session, "commit"):
        return
    lock = metrics_write_lock if metrics else main_write_lock
    async with lock:
        try:
            await session.commit()
        except Exception:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise


async def flush_with_lock(session: AsyncSession, *, metrics: bool = False) -> None:
    if not hasattr(session, "flush"):
        return
    lock = metrics_write_lock if metrics else main_write_lock
    async with lock:
        try:
            await session.flush()
        except Exception:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise
