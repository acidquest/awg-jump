"""
Общие фикстуры для тестов.

Используют in-memory SQLite и мокают системные вызовы
(wg, iptables, ipset, amneziawg-go) — всё тестируется без root.
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from backend.database import Base, get_db
from backend.models.interface import Interface, InterfaceMode
from backend.models.geoip import GeoipSource


# ── In-memory БД ─────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _create_test_db() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSessionLocal() as session:
        # Дефолтные интерфейсы
        session.add(Interface(
            name="awg0",
            mode=InterfaceMode.server,
            private_key="aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8hISE=",
            public_key="dGVzdHB1YmxpY2tleWZvcmF3ZzAxMjM0NTY3ODk=",
            listen_port=51820,
            address="10.10.0.1/24",
            dns="1.1.1.1",
            enabled=True,
            obf_jc=7, obf_jmin=50, obf_jmax=90,
            obf_s1=83, obf_s2=47, obf_s3=121, obf_s4=33,
            obf_h1=3928541027, obf_h2=1847392610,
            obf_h3=2938471056, obf_h4=847392015,
            obf_generated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(Interface(
            name="awg1",
            mode=InterfaceMode.client,
            private_key="aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8hIiM=",
            public_key="dGVzdHB1YmxpY2tleWZvcmF3ZzExMjM0NTY3ODk=",
            address="10.20.0.2/32",
            allowed_ips="0.0.0.0/0",
            persistent_keepalive=25,
            enabled=True,
            obf_jc=5, obf_jmin=45, obf_jmax=85,
            obf_s1=70, obf_s2=90, obf_s3=110, obf_s4=55,
            obf_h1=111111111, obf_h2=222222222,
            obf_h3=333333333, obf_h4=444444444,
            obf_generated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        session.add(GeoipSource(
            name="ipdeny.com RU",
            display_name="Russia",
            url="https://www.ipdeny.com/ipblocks/data/countries/ru.zone",
            country_code="ru",
            ipset_name="geoip_local",
            enabled=True,
            prefix_count=100,
            last_updated=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
        await session.commit()


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    await _create_test_db()
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP-клиент с переопределённой БД и заглушками системных вызовов."""
    from backend.main import app

    # Переопределить dependency get_db
    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Мокаем системные вызовы
    with (
        patch("backend.services.awg._awg_processes", {}),
        patch("backend.services.awg.subprocess.check_output",
              side_effect=lambda args, **kw: b"fake_key_base64==\n"),
        patch("backend.services.awg._run_cmd", return_value=(0, "")),
        patch("backend.services.awg._wait_for_socket", return_value=True),
        patch("backend.services.ipset_manager._run", return_value=(0, "")),
        patch("backend.services.routing._run", return_value=(0, "")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Возвращает заголовок Authorization с валидным токеном."""
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "changeme"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
