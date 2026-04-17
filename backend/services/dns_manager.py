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
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.protected_dns import (
    dnsmasq_target,
    status as protected_dns_status,
    stop_all as stop_protected_dns,
    sync as sync_protected_dns,
)

logger = logging.getLogger(__name__)

_CONF_FILE = "/etc/dnsmasq-awg.conf"
_PID_FILE = "/var/run/dnsmasq-awg.pid"
_STARTUP_WAIT_SECONDS = 2.0
_PROCESS: Optional[subprocess.Popen] = None

_DEFAULT_ZONE_SETTINGS = {
    "local": {
        "name": "Local",
        "dns_servers": ["77.88.8.8"],
        "description": "",
        "is_builtin": True,
        "protocol": "plain",
    },
    "vpn": {
        "name": "Upstream",
        "dns_servers": ["1.1.1.1", "8.8.8.8"],
        "description": "",
        "is_builtin": True,
        "protocol": "plain",
    },
}

_HOSTNAME_REGEX = re.compile(r"^(?=.{1,253}$)(?!-)(?:[a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}\.?$", re.IGNORECASE)


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


def is_valid_dns_server(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return bool(_HOSTNAME_REGEX.match(candidate))


def _normalize_dns_server(value: str) -> str:
    candidate = value.strip()
    if not is_valid_dns_server(candidate):
        raise ValueError(f"Invalid DNS server: {value}")
    return candidate.rstrip(".").lower()


def _zone_targets(zone: dict) -> list[str]:
    return dnsmasq_target(zone["protocol"], list(zone["dns_servers"]))


def _zone_payload(zone) -> dict:
    try:
        dns_servers = json.loads(zone.dns_servers)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid dns_servers JSON for zone={zone.zone!r}") from exc
    if not isinstance(dns_servers, list) or not all(isinstance(item, str) for item in dns_servers):
        raise RuntimeError(f"Invalid dns_servers payload for zone={zone.zone!r}")
    return {
        "zone": zone.zone,
        "name": zone.name,
        "protocol": getattr(zone, "protocol", "plain") or "plain",
        "dns_servers": dns_servers,
        "endpoint_host": getattr(zone, "endpoint_host", "") or "",
        "endpoint_port": getattr(zone, "endpoint_port", None),
        "endpoint_url": getattr(zone, "endpoint_url", "") or "",
        "bootstrap_address": getattr(zone, "bootstrap_address", "") or "",
    }


def _write_config(domains: list, zones_by_key: dict[str, dict], manual_addresses: list | None = None) -> None:
    """Генерирует конфиг dnsmasq из списка доменов и DNS-серверов зон."""
    listen_ip = get_awg0_ip()
    vpn_dns = _zone_targets(zones_by_key.get("vpn", {"protocol": "plain", "dns_servers": []}))

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

    special_domains = [d for d in domains if d.enabled and getattr(d, "upstream", "") != "vpn"]
    if special_domains:
        lines.append("")
        lines.append("# Special zone overrides")
        for d in sorted(special_domains, key=lambda x: (getattr(x, "upstream", ""), x.domain)):
            zone_key = getattr(d, "upstream", "")
            zone = zones_by_key.get(zone_key)
            zone_dns = _zone_targets(zone) if zone else []
            if not zone_dns:
                continue
            dnsmasq_domain = _to_dnsmasq_domain(d.domain)
            for dns in zone_dns:
                lines.append(f"server=/{dnsmasq_domain}/{dns}")

    manual_rules = [item for item in (manual_addresses or []) if item.enabled]
    if manual_rules:
        lines.append("")
        lines.append("# Manual replace addresses")
        for item in sorted(manual_rules, key=lambda x: x.domain):
            dnsmasq_domain = _to_dnsmasq_domain(item.domain)
            lines.append(f"address=/{dnsmasq_domain}/{item.address}")

    Path(_CONF_FILE).write_text("\n".join(lines) + "\n")
    logger.info(
        "dnsmasq config written: listen=%s, override_domains=%d, manual_addresses=%d, zones=%s",
        listen_ip, len(special_domains), len(manual_rules), sorted(zones_by_key),
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
                    name=payload["name"],
                    dns_servers=json.dumps(payload["dns_servers"]),
                    description=payload["description"],
                    is_builtin=payload["is_builtin"],
                    protocol=payload["protocol"],
                )
            )
            changed = True

    if changed:
        await db.commit()


async def get_zones(db: AsyncSession):
    from backend.models.dns_zone_settings import DnsZoneSettings

    await _ensure_zone_settings(db)
    result = await db.execute(select(DnsZoneSettings).order_by(DnsZoneSettings.zone))
    return result.scalars().all()


async def get_zone_dns(db: AsyncSession, zone: str) -> list[str]:
    row = next((item for item in await get_zones(db) if item.zone == zone), None)
    if row is None:
        raise RuntimeError(f"DNS zone settings not found for zone={zone!r}")
    return _zone_payload(row)["dns_servers"]


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
        _write_config(
            [],
            {
                zone: {
                    "zone": zone,
                    "protocol": payload["protocol"],
                    "dns_servers": payload["dns_servers"],
                }
                for zone, payload in _DEFAULT_ZONE_SETTINGS.items()
            },
        )

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
    """Применяет новый конфиг dnsmasq через полный restart процесса."""
    if is_running():
        logger.info("restarting dnsmasq to apply updated config")
        stop()
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
    stop_protected_dns()


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
    from backend.models.dns_manual_address import DnsManualAddress

    zones = await get_zones(db)
    zone_payloads = [_zone_payload(zone) for zone in zones]
    sync_protected_dns(zone_payloads)
    zones_by_key = {zone["zone"]: zone for zone in zone_payloads}

    result = await db.execute(
        select(DnsDomain).order_by(DnsDomain.domain)
    )
    domains = result.scalars().all()

    result = await db.execute(
        select(DnsManualAddress).order_by(DnsManualAddress.domain)
    )
    manual_addresses = result.scalars().all()

    _write_config(domains, zones_by_key, manual_addresses)

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
        **protected_dns_status(),
    }
