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
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_CONF_FILE = "/etc/dnsmasq-awg.conf"
_PID_FILE = "/var/run/dnsmasq-awg.pid"
_STARTUP_WAIT_SECONDS = 2.0
_PROCESS: Optional[subprocess.Popen] = None

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


def _to_dnsmasq_domain(domain: str) -> str:
    """Преобразует домен к ASCII-форме, совместимой с dnsmasq (IDNA/punycode)."""
    normalized = domain.strip().strip(".").lower()
    if not normalized:
        raise ValueError("Domain cannot be empty")
    return normalized.encode("idna").decode("ascii")


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
            dnsmasq_domain = _to_dnsmasq_domain(d.domain)
            for dns in local_dns:
                lines.append(f"server=/{dnsmasq_domain}/{dns}")

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
    global _PROCESS
    if _PROCESS is not None:
        if _PROCESS.poll() is None:
            return True
        _PROCESS = None

    if not os.path.exists(_PID_FILE):
        return False
    try:
        pid = int(Path(_PID_FILE).read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def _get_pid() -> Optional[int]:
    if _PROCESS is not None and _PROCESS.poll() is None:
        return _PROCESS.pid
    try:
        return int(Path(_PID_FILE).read_text().strip())
    except Exception:
        return None


def start() -> None:
    """Запускает dnsmasq (если не запущен)."""
    global _PROCESS
    if is_running():
        logger.debug("dnsmasq already running (pid=%s)", _get_pid())
        return

    if not Path(_CONF_FILE).exists():
        # Минимальный конфиг если вызвали до apply_from_db
        _write_config([], _DEFAULT_ZONE_SETTINGS["local"]["dns_servers"], _DEFAULT_ZONE_SETTINGS["vpn"]["dns_servers"])

    test_cmd = [
        "dnsmasq",
        "--test",
        f"--conf-file={_CONF_FILE}",
    ]
    test_result = subprocess.run(test_cmd, capture_output=True, text=True)
    if test_result.returncode != 0:
        raise RuntimeError(f"dnsmasq config test failed: {test_result.stderr.strip()}")

    cmd = [
        "dnsmasq",
        "--keep-in-foreground",
        f"--conf-file={_CONF_FILE}",
        f"--pid-file={_PID_FILE}",
        "--log-facility=-",   # логи в stderr/stdout
        "--log-async=5",
    ]
    _PROCESS = subprocess.Popen(cmd)

    deadline = time.monotonic() + _STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _PROCESS.poll() is not None:
            raise RuntimeError(f"dnsmasq exited during startup with rc={_PROCESS.returncode}")
        if is_running():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("dnsmasq startup timed out")

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
    global _PROCESS
    pid = _get_pid()
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("dnsmasq stopped (pid=%d)", pid)
    except OSError:
        pass
    if _PROCESS is not None:
        try:
            _PROCESS.wait(timeout=2)
        except Exception:
            pass
        _PROCESS = None
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
