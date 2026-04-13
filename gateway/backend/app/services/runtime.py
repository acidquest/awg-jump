from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EntryNode, GatewaySettings, RuntimeMode, TunnelStatus


_PROCESS: subprocess.Popen | None = None
_KERNEL_MODE: bool | None = None
logger = logging.getLogger(__name__)


def _stream_process_logs(proc: subprocess.Popen, node_name: str) -> None:
    if proc.stderr is None:
        return
    for raw_line in iter(proc.stderr.readline, ""):
        line = raw_line.strip()
        if line:
            logger.info("[awg-runtime][%s][stderr] %s", node_name, line)
    logger.info("[awg-runtime][%s] stderr stream closed", node_name)


def _run_logged(args: list[str], *, context: str) -> subprocess.CompletedProcess[str]:
    logger.info("[awg-runtime] exec %s: %s", context, " ".join(args))
    result = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        logger.info("[awg-runtime] %s stdout: %s", context, stdout)
    if stderr:
        logger.info("[awg-runtime] %s stderr: %s", context, stderr)
    return result


def _run_check(args: list[str], *, context: str) -> tuple[int, str]:
    logger.info("[awg-runtime] exec %s: %s", context, " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if output:
        logger.info("[awg-runtime] %s output: %s", context, output)
    return proc.returncode, output


def _detect_kernel_support() -> bool:
    global _KERNEL_MODE
    if _KERNEL_MODE is not None:
        return _KERNEL_MODE
    rc, _ = _run_check(["ip", "link", "add", "awg_probe_gateway", "type", "amneziawg"], context="kernel-probe")
    if rc == 0:
        _run_check(["ip", "link", "delete", "awg_probe_gateway"], context="kernel-probe-cleanup")
        logger.info("[awg-runtime] kernel mode confirmed for AmneziaWG")
        _KERNEL_MODE = True
        return True
    logger.info("[awg-runtime] kernel mode unavailable, falling back to amneziawg-go userspace")
    _KERNEL_MODE = False
    return False


def _resolve_runtime_mode(requested_mode: str) -> bool:
    kernel_supported = _detect_kernel_support()
    if requested_mode == RuntimeMode.kernel.value:
        if not kernel_supported:
            raise RuntimeError("Kernel mode requested but AmneziaWG kernel interface is not available")
        return True
    if requested_mode == RuntimeMode.userspace.value:
        logger.info("[awg-runtime] userspace mode forced by settings")
        return False
    return kernel_supported


def _ensure_interface_absent(interface_name: str) -> None:
    _run_check(["ip", "link", "delete", interface_name], context="ip-link-delete")


def is_runtime_available() -> bool:
    return shutil.which(settings.amneziawg_go_binary) is not None and shutil.which(settings.awg_binary) is not None


def current_pid() -> int | None:
    if _PROCESS is not None and _PROCESS.poll() is None:
        return _PROCESS.pid
    return None


def _render_config(node: EntryNode) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {node.private_key}",
    ]
    for key, value in sorted(node.obfuscation.items()):
        lines.append(f"{key} = {value}")
    lines.extend(
        [
            "",
            "[Peer]",
            f"PublicKey = {node.public_key}",
            f"Endpoint = {node.endpoint}",
            f"AllowedIPs = {', '.join(node.allowed_ips or ['0.0.0.0/0'])}",
        ]
    )
    if node.preshared_key:
        lines.append(f"PresharedKey = {node.preshared_key}")
    if node.persistent_keepalive is not None:
        lines.append(f"PersistentKeepalive = {node.persistent_keepalive}")
    lines.append("")
    return "\n".join(lines)


def write_runtime_config(node: EntryNode) -> str:
    Path(settings.wg_config_dir).mkdir(parents=True, exist_ok=True)
    config_path = Path(settings.wg_config_dir) / f"entry-node-{node.id}.conf"
    config_path.write_text(_render_config(node), encoding="utf-8")
    return str(config_path)


