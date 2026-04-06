#!/bin/bash
set -e

echo "[entrypoint] AWG Jump Server starting..."

# ── 1. Переключить на legacy iptables (совместимость) ────────────────────
echo "[entrypoint] Configuring iptables-legacy..."
update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true

# ── 2. Создать необходимые директории ───────────────────────────────────
mkdir -p "${DATA_DIR:-/data}"
mkdir -p "${GEOIP_CACHE_DIR:-/data/geoip}"
mkdir -p "${BACKUP_DIR:-/data/backups}"
mkdir -p "${WG_CONFIG_DIR:-/data/wg_configs}"
mkdir -p /var/run/wireguard

# ── 3. Применить миграции БД ─────────────────────────────────────────────
echo "[entrypoint] Running database migrations..."
cd /app
python3 -m alembic -c backend/alembic.ini upgrade head
echo "[entrypoint] Migrations complete."

# ── 4. Инициализация дефолтных записей в БД (если пустая) ───────────────
echo "[entrypoint] Initializing default database records..."
python3 - << 'PYEOF'
import asyncio
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models.interface import Interface, InterfaceMode
from backend.models.geoip import GeoipSource
from backend.config import settings


async def init_defaults():
    async with AsyncSessionLocal() as session:
        # Проверить наличие awg0
        result = await session.execute(select(Interface).where(Interface.name == "awg0"))
        awg0 = result.scalar_one_or_none()
        if not awg0:
            awg0 = Interface(
                name="awg0",
                mode=InterfaceMode.server,
                listen_port=settings.awg0_listen_port,
                address=settings.awg0_address,
                dns=settings.awg0_dns,
                enabled=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(awg0)
            print("[init] Created default interface: awg0")

        # Проверить наличие awg1
        result = await session.execute(select(Interface).where(Interface.name == "awg1"))
        awg1 = result.scalar_one_or_none()
        if not awg1:
            awg1 = Interface(
                name="awg1",
                mode=InterfaceMode.client,
                address=settings.awg1_address,
                allowed_ips=settings.awg1_allowed_ips,
                persistent_keepalive=settings.awg1_persistent_keepalive,
                enabled=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(awg1)
            print("[init] Created default interface: awg1")

        # Проверить наличие GeoIP источника
        result = await session.execute(select(GeoipSource))
        geoip = result.scalar_one_or_none()
        if not geoip:
            geoip = GeoipSource(
                name="ipdeny.com RU",
                url=settings.geoip_source_ru,
                country_code="ru",
                ipset_name="geoip_ru",
                enabled=True,
                created_at=datetime.now(timezone.utc),
            )
            session.add(geoip)
            print("[init] Created default GeoIP source: ipdeny.com RU")

        await session.commit()
        print("[init] Database initialization complete.")


asyncio.run(init_defaults())
PYEOF

# ── 5. Запустить supervisor (управляет uvicorn) ──────────────────────────
echo "[entrypoint] Starting supervisor..."
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
