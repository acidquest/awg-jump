import logging
import subprocess
import time
from pathlib import Path


logger = logging.getLogger(__name__)

DOT_LISTEN_HOST = "127.0.0.1"
DOT_LISTEN_PORT = 5453
DOH_LISTEN_HOST = "127.0.0.1"
DOH_LISTEN_PORT = 5053

_RUNTIME_DIR = Path("/var/run/awg-protected-dns")
_STUBBY_CONFIG = _RUNTIME_DIR / "stubby.yml"
_CLOUDFLARED_CONFIG = _RUNTIME_DIR / "cloudflared.yml"
_STUBBY_PROCESS: subprocess.Popen | None = None
_CLOUDFLARED_PROCESS: subprocess.Popen | None = None


def ensure_runtime_dir() -> None:
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _wait_started(proc: subprocess.Popen, name: str) -> None:
    time.sleep(0.2)
    if proc.poll() is not None:
        raise RuntimeError(f"{name} exited with code {proc.returncode}")


def _terminate_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        logger.warning("%s did not stop gracefully, sending SIGKILL", name)
        proc.kill()
        proc.wait(timeout=3)


def _is_ip_address(value: str) -> bool:
    try:
        import ipaddress

        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _render_stubby_config(*, host: str, port: int, bootstrap_address: str) -> str:
    address_data = host if _is_ip_address(host) else bootstrap_address
    tls_auth_name = ""
    if not _is_ip_address(host):
        tls_auth_name = f"    tls_auth_name: \"{host}\"\n"
    return (
        "resolution_type: GETDNS_RESOLUTION_STUB\n"
        "dns_transport_list:\n"
        "  - GETDNS_TRANSPORT_TLS\n"
        "tls_authentication: GETDNS_AUTHENTICATION_REQUIRED\n"
        "idle_timeout: 10000\n"
        "listen_addresses:\n"
        f"  - {DOT_LISTEN_HOST}@{DOT_LISTEN_PORT}\n"
        "upstream_recursive_servers:\n"
        "  -\n"
        f"    address_data: {address_data}\n"
        f"    tls_port: {port}\n"
        f"{tls_auth_name}"
    )


def _render_cloudflared_config(*, endpoint_url: str, bootstrap_address: str) -> str:
    lines = [
        "proxy-dns: true",
        f"proxy-dns-address: {DOH_LISTEN_HOST}",
        f"proxy-dns-port: {DOH_LISTEN_PORT}",
        "proxy-dns-upstream:",
        f"  - {endpoint_url}",
    ]
    if bootstrap_address:
        lines.extend(
            [
                "proxy-dns-bootstrap:",
                f"  - {bootstrap_address}",
            ]
        )
    return "\n".join(lines) + "\n"


def _start_stubby(*, host: str, port: int, bootstrap_address: str) -> None:
    global _STUBBY_PROCESS
    ensure_runtime_dir()
    _STUBBY_CONFIG.write_text(
        _render_stubby_config(host=host, port=port, bootstrap_address=bootstrap_address),
        encoding="utf-8",
    )
    _terminate_process(_STUBBY_PROCESS, "stubby")
    _STUBBY_PROCESS = subprocess.Popen(
        ["stubby", "-C", str(_STUBBY_CONFIG)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_started(_STUBBY_PROCESS, "stubby")


def _start_cloudflared(*, endpoint_url: str, bootstrap_address: str) -> None:
    global _CLOUDFLARED_PROCESS
    ensure_runtime_dir()
    _CLOUDFLARED_CONFIG.write_text(
        _render_cloudflared_config(endpoint_url=endpoint_url, bootstrap_address=bootstrap_address),
        encoding="utf-8",
    )
    _terminate_process(_CLOUDFLARED_PROCESS, "cloudflared")
    _CLOUDFLARED_PROCESS = subprocess.Popen(
        ["cloudflared", "proxy-dns", "--config", str(_CLOUDFLARED_CONFIG)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_started(_CLOUDFLARED_PROCESS, "cloudflared")


def sync(zone_payloads: list[dict]) -> None:
    dot_zone = next((zone for zone in zone_payloads if zone.get("protocol") == "dot"), None)
    doh_zone = next((zone for zone in zone_payloads if zone.get("protocol") == "doh"), None)

    if dot_zone is None:
        stop_stubby()
    else:
        _start_stubby(
            host=dot_zone["endpoint_host"],
            port=dot_zone["endpoint_port"],
            bootstrap_address=dot_zone.get("bootstrap_address", ""),
        )

    if doh_zone is None:
        stop_cloudflared()
    else:
        _start_cloudflared(
            endpoint_url=doh_zone["endpoint_url"],
            bootstrap_address=doh_zone.get("bootstrap_address", ""),
        )


def stop_stubby() -> None:
    global _STUBBY_PROCESS
    _terminate_process(_STUBBY_PROCESS, "stubby")
    _STUBBY_PROCESS = None


def stop_cloudflared() -> None:
    global _CLOUDFLARED_PROCESS
    _terminate_process(_CLOUDFLARED_PROCESS, "cloudflared")
    _CLOUDFLARED_PROCESS = None


def stop_all() -> None:
    stop_stubby()
    stop_cloudflared()


def status() -> dict:
    return {
        "stubby": {
            "enabled": _STUBBY_PROCESS is not None,
            "running": _STUBBY_PROCESS is not None and _STUBBY_PROCESS.poll() is None,
            "listen": f"{DOT_LISTEN_HOST}:{DOT_LISTEN_PORT}",
            "config": str(_STUBBY_CONFIG),
        },
        "cloudflared": {
            "enabled": _CLOUDFLARED_PROCESS is not None,
            "running": _CLOUDFLARED_PROCESS is not None and _CLOUDFLARED_PROCESS.poll() is None,
            "listen": f"{DOH_LISTEN_HOST}:{DOH_LISTEN_PORT}",
            "config": str(_CLOUDFLARED_CONFIG),
        },
    }


def dnsmasq_target(protocol: str, servers: list[str]) -> list[str]:
    if protocol == "dot":
        return [f"{DOT_LISTEN_HOST}#{DOT_LISTEN_PORT}"]
    if protocol == "doh":
        return [f"{DOH_LISTEN_HOST}#{DOH_LISTEN_PORT}"]
    return servers
