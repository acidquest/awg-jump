"""
Split DNS manager — управление dnsmasq для политики разрешения имён.

Политика:
  Домены из таблицы dns_domains (upstream=yandex)  →  77.88.8.8 (Яндекс DNS)
  Все остальные домены                              →  1.1.1.1 / 8.8.8.8

dnsmasq слушает на:
  - awg0 IP (для клиентов VPN)
  - 127.0.0.1 (для контейнера)

Контейнер получает разделённый DNS автоматически через /etc/resolv.conf → 127.0.0.1.
Маршрутизация DNS-трафика контейнера обеспечивается iptables mangle OUTPUT
(fwmark по geoip_local ipset, как и для клиентского трафика).
"""
import ipaddress
import json
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_CONF_FILE = "/etc/dnsmasq-awg.conf"
_PID_FILE = "/var/run/dnsmasq-awg.pid"

_DEFAULT_ZONE_SETTINGS = {
    "local": {
        "dns_servers": ["77.88.8.8"],
        "description": "DNS for local routing zone (RU/etc)",
    },
    "vpn": {
        "dns_servers": ["1.1.1.1", "8.8.8.8"],
        "description": "DNS for VPN routing zone",
    },
}


def get_awg0_ip() -> str:
    """Извлекает IP-адрес awg0 из настройки awg0_address (напр. '10.10.0.1/24' → '10.10.0.1')."""
    from backend.config import settings
    try:
        return str(ipaddress.ip_interface(settings.awg0_address).ip)
    except Exception:
        return settings.awg0_address.split("/")[0]


def _write_config(domains: list, local_dns: list[str], vpn_dns: list[str]) -> None:
    """Генерирует конфиг dnsmasq из списка доменов и DNS-серверов зон."""
    listen_ip = get_awg0_ip()

    lines = [
        "# AWG Split DNS — auto-generated, do not edit manually",
        f"listen-address={listen_ip},127.0.0.1",
        "bind-interfaces",
        "no-resolv",
        "no-hosts",
        "cache-size=2000",
        "neg-ttl=60",
        "local-ttl=60",
        "dns-forward-max=150",
        "",
        "# Default upstreams (VPN zone)",
    ]
    for dns in vpn_dns:
        lines.append(f"server={dns}")

    local_domains = [d for d in domains if d.enabled and getattr(d.upstream, "value", d.upstream) == "yandex"]
    if local_domains:
        lines.append("")
        lines.append(f"# Local zone domains -> {', '.join(local_dns)}")
        for d in sorted(local_domains, key=lambda x: x.domain):
            for dns in local_dns:
                lines.append(f"server=/{d.domain}/{dns}")

    Path(_CONF_FILE).write_text("\n".join(lines) + "\n")
    logger.info(
        "dnsmasq config written: listen=%s, local_domains=%d, local_dns=%s, vpn_dns=%s",
        listen_ip, len(local_domains), local_dns, vpn_dns,
    )


async def _ensure_zone_settings(db: AsyncSession) -> None:
    from backend.models.dns_zone_settings import DnsZoneSettings

    changed = False
    for zone, payload in _DEFAULT_ZONE_SETTINGS.items():
        existing = await db.scalar(
            select(DnsZoneSettings).where(DnsZoneSettings.zone == zone)
        )
        if existing is None:
            db.add(
                DnsZoneSettings(
                    zone=zone,
                    dns_servers=json.dumps(payload["dns_servers"]),
                    description=payload["description"],
                )
            )
            changed = True

    if changed:
        await db.commit()


async def get_zone_dns(db: AsyncSession, zone: str) -> list[str]:
    from backend.models.dns_zone_settings import DnsZoneSettings

    await _ensure_zone_settings(db)
    row = await db.scalar(
        select(DnsZoneSettings).where(DnsZoneSettings.zone == zone)
    )
    if row is None:
        raise RuntimeError(f"DNS zone settings not found for zone={zone!r}")

    try:
        dns_servers = json.loads(row.dns_servers)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid dns_servers JSON for zone={zone!r}") from exc

    if not isinstance(dns_servers, list) or not dns_servers or not all(isinstance(x, str) for x in dns_servers):
        raise RuntimeError(f"Invalid dns_servers payload for zone={zone!r}")

    return dns_servers


