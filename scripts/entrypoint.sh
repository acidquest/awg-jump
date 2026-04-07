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
mkdir -p /var/log/supervisor
mkdir -p /var/run/amneziawg

# ── 4. Применить миграции БД ─────────────────────────────────────────────
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
import ipaddress
from backend.config import settings
from backend.models.dns_domain import DnsDomain, DnsUpstream


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
                address=settings.awg1_address,
                allowed_ips=settings.awg1_allowed_ips,
                persistent_keepalive=settings.awg1_persistent_keepalive,
                enabled=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(awg1)
            print("[init] Created default interface: awg1")

        # ── GeoIP источник ───────────────────────────────────────────────
        result = await session.execute(select(GeoipSource))
        geoip = result.scalar_one_or_none()
        if not geoip:
            geoip = GeoipSource(
                name="ipdeny.com RU",
                display_name="Russia",
                url=settings.geoip_source_ru,
                country_code="ru",
                ipset_name="geoip_local",
                enabled=True,
                created_at=datetime.now(timezone.utc),
            )
            session.add(geoip)
            print("[init] Created default GeoIP source: ipdeny.com RU")

        # ── Дефолтные DNS домены для split DNS ───────────────────────────
        result = await session.execute(select(DnsDomain))
        existing_count = len(result.scalars().all())
        if existing_count == 0:
            for domain in _DEFAULT_DNS_DOMAINS:
                session.add(DnsDomain(
                    domain=domain,
                    upstream=DnsUpstream.yandex,
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
