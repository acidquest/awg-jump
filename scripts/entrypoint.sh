#!/bin/bash
set -euo pipefail

echo "[entrypoint] AWG Jump Server starting..."

SUPERVISOR_PID=0

graceful_shutdown() {
    echo "[entrypoint] Received shutdown signal, stopping supervisor..."

    if command -v supervisorctl >/dev/null 2>&1; then
        supervisorctl -c /etc/supervisor/supervisord.conf stop all >/dev/null 2>&1 || true
        supervisorctl -c /etc/supervisor/supervisord.conf shutdown >/dev/null 2>&1 || true
    fi

    if [ "${SUPERVISOR_PID}" -gt 0 ] && kill -0 "${SUPERVISOR_PID}" 2>/dev/null; then
        wait "${SUPERVISOR_PID}" || true
    fi
}

trap graceful_shutdown TERM INT

# ── 1. Переключить на legacy iptables (совместимость) ────────────────────
echo "[entrypoint] Configuring iptables-legacy..."
update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true

# ── 2. Создать /dev/net/tun если отсутствует ────────────────────────────
if [ ! -c /dev/net/tun ]; then
    echo "[entrypoint] Creating /dev/net/tun..."
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200
    chmod 666 /dev/net/tun
fi

# ── 3. Создать необходимые директории ───────────────────────────────────
mkdir -p "${DATA_DIR:-/data}"
mkdir -p "${GEOIP_CACHE_DIR:-/data/geoip}"
mkdir -p "${BACKUP_DIR:-/data/backups}"
mkdir -p "${WG_CONFIG_DIR:-/data/wg_configs}"
mkdir -p "${CERTS_DIR:-/data/certs}"
mkdir -p "${TELEMT_DIR:-/data/telemt}/tlsfront"
mkdir -p /var/log/supervisor
mkdir -p /var/run/amneziawg

# ── 3b. Сгенерировать TLS сертификат если отсутствует ───────────────────
if [ ! -f "${TLS_CERT_PATH:-/data/certs/server.crt}" ] || [ ! -f "${TLS_KEY_PATH:-/data/certs/server.key}" ]; then
    echo "[entrypoint] Generating self-signed TLS certificate..."
    CERT_DIR="${CERTS_DIR:-/data/certs}" /app/nginx/generate-cert.sh
fi

# ── 4. Применить миграции БД ─────────────────────────────────────────────
echo "[entrypoint] Running database migrations..."
cd /app
python3 - << 'PYEOF'
import os
import sqlite3

db_path = os.environ.get("DB_PATH", "/data/config.db")
legacy_revisions = {"0002", "0003", "0004", "0005", "0006", "0007"}

if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        )
        if cur.fetchone():
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            row = cur.fetchone()
            if row and row[0] in legacy_revisions:
                print(
                    "[entrypoint] Normalizing legacy alembic revision "
                    f"{row[0]} -> 0001 for baseline compatibility..."
                )
                cur.execute("UPDATE alembic_version SET version_num = '0001'")
                conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[entrypoint] Could not normalize alembic revision: {exc}")
        raise
PYEOF
python3 -m alembic -c backend/alembic.ini upgrade head
echo "[entrypoint] Migrations complete."

# ── 4b. Досоздать отсутствующие таблицы SQLAlchemy metadata ─────────────
# Alembic покрывает основной путь миграций, но в тестовых/переходных SQLite
# базах может отсутствовать часть таблиц из более новых подсистем. До
# init_defaults гарантируем, что metadata полностью материализована.
echo "[entrypoint] Ensuring SQLAlchemy tables exist..."
python3 - << 'PYEOF'
import asyncio
import sys
sys.path.insert(0, '/app')

import backend.models  # noqa: F401 - регистрирует все таблицы в metadata
from sqlalchemy import text
from backend.database import Base, engine


async def ensure_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        result = await conn.execute(text("PRAGMA table_info(interfaces)"))
        interface_columns = {row[1] for row in result.fetchall()}
        if "protocol" not in interface_columns:
            await conn.execute(text("ALTER TABLE interfaces ADD COLUMN protocol VARCHAR(16) NOT NULL DEFAULT 'awg'"))
        await conn.execute(
            text(
                """
                UPDATE interfaces
                SET protocol = CASE
                    WHEN name = 'wg0' THEN 'wg'
                    WHEN protocol IS NULL OR protocol = '' THEN 'awg'
                    ELSE protocol
                END
                """
            )
        )


asyncio.run(ensure_tables())
print("[entrypoint] SQLAlchemy metadata sync complete.")
PYEOF

# ── 4. Инициализация дефолтных записей в БД (если пустая) ───────────────
echo "[entrypoint] Initializing default database records..."
python3 - << 'PYEOF'
import asyncio
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models.interface import Interface, InterfaceMode, InterfaceProtocol
from backend.models.geoip import GeoipSource
import ipaddress
from backend.config import classic_wg_enabled, settings
from backend.models.dns_domain import DnsDomain


def _awg0_ip(address: str) -> str:
    """Извлекает IP из CIDR-адреса (напр. '10.10.0.1/24' → '10.10.0.1')."""
    try:
        return str(ipaddress.ip_interface(address).ip)
    except Exception:
        return address.split('/')[0]