async def start_tunnel(db: AsyncSession, node: EntryNode, gateway_settings: GatewaySettings) -> dict:
    global _PROCESS

    config_path = write_runtime_config(node)
    logger.info("[awg-runtime] requested tunnel start for node=%s endpoint=%s config=%s", node.name, node.endpoint, config_path)
    if not is_runtime_available():
        gateway_settings.tunnel_status = TunnelStatus.error.value
        gateway_settings.tunnel_last_error = "amneziawg-go or awg binary is not available in the container"
        logger.error("[awg-runtime] runtime binaries missing: amneziawg-go=%s awg=%s", shutil.which(settings.amneziawg_go_binary), shutil.which(settings.awg_binary))
        gateway_settings.tunnel_last_applied_at = datetime.now(timezone.utc)
        db.add(gateway_settings)
        await db.flush()
        return {"status": gateway_settings.tunnel_status, "error": gateway_settings.tunnel_last_error}

    stop_tunnel_process()
    gateway_settings.tunnel_status = TunnelStatus.starting.value
    gateway_settings.tunnel_last_error = None
    gateway_settings.tunnel_last_applied_at = datetime.now(timezone.utc)
    db.add(gateway_settings)
    await db.flush()

    env = os.environ.copy()
    env["WG_QUICK_USERSPACE_IMPLEMENTATION"] = settings.amneziawg_go_binary
    requested_mode = gateway_settings.runtime_mode or RuntimeMode.auto.value
    logger.info("[awg-runtime] requested runtime mode=%s", requested_mode)
    use_kernel = _resolve_runtime_mode(requested_mode)
    logger.info("[awg-runtime] selected %s mode for interface=%s", "kernel" if use_kernel else "userspace", settings.tunnel_interface)
    _ensure_interface_absent(settings.tunnel_interface)

    try:
        if use_kernel:
            _run_logged(
                ["ip", "link", "add", settings.tunnel_interface, "type", "amneziawg"],
                context="ip-link-add-amneziawg",
            )
            _PROCESS = None
        else:
            logger.info("[awg-runtime] starting userspace daemon: %s %s", settings.amneziawg_go_binary, settings.tunnel_interface)
            proc = subprocess.Popen(
                [settings.amneziawg_go_binary, settings.tunnel_interface],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            _PROCESS = proc
            threading.Thread(
                target=_stream_process_logs,
                args=(proc, node.name),
                daemon=True,
            ).start()
        _run_logged(
            [settings.awg_binary, "setconf", settings.tunnel_interface, config_path],
            context="setconf",
        )
        _run_logged(
            ["ip", "address", "replace", node.tunnel_address, "dev", settings.tunnel_interface],
            context="ip-address-replace",
        )
        _run_logged(
            ["ip", "link", "set", "up", "dev", settings.tunnel_interface],
            context="ip-link-up",
        )
        gateway_settings.tunnel_status = TunnelStatus.running.value
        logger.info("[awg-runtime] tunnel is running for node=%s pid=%s", node.name, current_pid())
    except subprocess.CalledProcessError as exc:
        gateway_settings.tunnel_status = TunnelStatus.error.value
        gateway_settings.tunnel_last_error = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.exception("[awg-runtime] tunnel start failed for node=%s: %s", node.name, gateway_settings.tunnel_last_error)
        stop_tunnel_process()
    gateway_settings.tunnel_last_applied_at = datetime.now(timezone.utc)
    db.add(gateway_settings)
    await db.flush()
    return {
        "status": gateway_settings.tunnel_status,
        "pid": current_pid(),
        "config_path": config_path,
        "error": gateway_settings.tunnel_last_error,
    }


def stop_tunnel_process() -> None:
    global _PROCESS
    if _PROCESS is None:
        logger.info("[awg-runtime] no userspace daemon tracked, deleting interface=%s if present", settings.tunnel_interface)
        _ensure_interface_absent(settings.tunnel_interface)
        return
    logger.info("[awg-runtime] stopping tunnel pid=%s", _PROCESS.pid)
    if _PROCESS.poll() is None:
        _PROCESS.terminate()
        try:
            _PROCESS.wait(timeout=3)
        except Exception:
            logger.warning("[awg-runtime] graceful stop timed out, killing pid=%s", _PROCESS.pid)
            _PROCESS.kill()
    _PROCESS = None
    _ensure_interface_absent(settings.tunnel_interface)


async def stop_tunnel(db: AsyncSession, gateway_settings: GatewaySettings) -> dict:
    stop_tunnel_process()
    gateway_settings.tunnel_status = TunnelStatus.stopped.value
    gateway_settings.tunnel_last_error = None
    gateway_settings.tunnel_last_applied_at = datetime.now(timezone.utc)
    db.add(gateway_settings)
    await db.flush()
    return {"status": gateway_settings.tunnel_status}


def probe_latency(node: EntryNode) -> float | None:
    target = node.endpoint_host
    logger.info("[awg-runtime] probing latency target=%s", target)
    try:
        proc = subprocess.run(
            [
                "ping",
                "-c",
                str(settings.latency_ping_count),
                "-W",
                str(settings.latency_ping_timeout_sec),
                target,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        logger.warning("[awg-runtime] latency probe failed target=%s rc=%s output=%s", target, proc.returncode, output.strip())
        return None
    match = re.search(r"time=([0-9.]+)\s*ms", output)
    if not match:
        logger.warning("[awg-runtime] latency probe produced no RTT target=%s output=%s", target, output.strip())
        return None
    logger.info("[awg-runtime] latency probe target=%s rtt_ms=%s", target, match.group(1))
    return float(match.group(1))