def is_running() -> bool:
    """Проверяет, запущен ли dnsmasq."""
    if not os.path.exists(_PID_FILE):
        return False
    try:
        pid = int(Path(_PID_FILE).read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def _get_pid() -> Optional[int]:
    try:
        return int(Path(_PID_FILE).read_text().strip())
    except Exception:
        return None


def start() -> None:
    """Запускает dnsmasq (если не запущен)."""
    if is_running():
        logger.debug("dnsmasq already running (pid=%s)", _get_pid())
        return

    if not Path(_CONF_FILE).exists():
        # Минимальный конфиг если вызвали до apply_from_db
        _write_config([], _DEFAULT_ZONE_SETTINGS["local"]["dns_servers"], _DEFAULT_ZONE_SETTINGS["vpn"]["dns_servers"])

    cmd = [
        "dnsmasq",
        f"--conf-file={_CONF_FILE}",
        f"--pid-file={_PID_FILE}",
        "--log-facility=-",   # логи в stderr/stdout
        "--log-async=5",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"dnsmasq start failed: {result.stderr.strip()}")
    logger.info("dnsmasq started (listen=%s,127.0.0.1)", get_awg0_ip())

    _patch_resolv_conf()


def _reload_process() -> None:
    """Перечитывает конфиг dnsmasq без перезапуска (SIGHUP)."""
    pid = _get_pid()
    if pid is None or not is_running():
        logger.warning("dnsmasq not running, starting instead of reload")
        start()
        return
    try:
        os.kill(pid, signal.SIGHUP)
        logger.info("dnsmasq config reloaded via SIGHUP (pid=%d)", pid)
    except OSError as e:
        logger.warning("dnsmasq SIGHUP failed: %s, restarting", e)
        start()


def stop() -> None:
    """Останавливает dnsmasq."""
    pid = _get_pid()
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("dnsmasq stopped (pid=%d)", pid)
    except OSError:
        pass
    try:
        os.unlink(_PID_FILE)
    except OSError:
        pass


def _patch_resolv_conf() -> None:
    """Перенаправляет DNS контейнера на локальный dnsmasq."""
    try:
        with open("/etc/resolv.conf", "w") as f:
            f.write("# Managed by AWG Split DNS\n")
            f.write("nameserver 127.0.0.1\n")
        logger.info("resolv.conf patched: nameserver 127.0.0.1")
    except Exception as e:
        logger.warning("Could not patch resolv.conf: %s", e)


async def reload(db: AsyncSession) -> None:
    """Читает настройки и домены из БД, генерирует конфиг dnsmasq и перезагружает сервис."""
    from backend.models.dns_domain import DnsDomain

    local_dns = await get_zone_dns(db, "local")
    vpn_dns = await get_zone_dns(db, "vpn")

    result = await db.execute(
        select(DnsDomain).order_by(DnsDomain.domain)
    )
    domains = result.scalars().all()

    _write_config(domains, local_dns, vpn_dns)

    if is_running():
        _reload_process()
    else:
        start()


async def apply_from_db() -> None:
    """Совместимый helper: открывает свою DB-сессию и вызывает reload()."""
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await reload(session)


def get_status() -> dict:
    """Статус dnsmasq и текущей конфигурации."""
    return {
        "running": is_running(),
        "pid": _get_pid() if is_running() else None,
        "listen_ip": get_awg0_ip(),
        "conf_file": _CONF_FILE,
        "local_zone_dns": _DEFAULT_ZONE_SETTINGS["local"]["dns_servers"],
        "vpn_zone_dns": _DEFAULT_ZONE_SETTINGS["vpn"]["dns_servers"],
    }
