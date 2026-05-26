"""
NodeDeployer — SSH деплой upstream нод через asyncssh, health-check, failover.

SSH пароль никогда не логируется, не сохраняется в БД, не попадает в DeployLog.
"""
import asyncio
import io
import json
import logging
import shlex
import socket
import time
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.interface import Interface
from backend.models.upstream_node import DeployLog, DeployStatus, NodePeer, NodeStatus, UpstreamNode
from backend.services.awg import _run_cmd, generate_keypair
from backend.services.upstream_nodes import (
    apply_node_to_awg1,
    apply_node_to_awg2,
    inherit_client_settings_from_interface,
    node_interface_address_for_remote,
)

logger = logging.getLogger(__name__)

# ── SSE очереди деплоя: log_id → asyncio.Queue ───────────────────────────
_deploy_queues: dict[int, asyncio.Queue] = {}

# ── Счётчики неудач health-check (в памяти) ──────────────────────────────
_health_fail_counts: dict[int, int] = {}


class RemoteShell:
    def __init__(
        self,
        conn: asyncssh.SSHClientConnection,
        *,
        ssh_user: str,
        sudo_password: str,
    ) -> None:
        self.conn = conn
        self.use_sudo = ssh_user != "root"
        self.sudo_password = sudo_password

    def _command(self, command: str) -> str:
        quoted = shlex.quote(command)
        if self.use_sudo:
            return f"sudo -S -p '' sh -lc {quoted}"
        return f"sh -lc {quoted}"

    def _input(self, payload: str | bytes | None = None) -> str | bytes | None:
        if not self.use_sudo:
            return payload
        prefix = f"{self.sudo_password}\n"
        if payload is None:
            return prefix
        if isinstance(payload, bytes):
            return prefix.encode() + payload
        return prefix + payload

    async def validate_sudo(self):
        if not self.use_sudo:
            return None
        return await self.conn.run(
            "sudo -S -p '' -v",
            input=f"{self.sudo_password}\n",
            check=False,
        )

    async def run(
        self,
        command: str,
        *,
        check: bool = False,
        input_data: str | bytes | None = None,
    ):
        return await self.conn.run(
            self._command(command),
            input=self._input(input_data),
            check=check,
        )

    def create_process(self, command: str, *, encoding: str | None = "utf-8"):
        return self.conn.create_process(self._command(command), encoding=encoding)

    async def write_process_password(self, proc) -> None:
        if self.use_sudo:
            proc.stdin.write(f"{self.sudo_password}\n")

    async def upload_bytes_to_tmp(self, path: str, content: bytes) -> None:
        async with self.conn.create_process(f"cat > {shlex.quote(path)}", encoding=None) as proc:
            proc.stdin.write(content)
            proc.stdin.write_eof()
            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"failed to upload {path}")

    async def write_text_file(self, path: str, content: str) -> None:
        quoted_path = shlex.quote(path)
        result = await self.run(f"install -d -m 755 {shlex.quote(str(Path(path).parent))}", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"failed to create remote directory for {path}: {(result.stderr or result.stdout or '')[:200]}")
        result = await self.run(f"tee {quoted_path} >/dev/null", input_data=content, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"failed to write {path}: {(result.stderr or result.stdout or '')[:200]}")


def get_deploy_queue(log_id: int) -> asyncio.Queue:
    if log_id not in _deploy_queues:
        _deploy_queues[log_id] = asyncio.Queue()
    return _deploy_queues[log_id]


def cleanup_deploy_queue(log_id: int) -> None:
    _deploy_queues.pop(log_id, None)


# ── Вспомогательные функции ───────────────────────────────────────────────

