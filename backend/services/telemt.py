from __future__ import annotations

import re
import secrets
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings, telemt_enabled
from backend.models.telemt_settings import TelemtSettings
from backend.models.telemt_user import TelemtUser

SUPERVISOR_CONFIG = "/etc/supervisor/supervisord.conf"
SUPERVISOR_PROGRAM = "telemt"
TELEMT_API_URL = "http://127.0.0.1:9091/v1/users"
TELEMT_RELEASE_API_URL = "https://api.github.com/repos/telemt/telemt/releases/latest"
TELEMT_REPO_URL = "https://github.com/telemt/telemt"
TELEMT_CONFIG_DOCS_URL = "https://github.com/telemt/telemt/tree/main/docs/Config_params"

DEFAULT_CONFIG_TEXT = """### Telemt Based Config.toml
# We believe that these settings are sufficient for most scenarios
# where cutting-egde methods and parameters or special solutions are not needed

# === General Settings ===
[general]
use_middle_proxy = true
# Global ad_tag fallback when user has no per-user tag in [access.user_ad_tags]
# ad_tag = "00000000000000000000000000000000"
# Per-user ad_tag in [access.user_ad_tags] (32 hex from @MTProxybot)

# === Log Level ===
# Log level: debug | verbose | normal | silent
# Can be overridden with --silent or --log-level CLI flags
# RUST_LOG env var takes absolute priority over all of these
log_level = "normal"

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"
# show = ["alice", "bob"] # Only show links for alice and bob
# show = "*"              # Show links for all users
# public_host = "proxy.example.com"  # Host (IP or domain) for tg:// links
# public_port = 443                  # Port for tg:// links (default: server.port)

# === Server Binding ===
[server]
port = 443
# proxy_protocol = false            # Enable if behind HAProxy/nginx with PROXY protocol
# metrics_port = 9090
# metrics_listen = "127.0.0.1:9090" # Listen address for metrics (overrides metrics_port)
# metrics_whitelist = ["127.0.0.1/32", "::1/128"]

[server.api]
enabled = true
listen = "127.0.0.1:9091"
whitelist = ["127.0.0.1/32", "::1/128"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

# Listen on multiple interfaces/IPs - IPv4
[[server.listeners]]
ip = "0.0.0.0"

# === Anti-Censorship & Masking ===
[censorship]
tls_domain = "petrovich.ru"  # Fake-TLS / SNI masking domain used in generated ee-links
mask = true
tls_emulation = true         # Fetch real cert lengths and emulate TLS records
tls_front_dir = "tlsfront"   # Cache directory for TLS emulation

[access.users]
# format: "username" = "32_hex_chars_secret"
#hello = "00000000000000000000000000000000"
"""


def runtime_enabled() -> bool:
    return telemt_enabled()


def ensure_runtime_dirs() -> None:
    Path(settings.telemt_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.telemt_dir, "tlsfront").mkdir(parents=True, exist_ok=True)


def generate_secret() -> str:
    return secrets.token_hex(16)


def _strip_outer_braces(value: str) -> str:
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        return text[1:-1].strip()
    return text


def normalize_config_text(value: str) -> str:
    text = _strip_outer_braces(value).strip()
    if not text:
        raise ValueError("config_text must not be empty")
    return text.rstrip() + "\n"