# Дефолтные RU-домены для split DNS
_DEFAULT_DNS_DOMAINS = [
    "ru", "рф",
    "yandex.ru", "yandex.net", "yandex.com", "ya.ru",
    "vk.com", "vk.ru", "vkontakte.ru",
    "mail.ru", "list.ru", "inbox.ru", "bk.ru",
    "ok.ru",
    "rambler.ru",
    "sberbank.ru", "sbrf.ru", "sber.ru",
    "gosuslugi.ru",
    "mos.ru",
    "tinkoff.ru",
    "avito.ru",
    "ozon.ru",
    "wildberries.ru",
]


async def init_defaults():
    async with AsyncSessionLocal() as session:
        # ── awg0 ─────────────────────────────────────────────────────────
        result = await session.execute(select(Interface).where(Interface.name == "awg0"))
        awg0 = result.scalar_one_or_none()

        # IP awg0 используется как DNS для клиентов (split DNS через dnsmasq)
        awg0_ip = _awg0_ip(settings.awg0_address)

        if not awg0:
            awg0 = Interface(
                name="awg0",
                mode=InterfaceMode.server,
                protocol=InterfaceProtocol.awg,
                listen_port=settings.awg0_listen_port,
                address=settings.awg0_address,
                dns=awg0_ip,   # dnsmasq слушает на этом IP
                enabled=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(awg0)
            print(f"[init] Created default interface: awg0 (dns={awg0_ip})")
        else:
            changed = False
            if awg0.protocol != InterfaceProtocol.awg:
                awg0.protocol = InterfaceProtocol.awg
                changed = True
            if awg0.listen_port != settings.awg0_listen_port:
                awg0.listen_port = settings.awg0_listen_port
                changed = True
                print(f"[init] Updated awg0 listen_port → {settings.awg0_listen_port}")
            # Обновить DNS на awg0 IP если ещё не установлен (переход со старой версии)
            if awg0.dns != awg0_ip:
                awg0.dns = awg0_ip
                changed = True
                print(f"[init] Updated awg0 DNS → {awg0_ip} (split DNS)")
            if changed:
                awg0.updated_at = datetime.now(timezone.utc)
                session.add(awg0)

        # ── awg1 ─────────────────────────────────────────────────────────
        result = await session.execute(select(Interface).where(Interface.name == "awg1"))
        awg1 = result.scalar_one_or_none()
        if not awg1:
            awg1 = Interface(
                name="awg1",
                mode=InterfaceMode.client,
                protocol=InterfaceProtocol.awg,
                address=settings.awg1_address,
                allowed_ips=settings.awg1_allowed_ips,
                persistent_keepalive=settings.awg1_persistent_keepalive,
                enabled=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(awg1)
            print("[init] Created default interface: awg1")
        elif awg1.protocol != InterfaceProtocol.awg:
            awg1.protocol = InterfaceProtocol.awg
            session.add(awg1)

        # ── wg0 (classic wireguard server) ─────────────────────────────
        result = await session.execute(select(Interface).where(Interface.name == "wg0"))
        wg0 = result.scalar_one_or_none()
        if classic_wg_enabled():
            wg0_dns = settings.wg0_dns or _awg0_ip(settings.wg0_address)
            if not settings.wg0_listen_port:
                print("[init] CLASSIC_WG=on but WG0_LISTEN_PORT is empty; wg0 will stay disabled")
            if not wg0:
                wg0 = Interface(
                    name="wg0",
                    mode=InterfaceMode.server,
                    protocol=InterfaceProtocol.wg,
                    listen_port=settings.wg0_listen_port,
                    address=settings.wg0_address,
                    dns=wg0_dns,
                    enabled=bool(settings.wg0_listen_port),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(wg0)
                print("[init] Created optional interface: wg0")
            else:
                changed = False
                if wg0.protocol != InterfaceProtocol.wg:
                    wg0.protocol = InterfaceProtocol.wg
                    changed = True
                if wg0.listen_port != settings.wg0_listen_port:
                    wg0.listen_port = settings.wg0_listen_port
                    changed = True
                if wg0.address != settings.wg0_address:
                    wg0.address = settings.wg0_address
                    changed = True
                if wg0.dns != wg0_dns:
                    wg0.dns = wg0_dns
                    changed = True
                desired_enabled = bool(settings.wg0_listen_port)
                if wg0.enabled != desired_enabled:
                    wg0.enabled = desired_enabled
                    changed = True
                if changed:
                    wg0.updated_at = datetime.now(timezone.utc)
                    session.add(wg0)
        elif wg0 and wg0.enabled:
            wg0.enabled = False
            wg0.updated_at = datetime.now(timezone.utc)
            session.add(wg0)
            print("[init] Disabled hidden interface: wg0")

        # ── GeoIP источники ──────────────────────────────────────────────
        # По умолчанию не создаём преднастроенную страну: local zone
        # настраивается пользователем через UI/API после первого старта.

        # ── Дефолтные DNS домены для split DNS ───────────────────────────
        result = await session.execute(select(DnsDomain))
        existing_count = len(result.scalars().all())
        if existing_count == 0:
            for domain in _DEFAULT_DNS_DOMAINS:
                session.add(DnsDomain(
                    domain=domain,
                    upstream="local",
                    enabled=True,
                    created_at=datetime.now(timezone.utc),
                ))
            print(f"[init] Created {len(_DEFAULT_DNS_DOMAINS)} default split DNS domains")

        await session.commit()
        print("[init] Database initialization complete.")


asyncio.run(init_defaults())
PYEOF

# ── 5. Запустить supervisor (uvicorn → lifespan: interfaces → geoip → routing → scheduler)
echo "[entrypoint] Starting supervisor..."
/usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf &
SUPERVISOR_PID=$!
wait "${SUPERVISOR_PID}"
