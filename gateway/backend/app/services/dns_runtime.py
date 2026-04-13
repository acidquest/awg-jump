from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DnsDomainRule, DnsUpstream
from app.services.dns import build_dnsmasq_config


logger = logging.getLogger(__name__)
_DNS_PROCESS: subprocess.Popen | None = None
_DNS_LAST_ERROR: str | None = None


def config_path() -> Path:
    return Path(settings.dns_runtime_dir) / "dnsmasq.conf"


def pid_path() -> Path:
    return Path(settings.runtime_dir) / "dnsmasq.pid"


def is_running() -> bool:
    return _DNS_PROCESS is not None and _DNS_PROCESS.poll() is None


def status() -> dict:
    return {
        "running": is_running(),
        "pid": _DNS_PROCESS.pid if is_running() and _DNS_PROCESS is not None else None,
        "config_path": str(config_path()),
        "pid_path": str(pid_path()),
        "last_error": _DNS_LAST_ERROR,
    }


async def render_runtime_config(db: AsyncSession) -> str:
    upstreams = (await db.execute(select(DnsUpstream).order_by(DnsUpstream.zone))).scalars().all()
    rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    return build_dnsmasq_config(upstreams, rules)


async def restart_dnsmasq(db: AsyncSession) -> dict:
    global _DNS_PROCESS, _DNS_LAST_ERROR
    stop_dnsmasq()
    config_path().write_text(await render_runtime_config(db), encoding="utf-8")
    proc = subprocess.Popen(
        [
            "dnsmasq",
            "--keep-in-foreground",
            f"--conf-file={config_path()}",
            f"--pid-file={pid_path()}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.2)
    if proc.poll() is not None:
        stderr = (proc.stderr.read() or "").strip() if proc.stderr else ""
        _DNS_LAST_ERROR = stderr or f"dnsmasq exited with code {proc.returncode}"
        _DNS_PROCESS = None
        logger.error("[gateway-dns] dnsmasq failed to start: %s", _DNS_LAST_ERROR)
        raise RuntimeError(_DNS_LAST_ERROR)
    _DNS_PROCESS = proc
    _DNS_LAST_ERROR = None
    logger.info("[gateway-dns] dnsmasq started pid=%s config=%s", proc.pid, config_path())
    return status()


def stop_dnsmasq() -> None:
    global _DNS_PROCESS
    if _DNS_PROCESS is None:
        return
    if _DNS_PROCESS.poll() is None:
        _DNS_PROCESS.terminate()
        try:
            _DNS_PROCESS.wait(timeout=3)
        except Exception:
            _DNS_PROCESS.kill()
    _DNS_PROCESS = None
