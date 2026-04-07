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
(fwmark по geoip_ru ipset, как и для клиентского трафика).
"""
import ipaddress
import logging
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONF_FILE = "/etc/dnsmasq-awg.conf"
_PID_FILE = "/var/run/dnsmasq-awg.pid"

_YANDEX_DNS = "77.88.8.8"
_DEFAULT_DNS = ["1.1.1.1", "8.8.8.8"]


def get_awg0_ip() -> str:
    """Извлекает IP-адрес awg0 из настройки awg0_address (напр. '10.10.0.1/24' → '10.10.0.1')."""
    from backend.config import settings
    try:
        return str(ipaddress.ip_interface(settings.awg0_address).ip)
    except Exception:
        return settings.awg0_address.split("/")[0]


def _write_config(domains: list) -> None:
    """Генерирует конфиг dnsmasq из списка доменов."""
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
        "# Default upstreams (non-RU traffic → VPN)",
    ]
    for dns in _DEFAULT_DNS:
        lines.append(f"server={dns}")

    yandex_domains = [d for d in domains if d.enabled and d.upstream == "yandex"]
    if yandex_domains:
        lines.append("")
        lines.append(f"# RU domains → Yandex DNS ({_YANDEX_DNS})")
        for d in sorted(yandex_domains, key=lambda x: x.domain):
            lines.append(f"server=/{d.domain}/{_YANDEX_DNS}")

    Path(_CONF_FILE).write_text("\n".join(lines) + "\n")
    logger.info(
        "dnsmasq config written: listen=%s, RU domains=%d",
        listen_ip, len(yandex_domains),
    )


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
        _write_config([])

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


def reload() -> None:
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


async def apply_from_db() -> None:
    """
    Читает домены из БД, генерирует конфиг dnsmasq и перезагружает его.
    Открывает собственную DB-сессию — безопасно вызывать из любого контекста.
    """
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.dns_domain import DnsDomain

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DnsDomain).order_by(DnsDomain.domain)
        )
        domains = result.scalars().all()

    _write_config(domains)

    if is_running():
        reload()
    else:
        start()


def get_status() -> dict:
    """Статус dnsmasq и текущей конфигурации."""
    return {
        "running": is_running(),
        "pid": _get_pid() if is_running() else None,
        "listen_ip": get_awg0_ip(),
        "conf_file": _CONF_FILE,
        "yandex_dns": _YANDEX_DNS,
        "default_dns": _DEFAULT_DNS,
    }
