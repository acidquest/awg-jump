from __future__ import annotations

import asyncio
import io
import json
import logging
import shlex
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import asyncssh

from app.database import AsyncSessionLocal
from app.models import FirstNodeBootstrapLog, FirstNodeBootstrapStatus


logger = logging.getLogger(__name__)

_bootstrap_queues: dict[int, asyncio.Queue[str | None]] = {}


def _resolve_assets_root() -> Path:
    candidates = [
        Path("/app"),
        Path(__file__).resolve().parents[5],
    ]
    for root in candidates:
        if (root / "deploy" / "docker-compose.images.yml").exists():
            return root
    raise FileNotFoundError("Bootstrap assets are missing: deploy/docker-compose.images.yml")


def get_bootstrap_queue(log_id: int) -> asyncio.Queue[str | None]:
    if log_id not in _bootstrap_queues:
        _bootstrap_queues[log_id] = asyncio.Queue()
    return _bootstrap_queues[log_id]


def cleanup_bootstrap_queue(log_id: int) -> None:
    _bootstrap_queues.pop(log_id, None)


def _replace_env_value(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    needle = f"{key}="
    for index, line in enumerate(lines):
        if line.lstrip().startswith(needle):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _build_bundle(*, host: str, docker_namespace: str, image_tag: str) -> bytes:
    assets_root = _resolve_assets_root()
    compose_path = assets_root / "deploy" / "docker-compose.images.yml"
    env_ru_path = assets_root / ".env.ru.example"
    env_en_path = assets_root / ".env.en.example"
    compose_content = compose_path.read_text(encoding="utf-8")
    env_ru_content = env_ru_path.read_text(encoding="utf-8")
    env_en_content = env_en_path.read_text(encoding="utf-8")

    env_content = env_ru_content
    image_jump = f"docker.io/{docker_namespace}/awg-jump:{image_tag}"
    image_nginx = f"docker.io/{docker_namespace}/awg-jump-nginx:{image_tag}"
    env_content = _replace_env_value(env_content, "TLS_COMMON_NAME", host)
    env_content = _replace_env_value(env_content, "SERVER_HOST", host)
    env_content = _replace_env_value(env_content, "AWG_JUMP_IMAGE", image_jump)
    env_content = _replace_env_value(env_content, "AWG_NGINX_IMAGE", image_nginx)

    remote_bootstrap = """#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="$1"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run remote bootstrap as root." >&2
    exit 1
fi

install_docker_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi

    if ! docker compose version >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq docker-compose-plugin || true
    fi
}

install_docker_dnf() {
    dnf install -y ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi
}

if command -v apt-get >/dev/null 2>&1; then
    install_docker_apt
elif command -v dnf >/dev/null 2>&1; then
    install_docker_dnf
else
    echo "Unsupported Linux distribution: no apt-get or dnf found." >&2
    exit 1
fi

systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is not available after installation." >&2
    exit 1
fi

mkdir -p "$REMOTE_DIR"
mkdir -p "$REMOTE_DIR/data/certs" "$REMOTE_DIR/data/backups" "$REMOTE_DIR/data/geoip" "$REMOTE_DIR/data/wg_configs"

if [[ ! -c /dev/net/tun ]]; then
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200 || true
    chmod 666 /dev/net/tun || true
fi
"""

    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w:gz") as archive:
        files = {
            "docker-compose.yml": compose_content,
            ".env": env_content,
            ".env.ru.example": env_ru_content,
            ".env.en.example": env_en_content,
            "REMOTE_BOOTSTRAP.sh": remote_bootstrap,
        }
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return bundle.getvalue()


async def _append_log(log_id: int, line: str) -> None:
    async with AsyncSessionLocal() as session:
        log = await session.get(FirstNodeBootstrapLog, log_id)
        if log is None:
            return
        log.log_output = (log.log_output or "") + line
        session.add(log)
        await session.commit()


async def _finish_log(log_id: int, status: FirstNodeBootstrapStatus) -> None:
    async with AsyncSessionLocal() as session:
        log = await session.get(FirstNodeBootstrapLog, log_id)
        if log is None:
            return
        log.status = status.value
        log.finished_at = datetime.now(timezone.utc)
        session.add(log)
        await session.commit()


async def bootstrap_first_node(
    *,
    log_id: int,
    host: str,
    ssh_user: str,
    ssh_password: str,
    ssh_port: int,
    remote_dir: str,
    docker_namespace: str,
    image_tag: str,
) -> None:
    queue = get_bootstrap_queue(log_id)
    step = 0
    total = 8

    async def emit(message: str, *, status: str = "running", advance: bool = True) -> None:
        nonlocal step
        if advance:
            step += 1
            line = f"[{step}/{total}] {message}\n"
        else:
            line = f"{message}\n"
        await queue.put(json.dumps({"step": step, "total": total, "message": message, "status": status}))
        await _append_log(log_id, line)

    remote_root_prefix = "" if ssh_user == "root" else "sudo "

    try:
        await emit(f"Connecting to {host}:{ssh_port}...")
        try:
            conn = await asyncssh.connect(
                host,
                port=ssh_port,
                username=ssh_user,
                password=ssh_password,
                known_hosts=None,
                connect_timeout=15,
            )
        except asyncssh.PermissionDenied as exc:
            raise RuntimeError("SSH connection failed: invalid credentials") from exc
        except (asyncssh.ConnectionLost, OSError) as exc:
            raise RuntimeError(f"SSH connection failed: {exc}") from exc

        async with conn:
            await emit("Preparing deployment bundle...")
            bundle_bytes = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _build_bundle(host=host, docker_namespace=docker_namespace, image_tag=image_tag),
            )

            await emit("Uploading deployment bundle to remote host...")
            async with conn.start_sftp_client() as sftp:
                async with sftp.open("/tmp/awg-jump-bootstrap.tgz", "wb") as remote_file:
                    await remote_file.write(bundle_bytes)

            await emit("Extracting bundle to /tmp on remote host...")
            result = await conn.run("tar -xzf /tmp/awg-jump-bootstrap.tgz -C /tmp", check=False)
            if result.returncode != 0:
                raise RuntimeError("Failed to unpack bootstrap bundle on remote host")

            await emit("Installing Docker and preparing remote directories...")
            bootstrap_command = (
                f"{remote_root_prefix}bash /tmp/REMOTE_BOOTSTRAP.sh {shlex.quote(remote_dir)}"
            )
            result = await conn.run(bootstrap_command, check=False)
            if result.stdout.strip():
                for line in result.stdout.splitlines():
                    await emit(line, advance=False)
            if result.stderr.strip():
                for line in result.stderr.splitlines():
                    await emit(line, advance=False)
            if result.returncode != 0:
                raise RuntimeError("Remote bootstrap script failed")

            await emit(f"Copying files into {remote_dir}...")
            unpack_command = (
                f"{remote_root_prefix}mkdir -p {shlex.quote(remote_dir)} && "
                f"{remote_root_prefix}tar -xzf /tmp/awg-jump-bootstrap.tgz -C {shlex.quote(remote_dir)} "
                "docker-compose.yml .env .env.ru.example .env.en.example"
            )
            result = await conn.run(unpack_command, check=False)
            if result.returncode != 0:
                raise RuntimeError("Failed to copy deployment files into the target directory")

            await emit("Removing temporary bootstrap files...")
            await conn.run("rm -f /tmp/awg-jump-bootstrap.tgz /tmp/REMOTE_BOOTSTRAP.sh", check=False)

            await emit("Bootstrap complete.", status="done")
            await _finish_log(log_id, FirstNodeBootstrapStatus.success)
    except Exception as exc:
        logger.error("[gateway-bootstrap] first node bootstrap failed: %s", exc)
        message = f"ERROR: {exc}"
        await queue.put(json.dumps({"step": step, "total": total, "message": message, "status": "error"}))
        await _append_log(log_id, message + "\n")
        await _finish_log(log_id, FirstNodeBootstrapStatus.failed)
    finally:
        await queue.put(None)