def _pack_node_sources() -> bytes:
    """Упаковывает /app/node/ в tar.gz в памяти (без temp-файлов)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add("/app/node", arcname="awg-node")
    return buf.getvalue()


def _command_output_tail(result, limit: int = 1800) -> str:
    parts: list[str] = []
    stdout = (getattr(result, "stdout", None) or "").strip()
    stderr = (getattr(result, "stderr", None) or "").strip()
    if stdout:
        parts.append(f"stdout:\n{stdout[-limit:]}")
    if stderr:
        parts.append(f"stderr:\n{stderr[-limit:]}")
    return "\n".join(parts).strip()


async def _emit_failed_command_details(emit_line, label: str, result) -> None:
    details = _command_output_tail(result)
    if details:
        await emit_line(f"{label} output:\n{details}")


def _make_node_server_config(
    *,
    private_key: str,
    awg_address: str,
    awg_port: int,
    awg1_public_key: str,
    client_address: str,
    awg1: Interface,
    shared_peers: list[NodePeer],
) -> str:
    lines = [
        "[Interface]",
        f"ListenPort = {awg_port}",
        f"PrivateKey = {private_key}",
    ]
    for key, value in [
        ("S1", awg1.obf_s1),
        ("S2", awg1.obf_s2),
        ("S3", awg1.obf_s3),
        ("S4", awg1.obf_s4),
        ("H1", awg1.obf_h1),
        ("H2", awg1.obf_h2),
        ("H3", awg1.obf_h3),
        ("H4", awg1.obf_h4),
    ]:
        if value is not None:
            lines.append(f"{key} = {value}")
    lines.append("")

    lines.extend(
        [
            "[Peer]",
            f"PublicKey = {awg1_public_key}",
            f"AllowedIPs = {client_address}",
        ]
    )
    lines.append("")

    for peer in shared_peers:
        if not peer.enabled:
            continue
        lines.extend(
            [
                "[Peer]",
                f"PublicKey = {peer.public_key}",
                f"AllowedIPs = {peer.tunnel_address}",
            ]
        )
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        if peer.persistent_keepalive:
            lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _make_env_content(private_key: str, awg_interface_address: str, awg_port: int) -> str:
    return "\n".join(
        [
            f"AWG_LISTEN_PORT={awg_port}",
            f"AWG_PRIVATE_KEY={private_key}",
            f"AWG_ADDRESS={awg_interface_address}",
        ]
    ) + "\n"


def _make_compose_content(awg_port: int) -> str:
    return (
        f"services:\n"
        f"  awg-node:\n"
        f"    image: awg-node:local\n"
        f"    restart: unless-stopped\n"
        f"    cap_add:\n"
        f"      - NET_ADMIN\n"
        f"      - NET_RAW\n"
        f"    devices:\n"
        f"      - /dev/net/tun:/dev/net/tun\n"
        f"    network_mode: host\n"
        f"    env_file: .env\n"
        f"    volumes:\n"
        f"      - ./awg0.conf:/etc/awg-node/awg0.conf:ro\n"
    )


def _probe_udp_port(host: str, port: int) -> tuple[bool, str]:
    """
    Best-effort UDP availability probe.

    UDP does not provide a reliable connect handshake, so we treat the absence
    of an immediate ICMP port-unreachable error as "online".
    """
    try:
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except OSError as exc:
        return False, f"dns_error: {exc}"

    last_error = "udp probe failed"
    for family, socktype, proto, _canonname, sockaddr in addrinfo:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(float(settings.node_health_check_timeout))
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_BINDTODEVICE,
                    settings.physical_iface.encode() + b"\0",
                )
            except OSError as exc:
                logger.warning(
                    "[health] failed to bind UDP probe to iface=%s for %s:%s: %s",
                    settings.physical_iface,
                    host,
                    port,
                    exc,
                )
            sock.connect(sockaddr)
            sock.send(b"\0")
            try:
                sock.recv(1)
                return True, f"udp response received via {settings.physical_iface}"
            except socket.timeout:
                return True, f"no ICMP error received via {settings.physical_iface}"
            except ConnectionRefusedError:
                last_error = "connection refused"
            except OSError as exc:
                if exc.errno == 111:
                    last_error = "connection refused"
                else:
                    last_error = str(exc)
        except OSError as exc:
            last_error = str(exc)
        finally:
            sock.close()

    return False, last_error


async def _get_node(node_id: int, session: AsyncSession) -> UpstreamNode:
    result = await session.execute(
        select(UpstreamNode)
        .options(selectinload(UpstreamNode.shared_peers))
        .where(UpstreamNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise RuntimeError(f"Node {node_id} not found")
    return node


async def _append_log(log_id: int, text: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            log = await session.get(DeployLog, log_id)
            if log:
                log.log_output = (log.log_output or "") + text
                session.add(log)
                await session.commit()
    except Exception as exc:
        logger.debug("[deploy_log] append failed: %s", exc)


async def _finish_log(log_id: int, status: DeployStatus) -> None:
    try:
        async with AsyncSessionLocal() as session:
            log = await session.get(DeployLog, log_id)
            if log:
                log.status = status
                log.finished_at = datetime.now(timezone.utc)
                session.add(log)
                await session.commit()
    except Exception as exc:
        logger.debug("[deploy_log] finish failed: %s", exc)


# ── NodeDeployer ──────────────────────────────────────────────────────────

class NodeDeployer:
    DEPLOY_TOTAL = 16
    REDEPLOY_TOTAL = 7

    @staticmethod
    async def _cleanup_remote_awg_node(remote: RemoteShell) -> None:
        """
        Best-effort cleanup before deploy/redeploy.

        awg0 is created in host network namespace, so after a crashed container it
        can remain on the host and break the next startup with "File exists".
        """
        await remote.run(
            "docker-compose -f /opt/awg-node/docker-compose.yml down --remove-orphans",
            check=False,
        )
        await remote.run("docker rm -f awg-node 2>/dev/null || true", check=False)
        await remote.run("ip link show awg0 >/dev/null 2>&1 && ip link delete awg0 || true", check=False)

    # ── Deploy ────────────────────────────────────────────────────────────

    async def deploy(
        self,
        node_id: int,
        log_id: int,
        ssh_user: str,
        ssh_password: str,
        ssh_port: int,
        delete_on_failure: bool = False,
    ) -> None:
        """
        Полный SSH деплой ноды.
        Пишет прогресс в _deploy_queues[log_id] + DeployLog.log_output.
        SSH пароль нигде не сохраняется и не логируется.
        """
        queue = get_deploy_queue(log_id)
        total = self.DEPLOY_TOTAL
        step = 0

        async def emit(message: str, status: str = "running") -> None:
            nonlocal step
            step += 1
            payload = json.dumps({"step": step, "total": total, "message": message, "status": status})
            await queue.put(payload)
            await _append_log(log_id, f"[{step}/{total}] {message}\n")

        async def emit_line(message: str) -> None:
            """Строка вывода без инкремента шага (для docker build)."""
            payload = json.dumps({"step": step, "total": total, "message": message, "status": "running"})
            await queue.put(payload)
            await _append_log(log_id, message + "\n")

        is_first = False

        try:
            # ── Загрузка данных из БД ─────────────────────────────────────
            async with AsyncSessionLocal() as session:
                node = await _get_node(node_id, session)
                awg1 = await session.scalar(select(Interface).where(Interface.name == "awg1"))
                if awg1 is None:
                    raise RuntimeError("awg1 interface not found in database")

                # Keypair: переиспользуем если уже сгенерирован (idempotent redeploy)
                if node.private_key and node.public_key:
                    node_private_key = node.private_key
                    node_public_key = node.public_key
                else:
                    node_private_key = None
                    node_public_key = None

                awg_address = node.awg_address
                if not node.client_address or not awg_address:
                    raise RuntimeError(
                        "Node tunnel addresses are not configured. "
                        "Set interface address in Deploy new node modal."
                    )
                awg_port = node.awg_port
                host = node.host
                awg1_public_key = awg1.public_key
                inherit_client_settings_from_interface(node, awg1)
                shared_peers = list(node.shared_peers)

                node.status = NodeStatus.deploying
                node.updated_at = datetime.now(timezone.utc)
                await session.commit()

            # ── Шаг 1: SSH соединение ─────────────────────────────────────
            await emit(f"Connecting to {host}:{ssh_port}...")
            try:
                conn = await asyncssh.connect(
                    host,
                    port=ssh_port,
                    username=ssh_user,
                    password=ssh_password,
                    known_hosts=None,  # TODO: store and verify host keys after first deploy
                    connect_timeout=15,
                )
            except asyncssh.PermissionDenied:
                raise RuntimeError("SSH connection failed: invalid credentials")
            except asyncssh.ConnectionLost as e:
                raise RuntimeError(f"SSH connection lost: {e}")
            except OSError as e:
                raise RuntimeError(f"SSH connection failed: {e}")

            async with conn:
                remote = RemoteShell(conn, ssh_user=ssh_user, sudo_password=ssh_password)
                if remote.use_sudo:
                    await emit_line("Checking sudo access...")
                    res = await remote.validate_sudo()
                    if res.returncode != 0:
                        await _emit_failed_command_details(emit_line, "sudo check", res)
                        raise RuntimeError(
                            f"sudo access failed (rc={res.returncode}): "
                            "user must be in sudoers and password must be valid"
                        )

                # ── Шаг 2: apt-get update & upgrade ──────────────────────
                await emit("Running apt-get update && upgrade...")
                res = await remote.run(
                    "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                    "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "apt-get update && upgrade", res)
                    raise RuntimeError(f"apt-get update && upgrade failed (rc={res.returncode})")

                # ── Шаг 3: установка docker ───────────────────────────────
                await emit("Installing docker.io, docker-compose, curl...")
                res = await remote.run(
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y -q "
                    "docker.io docker-compose curl ca-certificates",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "apt install", res)
                    raise RuntimeError(f"apt install failed (rc={res.returncode})")

                # ── Шаг 4: включить docker service ───────────────────────
                await emit("Enabling docker service...")
                await remote.run("systemctl enable --now docker", check=False)

                # ── Шаг 5: генерация AWG keypair ──────────────────────────
                await emit("Generating AWG keypair for node...")
                if not node_private_key:
                    node_private_key, node_public_key = generate_keypair()

                # ── Шаг 6: сохранение keypair ────────────────────────────
                await emit("Saving AWG keypair and tunnel addresses...")
                async with AsyncSessionLocal() as session:
                    node_obj = await _get_node(node_id, session)
                    node_obj.private_key = node_private_key
                    node_obj.public_key = node_public_key
                    node_obj.updated_at = datetime.now(timezone.utc)
                    await session.commit()

                # ── Шаг 7: передача исходников через tar pipe ─────────────
                await emit("Uploading node sources via tar pipe...")
                tar_bytes = await asyncio.get_running_loop().run_in_executor(
                    None, _pack_node_sources
                )
                tar_path = f"/tmp/awg-node-{log_id}.tar.gz"
                await remote.upload_bytes_to_tmp(tar_path, tar_bytes)
                res = await remote.run(
                    f"mkdir -p /opt/awg-node && tar -xzf {shlex.quote(tar_path)} -C /opt/awg-node --strip-components=1 && rm -f {shlex.quote(tar_path)}",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "upload node sources", res)
                    raise RuntimeError(f"upload node sources failed (rc={res.returncode})")

                await remote.run(
                    "find /opt/awg-node -path '/opt/awg-node/scripts/*.sh' -o -name entrypoint.sh | xargs -r chmod +x",
                    check=False,
                )

                # ── Шаг 8: запись .env ────────────────────────────────────
                await emit("Writing .env to remote node...")

                # Перечитать awg1 обфускацию (актуальная)
                async with AsyncSessionLocal() as session:
                    awg1_fresh = await session.scalar(
                        select(Interface).where(Interface.name == "awg1")
                    )

                env_content = _make_env_content(
                    private_key=node_private_key,
                    awg_interface_address=node_interface_address_for_remote(node),
                    awg_port=awg_port,
                )
                node_config_content = _make_node_server_config(
                    private_key=node_private_key,
                    awg_address=awg_address,
                    awg_port=awg_port,
                    awg1_public_key=awg1_public_key,
                    client_address=node.client_address,
                    awg1=awg1_fresh or awg1,
                    shared_peers=shared_peers,
                )
                await remote.write_text_file("/opt/awg-node/.env", env_content)
                await remote.write_text_file("/opt/awg-node/awg0.conf", node_config_content)

                # ── Шаг 9: docker build (стриминг построчно) ─────────────
                await emit("Building docker image (this may take 2-5 min)...")
                async with remote.create_process(
                    "docker build -t awg-node:local /opt/awg-node 2>&1"
                ) as proc:
                    await remote.write_process_password(proc)
                    proc.stdin.write_eof()
                    async for line in proc.stdout:
                        stripped = line.rstrip()
                        if stripped:
                            await emit_line(stripped)
                    await proc.wait()
                    if proc.returncode != 0:
                        raise RuntimeError("docker build failed")

                # ── Шаг 10: запись docker-compose.yml ────────────────────
                await emit("Writing docker-compose.yml...")
                compose_content = _make_compose_content(awg_port)
                await remote.write_text_file("/opt/awg-node/docker-compose.yml", compose_content)

                # ── Шаг 11: убедиться что /dev/net/tun существует на хосте ──
                await emit("Ensuring /dev/net/tun exists on remote host...")
                await remote.run(
                    "[ -c /dev/net/tun ] || (mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 666 /dev/net/tun)",
                    check=False,
                )

                # ── Шаг 11.5: зачистить предыдущий контейнер/интерфейс ──
                await emit("Cleaning up previous awg-node state...")
                await self._cleanup_remote_awg_node(remote)

                # ── Шаг 12 (бывший 11): docker-compose up ────────────────
                await emit("Starting awg-node container...")
                res = await remote.run(
                    "docker-compose -f /opt/awg-node/docker-compose.yml up -d",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "docker-compose up", res)
                    raise RuntimeError(
                        f"docker-compose up failed (rc={res.returncode})"
                    )

                # ── Шаг 12: проверка запуска ──────────────────────────────
                await emit("Verifying container is running...")
                await asyncio.sleep(5)
                res = await remote.run("docker ps | grep awg-node", check=False)
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "docker ps", res)
                    raise RuntimeError("awg-node container not found in docker ps")

            # ── Шаг 13: сохранение в БД ───────────────────────────────────
            await emit("Saving node status to database...")
            async with AsyncSessionLocal() as session:
                node_obj = await _get_node(node_id, session)
                node_obj.status = NodeStatus.online
                node_obj.last_deploy = datetime.now(timezone.utc)
                node_obj.last_seen = datetime.now(timezone.utc)
                node_obj.updated_at = datetime.now(timezone.utc)

                online_count = await session.scalar(
                    select(func.count()).select_from(UpstreamNode).where(
                        UpstreamNode.status == NodeStatus.online,
                        UpstreamNode.id != node_id,
                    )
                )
                is_first = (online_count == 0)
                if is_first:
                    node_obj.is_active = True

                await session.commit()

            # ── Шаг 14: добавить peer в awg1 ──────────────────────────────
            await emit("Adding node as awg1 peer...")
            async with AsyncSessionLocal() as session:
                node_obj = await _get_node(node_id, session)
                await apply_node_to_awg1(session, node_obj)
                await session.commit()

            # ── Шаг 15: активация (если первая нода) ──────────────────────
            if is_first:
                await emit("Activating as default upstream route...", status="running")
                from backend.services.routing import update_upstream_host_route, update_vpn_route
                update_vpn_route("awg1")
                update_upstream_host_route(awg_address)
            else:
                await emit("Deployment complete!", status="ok")

            if is_first:
                # Заменить последний emit на ok
                payload = json.dumps({
                    "step": step, "total": total,
                    "message": "Node activated as default upstream!",
                    "status": "ok",
                })
                await queue.put(payload)
                await _append_log(log_id, f"[{step}/{total}] Node activated as default upstream!\n")

            await _finish_log(log_id, DeployStatus.success)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("[node_deployer] Deploy node=%d failed: %s", node_id, error_msg)
            try:
                err_payload = json.dumps({
                    "step": step, "total": total,
                    "message": f"ERROR: {error_msg}",
                    "status": "error",
                })
                await queue.put(err_payload)
                await _append_log(log_id, f"ERROR: {error_msg}\n")
            except Exception:
                pass

            try:
                async with AsyncSessionLocal() as session:
                    node_obj = await _get_node(node_id, session)
                    initial_deploy = node_obj.last_deploy is None
                    if delete_on_failure and initial_deploy:
                        await session.delete(node_obj)
                    elif node_obj.status == NodeStatus.deploying:
                        node_obj.status = NodeStatus.error
                        node_obj.updated_at = datetime.now(timezone.utc)
                    await session.commit()
            except Exception:
                pass

            await _finish_log(log_id, DeployStatus.failed)

        finally:
            await queue.put(None)  # сигнал конца стрима

    # ── Redeploy ──────────────────────────────────────────────────────────

    async def redeploy(
        self,
        node_id: int,
        log_id: int,
        ssh_user: str,
        ssh_password: str,
        ssh_port: int,
    ) -> None:
        """
        Повторный деплой: передаём свежие исходники, пересобираем образ,
        перезапускаем контейнер. Ключи берём из БД.
        """
        queue = get_deploy_queue(log_id)
        total = self.REDEPLOY_TOTAL
        step = 0

        async def emit(message: str, status: str = "running") -> None:
            nonlocal step
            step += 1
            payload = json.dumps({"step": step, "total": total, "message": message, "status": status})
            await queue.put(payload)
            await _append_log(log_id, f"[{step}/{total}] {message}\n")

        async def emit_line(message: str) -> None:
            payload = json.dumps({"step": step, "total": total, "message": message, "status": "running"})
            await queue.put(payload)
            await _append_log(log_id, message + "\n")

        try:
            async with AsyncSessionLocal() as session:
                node = await _get_node(node_id, session)
                host = node.host
                awg_port = node.awg_port
                awg_address = node.awg_address
                if not node.client_address or not awg_address:
                    raise RuntimeError(
                        "Node tunnel addresses are not configured. "
                        "Set interface address and deploy again."
                    )
                node_private_key = node.private_key
                shared_peers = list(node.shared_peers)
                awg1 = await session.scalar(select(Interface).where(Interface.name == "awg1"))
                if awg1 is None:
                    raise RuntimeError("awg1 interface not found in database")
                if awg1:
                    inherit_client_settings_from_interface(node, awg1)

            if not node_private_key:
                raise RuntimeError("Node private key not found — deploy first")

            await emit(f"Connecting to {host}:{ssh_port}...")
            try:
                conn = await asyncssh.connect(
                    host,
                    port=ssh_port,
                    username=ssh_user,
                    password=ssh_password,
                    known_hosts=None,  # TODO: store and verify host keys after first deploy
                    connect_timeout=15,
                )
            except asyncssh.PermissionDenied:
                raise RuntimeError("SSH connection failed: invalid credentials")
            except asyncssh.ConnectionLost as e:
                raise RuntimeError(f"SSH connection lost: {e}")
            except OSError as e:
                raise RuntimeError(f"SSH connection failed: {e}")

            async with conn:
                remote = RemoteShell(conn, ssh_user=ssh_user, sudo_password=ssh_password)
                if remote.use_sudo:
                    await emit_line("Checking sudo access...")
                    res = await remote.validate_sudo()
                    if res.returncode != 0:
                        await _emit_failed_command_details(emit_line, "sudo check", res)
                        raise RuntimeError(
                            f"sudo access failed (rc={res.returncode}): "
                            "user must be in sudoers and password must be valid"
                        )

                await emit("Uploading fresh node sources...")
                tar_bytes = await asyncio.get_running_loop().run_in_executor(
                    None, _pack_node_sources
                )
                tar_path = f"/tmp/awg-node-{log_id}.tar.gz"
                await remote.upload_bytes_to_tmp(tar_path, tar_bytes)
                res = await remote.run(
                    f"mkdir -p /opt/awg-node && tar -xzf {shlex.quote(tar_path)} -C /opt/awg-node --strip-components=1 && rm -f {shlex.quote(tar_path)}",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "upload node sources", res)
                    raise RuntimeError(f"upload node sources failed (rc={res.returncode})")

                # Перезаписать .env (ключи из БД — не меняем)
                env_content = _make_env_content(
                    private_key=node_private_key,
                    awg_interface_address=node_interface_address_for_remote(node),
                    awg_port=awg_port,
                )
                node_config_content = _make_node_server_config(
                    private_key=node_private_key,
                    awg_address=awg_address,
                    awg_port=awg_port,
                    awg1_public_key=awg1.public_key if awg1 else "",
                    client_address=node.client_address,
                    awg1=awg1,
                    shared_peers=shared_peers,
                )
                compose_content = _make_compose_content(awg_port)
                await remote.write_text_file("/opt/awg-node/.env", env_content)
                await remote.write_text_file("/opt/awg-node/awg0.conf", node_config_content)
                await remote.write_text_file("/opt/awg-node/docker-compose.yml", compose_content)

                await emit("Rebuilding docker image...")
                async with remote.create_process(
                    "docker build -t awg-node:local /opt/awg-node 2>&1"
                ) as proc:
                    await remote.write_process_password(proc)
                    proc.stdin.write_eof()
                    async for line in proc.stdout:
                        stripped = line.rstrip()
                        if stripped:
                            await emit_line(stripped)
                    await proc.wait()
                    if proc.returncode != 0:
                        raise RuntimeError("docker build failed")

                await remote.run(
                    "[ -c /dev/net/tun ] || (mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 666 /dev/net/tun)",
                    check=False,
                )
                await emit("Cleaning up previous awg-node state...")
                await self._cleanup_remote_awg_node(remote)
                await emit("Recreating container...")
                res = await remote.run(
                    "docker-compose -f /opt/awg-node/docker-compose.yml up -d --force-recreate",
                    check=False,
                )
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "docker-compose up", res)
                    raise RuntimeError(
                        f"docker-compose up failed (rc={res.returncode})"
                    )

                await emit("Verifying container...")
                await asyncio.sleep(5)
                res = await remote.run("docker ps | grep awg-node", check=False)
                if res.returncode != 0:
                    await _emit_failed_command_details(emit_line, "docker ps", res)
                    raise RuntimeError("awg-node container not found")

            async with AsyncSessionLocal() as session:
                node_obj = await _get_node(node_id, session)
                node_obj.status = NodeStatus.online
                node_obj.last_deploy = datetime.now(timezone.utc)
                node_obj.last_seen = datetime.now(timezone.utc)
                node_obj.updated_at = datetime.now(timezone.utc)
                is_active = node_obj.is_active
                is_geoip = node_obj.is_geoip
                await session.commit()

            if is_active or is_geoip:
                async with AsyncSessionLocal() as session:
                    node_obj = await _get_node(node_id, session)
                    if is_geoip:
                        await apply_node_to_awg2(session, node_obj)
                    else:
                        await apply_node_to_awg1(session, node_obj)
                    await session.commit()

                from backend.services.routing import update_geoip_route, update_upstream_host_route, update_vpn_route
                if is_geoip:
                    update_geoip_route("awg2")
                    update_upstream_host_route(awg_address, interface_name="awg2")
                else:
                    update_vpn_route("awg1")
                    update_upstream_host_route(awg_address)

            await emit("Redeploy complete!", status="ok")
            await _finish_log(log_id, DeployStatus.success)

        except Exception as exc:
            error_msg = str(exc)
            logger.error("[node_deployer] Redeploy node=%d failed: %s", node_id, error_msg)
            err_payload = json.dumps({
                "step": step, "total": total,
                "message": f"ERROR: {error_msg}",
                "status": "error",
            })
            await queue.put(err_payload)
            await _append_log(log_id, f"ERROR: {error_msg}\n")
            await _finish_log(log_id, DeployStatus.failed)

        finally:
            await queue.put(None)

    # ── Health check ──────────────────────────────────────────────────────

    async def check_health(self, node_id: int) -> dict:
        """
        Активная нода: парсит awg show awg1 dump → last_handshake/RX/TX.
        GeoIP-нода: парсит awg show awg2 dump → last_handshake/RX/TX.
        Остальные неактивные ноды проверяются по доступности UDP-порта.
        Учитывает grace period после деплоя — нода считается живой в течение 5 минут после
        последнего деплоя даже без handshake (туннель только устанавливается).
        """
        async with AsyncSessionLocal() as session:
            node = await _get_node(node_id, session)
            host = node.host
            awg_port = node.awg_port
            is_active = node.is_active
            is_geoip = node.is_geoip
            public_key = node.public_key
            last_deploy = node.last_deploy

        result: dict = {"node_id": node_id, "alive": False, "latency_ms": None}

        # Grace period: сразу после деплоя туннель ещё не установлен
        _GRACE_PERIOD_SEC = 300  # 5 минут
        in_grace = (
            last_deploy is not None
            and (datetime.now(timezone.utc) - last_deploy.replace(tzinfo=timezone.utc)
                 if last_deploy.tzinfo is None
                 else datetime.now(timezone.utc) - last_deploy
                 ).total_seconds() < _GRACE_PERIOD_SEC
        )

        dump_interface = "awg1" if is_active and public_key else "awg2" if is_geoip and public_key else None

        if dump_interface:
            rc, output = _run_cmd(["awg", "show", dump_interface, "dump"])
            if rc == 0:
                now_ts = int(time.time())
                matched_peer = False
                for line in output.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) < 7:
                        continue
                    if parts[0] != public_key:
                        continue
                    matched_peer = True
                    handshake = int(parts[4]) if parts[4].isdigit() else 0
                    rx = int(parts[5]) if parts[5].isdigit() else 0
                    tx = int(parts[6]) if parts[6].isdigit() else 0
                    age = (now_ts - handshake) if handshake > 0 else 9999

                    result["alive"] = age < 180 or in_grace
                    result["handshake_age_sec"] = age
                    result["rx_bytes"] = rx
                    result["tx_bytes"] = tx

                    async with AsyncSessionLocal() as session:
                        node_obj = await _get_node(node_id, session)
                        node_obj.rx_bytes = rx
                        node_obj.tx_bytes = tx
                        node_obj.latency_ms = None
                        if result["alive"]:
                            if age < 180:
                                node_obj.last_seen = datetime.now(timezone.utc)
                            if node_obj.status in (NodeStatus.degraded, NodeStatus.offline):
                                node_obj.status = NodeStatus.online
                        else:
                            node_obj.status = NodeStatus.degraded
                        node_obj.updated_at = datetime.now(timezone.utc)
                        await session.commit()
                    break

                if not matched_peer:
                    logger.warning(
                        "[health] node %d peer not found in %s dump",
                        node_id, dump_interface,
                    )
                    result["alive"] = in_grace
                    async with AsyncSessionLocal() as session:
                        node_obj = await _get_node(node_id, session)
                        node_obj.latency_ms = None
                        if result["alive"]:
                            if node_obj.status in (NodeStatus.degraded, NodeStatus.offline):
                                node_obj.status = NodeStatus.online
                        else:
                            node_obj.status = NodeStatus.degraded
                        node_obj.updated_at = datetime.now(timezone.utc)
                        await session.commit()
            else:
                logger.warning("[health] awg show %s dump failed (rc=%d)", dump_interface, rc)
                result["alive"] = in_grace
                async with AsyncSessionLocal() as session:
                    node_obj = await _get_node(node_id, session)
                    node_obj.latency_ms = None
                    if result["alive"]:
                        if node_obj.status in (NodeStatus.degraded, NodeStatus.offline):
                            node_obj.status = NodeStatus.online
                    else:
                        node_obj.status = NodeStatus.degraded
                    node_obj.updated_at = datetime.now(timezone.utc)
                    await session.commit()
        else:
            udp_alive, udp_detail = _probe_udp_port(host, awg_port)
            result["alive"] = udp_alive
            result["udp_status"] = "online" if udp_alive else "offline"
            result["udp_detail"] = udp_detail

            async with AsyncSessionLocal() as session:
                node_obj = await _get_node(node_id, session)
                node_obj.latency_ms = None
                node_obj.updated_at = datetime.now(timezone.utc)
                if udp_alive:
                    node_obj.last_seen = datetime.now(timezone.utc)
                    node_obj.status = NodeStatus.online
                else:
                    node_obj.status = NodeStatus.offline
                await session.commit()

        return result

    # ── Failover ──────────────────────────────────────────────────────────

    async def failover(self, failed_node_id: int) -> bool:
        """
        Переключает awg1 на следующую онлайн-ноду по приоритету.
        Возвращает True если переключение выполнено.
        """
        logger.warning("[failover] Initiating failover from node %d", failed_node_id)

        async with AsyncSessionLocal() as session:
            next_node = await session.scalar(
                select(UpstreamNode)
                .where(
                    UpstreamNode.status == NodeStatus.online,
                    UpstreamNode.id != failed_node_id,
                    UpstreamNode.is_geoip == False,  # noqa: E712
                )
                .order_by(UpstreamNode.priority, UpstreamNode.id)
                .limit(1)
            )

            if next_node is None:
                logger.error("[failover] No online nodes available")
                # Деактивировать упавшую ноду
                failed = await _get_node(failed_node_id, session)
                failed.is_active = False
                failed.status = NodeStatus.offline
                failed.updated_at = datetime.now(timezone.utc)
                session.add(failed)
                await session.commit()
                from backend.services.routing import update_vpn_route
                update_vpn_route(None)
                from backend.services.routing import update_upstream_host_route
                update_upstream_host_route(None)
                return False

            # Деактивировать упавшую
            failed = await _get_node(failed_node_id, session)
            failed.is_active = False
            failed.status = NodeStatus.degraded
            failed.updated_at = datetime.now(timezone.utc)
            session.add(failed)

            # Активировать новую
            next_node.is_active = True
            next_node.updated_at = datetime.now(timezone.utc)
            session.add(next_node)
            await session.commit()

            new_host = next_node.host
            new_port = next_node.awg_port
            new_pubkey = next_node.public_key
            new_address = next_node.awg_address
            new_id = next_node.id

        logger.info(
            "[failover] Switching to node %d (%s:%d)", new_id, new_host, new_port
        )

        async with AsyncSessionLocal() as session:
            next_node = await _get_node(new_id, session)
            await apply_node_to_awg1(session, next_node)
            await session.commit()

        from backend.services.routing import update_upstream_host_route, update_vpn_route
        update_vpn_route("awg1")
        update_upstream_host_route(new_address)

        _health_fail_counts[failed_node_id] = 0
        return True

    # ── Remove ────────────────────────────────────────────────────────────

    async def remove(
        self,
        node_id: int,
        ssh_user: Optional[str] = None,
        ssh_password: Optional[str] = None,
        ssh_port: int = 22,
    ) -> None:
        """
        Останавливает контейнер на ноде (если переданы SSH credentials),
        убирает peer из awg1. Не удаляет запись из БД.
        """
        async with AsyncSessionLocal() as session:
            node = await _get_node(node_id, session)
            public_key = node.public_key
            host = node.host

        if ssh_user and ssh_password:
            try:
                conn = await asyncssh.connect(
                    host,
                    port=ssh_port,
                    username=ssh_user,
                    password=ssh_password,
                    known_hosts=None,  # TODO: store and verify host keys after first deploy
                    connect_timeout=10,
                )
                async with conn:
                    remote = RemoteShell(conn, ssh_user=ssh_user, sudo_password=ssh_password)
                    if remote.use_sudo:
                        res = await remote.validate_sudo()
                        if res.returncode != 0:
                            raise RuntimeError("sudo access failed")
                    await remote.run(
                        "docker-compose -f /opt/awg-node/docker-compose.yml down",
                        check=False,
                    )
            except Exception as exc:
                logger.warning(
                    "[remove] SSH cleanup failed for node %d: %s",
                    node_id, type(exc).__name__,
                )

        if public_key:
            _run_cmd(["awg", "set", "awg1", "peer", public_key, "remove"])
            _run_cmd(["awg", "set", "awg2", "peer", public_key, "remove"])

        async with AsyncSessionLocal() as session:
            node_obj = await _get_node(node_id, session)
            if node_obj.is_active:
                from backend.services.routing import update_upstream_host_route, update_vpn_route
                update_vpn_route(None)
                update_upstream_host_route(None)
            if node_obj.is_geoip:
                from backend.services.routing import update_geoip_route, update_upstream_host_route
                update_geoip_route(None)
                update_upstream_host_route(None, interface_name="awg2")

        _health_fail_counts.pop(node_id, None)


# ── Singleton ─────────────────────────────────────────────────────────────
deployer = NodeDeployer()
