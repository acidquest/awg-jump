from __future__ import annotations

import logging
import os
import pwd
import signal
import subprocess
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DnsDomainRule, DnsManualAddress, DnsUpstream, GatewaySettings, RoutingPolicy
from app.services.dns import build_dnsmasq_config
from app.services.external_ip import effective_fqdn_prefixes
from app.services.nftables_manager import TABLE_NAME as NFT_TABLE_NAME
from app.services.routing import firewall_backend, fqdn_ipset_name


logger = logging.getLogger(__name__)
_DNS_PROCESS: subprocess.Popen | None = None
_DNS_LAST_ERROR: str | None = None
_DNS_RUNTIME_USER = "nobody"


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
        "runtime_user": _DNS_RUNTIME_USER,
    }


def runtime_uid() -> int | None:
    try:
        return pwd.getpwnam(_DNS_RUNTIME_USER).pw_uid
    except KeyError:
        return None


async def render_runtime_config(db: AsyncSession) -> str:
    upstreams = (await db.execute(select(DnsUpstream).order_by(DnsUpstream.zone))).scalars().all()
    rules = (await db.execute(select(DnsDomainRule).order_by(DnsDomainRule.domain))).scalars().all()
    manual_addresses = (await db.execute(select(DnsManualAddress).order_by(DnsManualAddress.domain))).scalars().all()
    policy = await db.get(RoutingPolicy, 1)
    gateway_settings = await db.get(GatewaySettings, 1)
    fqdn_prefixes = effective_fqdn_prefixes(policy, gateway_settings)
    ipset_name = fqdn_ipset_name(policy) if policy else "routing_prefixes_fqdn"
    return build_dnsmasq_config(
        upstreams,
        rules,
        manual_addresses=manual_addresses,
        fqdn_prefixes=fqdn_prefixes,
        ipset_name=ipset_name,
        use_nftset=firewall_backend(gateway_settings) == "nftables",
        nft_table_name=NFT_TABLE_NAME,
    )


async def restart_dnsmasq(db: AsyncSession) -> dict:
    global _DNS_PROCESS, _DNS_LAST_ERROR
    stop_dnsmasq()
    config_path().write_text(await render_runtime_config(db), encoding="utf-8")
    proc = subprocess.Popen(
        [
            "dnsmasq",
            "--keep-in-foreground",
            f"--user={_DNS_RUNTIME_USER}",
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


def _read_pidfile() -> int | None:
    try:
        raw_value = pid_path().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("[gateway-dns] invalid pid file contents: %r", raw_value)
        return None


def _remove_pidfile() -> None:
    try:
        pid_path().unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("[gateway-dns] failed to remove pid file %s: %s", pid_path(), exc)


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_pidfile()
        return
    except OSError as exc:
        logger.warning("[gateway-dns] failed to terminate dnsmasq pid=%s: %s", pid, exc)
        return

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _remove_pidfile()
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        logger.warning("[gateway-dns] failed to kill dnsmasq pid=%s: %s", pid, exc)
        return
    _remove_pidfile()


def stop_dnsmasq() -> None:
    global _DNS_PROCESS
    if _DNS_PROCESS is None:
        pid = _read_pidfile()
        if pid is not None:
            _terminate_pid(pid)
        return
    if _DNS_PROCESS.poll() is None:
        _DNS_PROCESS.terminate()
        try:
            _DNS_PROCESS.wait(timeout=3)
        except Exception:
            _DNS_PROCESS.kill()
    _remove_pidfile()
    _DNS_PROCESS = None