def _parse_config(config_text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(normalize_config_text(config_text))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML: {exc}") from exc


def extract_port(config_text: str) -> int:
    parsed = _parse_config(config_text)
    port = parsed.get("server", {}).get("port", settings.telemt_port)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("server.port must be an integer between 1 and 65535")
    return port


def extract_tls_domain(config_text: str) -> str | None:
    parsed = _parse_config(config_text)
    value = parsed.get("censorship", {}).get("tls_domain")
    return str(value).strip() if value else None


def _replace_access_users_section(config_text: str, users: list[TelemtUser]) -> str:
    lines = normalize_config_text(config_text).splitlines()
    output: list[str] = []
    in_access_users = False
    access_users_seen = False

    for line in lines:
        stripped = line.strip()
        is_section_header = stripped.startswith("[") and stripped.endswith("]")
        if stripped == "[access.users]":
            access_users_seen = True
            in_access_users = True
            output.append(line)
            output.append('# format: "username" = "32_hex_chars_secret"')
            if users:
                for user in users:
                    if user.enabled:
                        output.append(f'{user.username} = "{user.secret_hex}"')
            else:
                output.append('#hello = "00000000000000000000000000000000"')
            continue
        if in_access_users:
            if is_section_header:
                in_access_users = False
            else:
                continue
        output.append(line)

    if not access_users_seen:
        if output and output[-1].strip():
            output.append("")
        output.append("[access.users]")
        output.append('# format: "username" = "32_hex_chars_secret"')
        if users:
            for user in users:
                if user.enabled:
                    output.append(f'{user.username} = "{user.secret_hex}"')
        else:
            output.append('#hello = "00000000000000000000000000000000"')

    return "\n".join(output).rstrip() + "\n"


def _inject_public_links(config_text: str, *, public_host: str, public_port: int) -> str:
    lines = normalize_config_text(config_text).splitlines()
    output: list[str] = []
    in_general_links = False
    links_seen = False
    has_public_host = False
    has_public_port = False

    def append_missing_link_lines() -> None:
        if not has_public_host and public_host:
            output.append(f'public_host = "{public_host}"')
        if not has_public_port:
            output.append(f"public_port = {public_port}")

    for line in lines:
        stripped = line.strip()
        is_section_header = stripped.startswith("[") and stripped.endswith("]")
        if stripped == "[general.links]":
            links_seen = True
            in_general_links = True
            output.append(line)
            continue
        if in_general_links:
            if re.match(r"^public_host\s*=", stripped):
                has_public_host = True
            elif re.match(r"^public_port\s*=", stripped):
                has_public_port = True
            if is_section_header:
                append_missing_link_lines()
                in_general_links = False
                output.append(line)
                continue
        output.append(line)

    if in_general_links:
        append_missing_link_lines()

    if not links_seen:
        if output and output[-1].strip():
            output.append("")
        output.extend(
            [
                "[general.links]",
                'show = "*"',
            ]
        )
        append_missing_link_lines()

    return "\n".join(output).rstrip() + "\n"


def build_runtime_config(config_text: str, *, users: list[TelemtUser], public_host: str, public_port: int) -> str:
    rendered = _inject_public_links(config_text, public_host=public_host, public_port=public_port)
    rendered = _replace_access_users_section(rendered, users)
    _parse_config(rendered)
    return rendered


def _version_tuple(version: str) -> tuple[int, ...]:
    cleaned = version.strip().lstrip("v")
    parts = []
    for item in cleaned.split("."):
        digits = "".join(ch for ch in item if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def compare_versions(installed: str, latest: str) -> str:
    if not installed or not latest:
        return "unknown"
    left = _version_tuple(installed)
    right = _version_tuple(latest)
    if not left or not right:
        return "unknown"
    if left < right:
        return "outdated"
    if left == right:
        return "latest"
    return "ahead"


async def fetch_latest_version() -> dict[str, str | None]:
    try:
        async with httpx.AsyncClient(timeout=3.0, headers={"Accept": "application/vnd.github+json"}) as client:
            response = await client.get(TELEMT_RELEASE_API_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return {
            "latest_version": None,
            "latest_release_url": f"{TELEMT_REPO_URL}/releases",
            "version_status": "unknown",
        }

    latest_version = str(payload.get("tag_name") or "").strip().lstrip("v") or None
    release_url = str(payload.get("html_url") or f"{TELEMT_REPO_URL}/releases")
    return {
        "latest_version": latest_version,
        "latest_release_url": release_url,
        "version_status": compare_versions(settings.telemt_version, latest_version or ""),
    }


async def ensure_settings_row(session: AsyncSession) -> TelemtSettings:
    row = await session.get(TelemtSettings, 1)
    default_config = normalize_config_text(DEFAULT_CONFIG_TEXT)
    if row is None:
        row = TelemtSettings(
            id=1,
            enabled=runtime_enabled(),
            port=extract_port(default_config),
            tls_domain=extract_tls_domain(default_config) or "petrovich.ru",
            use_middle_proxy=True,
            log_level="normal",
            mode_classic=False,
            mode_secure=False,
            mode_tls=True,
            config_text=default_config,
            public_host=settings.server_host or "",
            restart_required=False,
            service_autostart=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.flush()
        return row

    config_text = row.config_text or default_config
    row.config_text = normalize_config_text(config_text)
    row.enabled = runtime_enabled()
    row.port = extract_port(row.config_text)
    row.tls_domain = extract_tls_domain(row.config_text) or row.tls_domain or "petrovich.ru"
    row.public_host = settings.server_host or ""
    session.add(row)
    await session.flush()
    return row


async def list_users(session: AsyncSession, *, include_disabled: bool = True) -> list[TelemtUser]:
    stmt = select(TelemtUser).order_by(TelemtUser.username.asc())
    if not include_disabled:
        stmt = stmt.where(TelemtUser.enabled == True)  # noqa: E712
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def write_config(session: AsyncSession) -> str:
    ensure_runtime_dirs()
    row = await ensure_settings_row(session)
    users = await list_users(session)
    rendered = build_runtime_config(
        row.config_text,
        users=users,
        public_host=row.public_host,
        public_port=row.port,
    )
    Path(settings.telemt_config_path).write_text(rendered, encoding="utf-8")
    return rendered


def _run_supervisorctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONFIG, *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_iptables(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["iptables", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _telemt_mark_rules() -> list[list[str]]:
    return [
        [
            "-t",
            "mangle",
            "-p",
            "tcp",
            "-m",
            "tcp",
            "--dport",
            "8888",
            "-m",
            "set",
            "--match-set",
            "geoip_local",
            "dst",
            "-j",
            "MARK",
            "--set-xmark",
            "0x1/0xffffffff",
        ],
        [
            "-t",
            "mangle",
            "-p",
            "tcp",
            "-m",
            "tcp",
            "--dport",
            "8888",
            "-m",
            "set",
            "!",
            "--match-set",
            "geoip_local",
            "dst",
            "-j",
            "MARK",
            "--set-xmark",
            "0x2/0xffffffff",
        ],
        [
            "-t",
            "mangle",
            "-p",
            "tcp",
            "-m",
            "tcp",
            "--dport",
            "443",
            "-m",
            "set",
            "--match-set",
            "geoip_local",
            "dst",
            "-j",
            "MARK",
            "--set-xmark",
            "0x1/0xffffffff",
        ],
        [
            "-t",
            "mangle",
            "-p",
            "tcp",
            "-m",
            "tcp",
            "--dport",
            "443",
            "-m",
            "set",
            "!",
            "--match-set",
            "geoip_local",
            "dst",
            "-j",
            "MARK",
            "--set-xmark",
            "0x2/0xffffffff",
        ],
    ]


def _sync_telemt_output_marks(action: str) -> None:
    if action == "start":
        operation = "-A"
    elif action == "stop":
        operation = "-D"
    else:
        return

    for rule in _telemt_mark_rules():
        _run_iptables(*rule[:2], operation, "OUTPUT", *rule[2:])


def get_service_status() -> dict[str, Any]:
    if not runtime_enabled():
        return {
            "enabled": False,
            "running": False,
            "status": "disabled",
            "message": "TeleMT feature is disabled in .env",
        }
    result = _run_supervisorctl("status", SUPERVISOR_PROGRAM)
    output = (result.stdout or result.stderr or "").strip()
    lowered = output.lower()
    running = " running " in f" {lowered} " or lowered.endswith(" running")
    status = "unknown"
    if output:
        parts = output.split(None, 2)
        if len(parts) >= 2:
            status = parts[1].lower()
    return {
        "enabled": True,
        "running": running,
        "status": status,
        "message": output,
    }


def control_service(action: str) -> dict[str, Any]:
    if action not in {"start", "stop", "restart"}:
        raise ValueError(f"Unsupported action: {action}")
    result = _run_supervisorctl(action, SUPERVISOR_PROGRAM)
    if result.returncode == 0:
        _sync_telemt_output_marks(action)
    payload = get_service_status()
    payload.update(
        {
            "action": action,
            "ok": result.returncode == 0,
            "command_output": (result.stdout or result.stderr or "").strip(),
        }
    )
    return payload


def service_autostart_for_action(action: str) -> bool | None:
    if action in {"start", "restart"}:
        return True
    if action == "stop":
        return False
    return None


async def fetch_links() -> dict[str, dict[str, list[str]]]:
    if not get_service_status().get("running"):
        return {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(TELEMT_API_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return {}

    result: dict[str, dict[str, list[str]]] = {}
    for item in payload.get("data", []):
        username = item.get("username")
        links = item.get("links") or {}
        if not isinstance(username, str):
            continue
        result[username] = {
            "classic": [str(v) for v in (links.get("classic") or [])],
            "secure": [str(v) for v in (links.get("secure") or [])],
            "tls": [str(v) for v in (links.get("tls") or [])],
        }
    return result


def primary_link(links: dict[str, list[str]]) -> str | None:
    for key in ("tls", "secure", "classic"):
        values = links.get(key) or []
        if values:
            return values[0]
    return None


async def build_page_payload(session: AsyncSession) -> dict[str, Any]:
    row = await ensure_settings_row(session)
    await write_config(session)
    users = await list_users(session)
    status = get_service_status()
    link_map = await fetch_links() if status.get("running") else {}
    version_info = await fetch_latest_version()
    return {
        "feature_enabled": runtime_enabled(),
        "service": status,
        "settings": {
            "config_text": row.config_text,
            "port": row.port,
            "public_host": row.public_host,
            "restart_required": row.restart_required,
            "service_autostart": row.service_autostart,
            "docs_url": TELEMT_CONFIG_DOCS_URL,
        },
        "version": {
            "installed": settings.telemt_version,
            **version_info,
            "repo_url": TELEMT_REPO_URL,
        },
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "secret_hex": user.secret_hex,
                "enabled": user.enabled,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "links": link_map.get(user.username, {"classic": [], "secure": [], "tls": []}),
                "address": primary_link(link_map.get(user.username, {})) if status.get("running") else None,
            }
            for user in users
        ],
    }


async def refresh_generated_config(session: AsyncSession) -> None:
    await write_config(session)
    await session.flush()


def normalize_username(value: str) -> str:
    username = value.strip()
    if not username:
        raise ValueError("username is required")
    if len(username) > 64:
        raise ValueError("username is too long")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in username):
        raise ValueError("username may contain only letters, digits, dot, underscore, and dash")
    return username


def normalize_secret(value: str) -> str:
    secret_hex = value.strip().lower()
    if len(secret_hex) != 32 or any(ch not in "0123456789abcdef" for ch in secret_hex):
        raise ValueError("secret_hex must be exactly 32 lowercase hexadecimal characters")
    return secret_hex
