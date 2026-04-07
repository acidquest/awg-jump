"""
NodeDeployer — SSH деплой upstream нод через asyncssh, health-check, failover.

SSH пароль никогда не логируется, не сохраняется в БД, не попадает в DeployLog.
"""
import asyncio
import io
import ipaddress
import json
import logging
import time
import tarfile
from datetime import datetime, timezone
from typing import Optional

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.interface import Interface
from backend.models.upstream_node import DeployLog, DeployStatus, NodeStatus, UpstreamNode
from backend.services.awg import _run_cmd, generate_keypair

logger = logging.getLogger(__name__)

_UPSTREAM_ALLOWED_IPS = settings.awg1_allowed_ips or "0.0.0.0/0"

# ── SSE очереди деплоя: log_id → asyncio.Queue ───────────────────────────
_deploy_queues: dict[int, asyncio.Queue] = {}

# ── Счётчики неудач health-check (в памяти) ──────────────────────────────
_health_fail_counts: dict[int, int] = {}


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


async def _allocate_awg_address(session: AsyncSession) -> str:
    """Выделяет следующий свободный /32 адрес из NODE_VPN_SUBNET (начиная с .3)."""
    network = ipaddress.IPv4Network(settings.node_vpn_subnet)
    result = await session.execute(
        select(UpstreamNode.awg_address).where(UpstreamNode.awg_address.isnot(None))
    )
    used = {row[0].split("/")[0] for row in result.all()}

    # Зарезервированные: .1 (gateway), .2 (jump awg1)
    jump_base = str(network.network_address).rsplit(".", 1)[0]
    reserved = {f"{jump_base}.1", f"{jump_base}.2"}

    for host in network.hosts():
        addr = str(host)
        if addr in reserved:
            continue
        if addr not in used:
            return f"{addr}/32"

    raise RuntimeError("No available addresses in NODE_VPN_SUBNET")


def _make_env_content(
    private_key: str,
    awg_address: str,
    awg_port: int,
    awg1_public_key: str,
    awg1: Interface,
) -> str:
    """Генерирует содержимое .env для awg-node (нода — сервер, без Junk)."""
    # AWG_PEER_ALLOWED_IPS — адрес awg1 jump-сервера (он пир для ноды)
    jump_awg1_address = settings.awg1_address  # например 10.20.0.2/32
    lines = [
        f"AWG_LISTEN_PORT={awg_port}",
        f"AWG_PRIVATE_KEY={private_key}",
        f"AWG_ADDRESS={awg_address}",
        f"AWG_PEER_PUBLIC_KEY={awg1_public_key}",
        f"AWG_PEER_ALLOWED_IPS={jump_awg1_address}",
        "AWG_PEER_ENDPOINT=",
        # Симметричные параметры обфускации (нода — сервер, Junk не нужен)
        f"AWG_S1={awg1.obf_s1 or 0}",
        f"AWG_S2={awg1.obf_s2 or 0}",
        f"AWG_S3={awg1.obf_s3 or 0}",
        f"AWG_S4={awg1.obf_s4 or 0}",
        f"AWG_H1={awg1.obf_h1 or 0}",
        f"AWG_H2={awg1.obf_h2 or 0}",
        f"AWG_H3={awg1.obf_h3 or 0}",
        f"AWG_H4={awg1.obf_h4 or 0}",
    ]
    return "\n".join(lines) + "\n"


def _make_compose_content(awg_port: int) -> str:
    return (
        f"services:\n"
        f"  awg-node:\n"
        f"    image: awg-node:local\n"
        f"    restart: unless-stopped\n"
        f"    cap_add:\n"
        f"      - NET_ADMIN\n"
        f"      - NET_RAW\n"
        f"    sysctls:\n"
        f"      - net.ipv4.ip_forward=1\n"
        f"    devices:\n"
        f"      - /dev/net/tun:/dev/net/tun\n"
        f"    network_mode: host\n"
        f"    env_file: .env\n"
    )


async def _get_node(node_id: int, session: AsyncSession) -> UpstreamNode:
    node = await session.get(UpstreamNode, node_id)
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
    DEPLOY_TOTAL = 15
    REDEPLOY_TOTAL = 6

    # ── Deploy ────────────────────────────────────────────────────────────

    async def deploy(
        self,
        node_id: int,
        log_id: int,
        ssh_user: str,
        ssh_password: str,
        ssh_port: int,
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

                # Адрес: используем существующий или выделим позже
                awg_address = node.awg_address
                awg_port = node.awg_port
                host = node.host
                awg1_public_key = awg1.public_key

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
                # ── Шаг 2: apt-get update & upgrade ──────────────────────
                await emit("Running apt-get update && upgrade...")
                res = await conn.run(
                    "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                    "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q",
                    check=False,
                )
                if res.returncode != 0:
                    raise RuntimeError(f"apt-get update failed (rc={res.returncode})")

                # ── Шаг 3: установка docker ───────────────────────────────
                await emit("Installing docker.io, docker-compose, curl...")
                res = await conn.run(
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y -q "
                    "docker.io docker-compose curl ca-certificates",
                    check=False,
                )
                if res.returncode != 0:
                    raise RuntimeError(f"apt install failed (rc={res.returncode})")

                # ── Шаг 4: включить docker service ───────────────────────
                await emit("Enabling docker service...")
                await conn.run("systemctl enable --now docker", check=False)

                # ── Шаг 5: генерация AWG keypair ──────────────────────────
                await emit("Generating AWG keypair for node...")
                if not node_private_key:
                    node_private_key, node_public_key = generate_keypair()

                # ── Шаг 6: выделение awg_address ─────────────────────────
                await emit("Allocating AWG address...")
                if not awg_address:
                    async with AsyncSessionLocal() as session:
                        awg_address = await _allocate_awg_address(session)
                        node_obj = await _get_node(node_id, session)
                        node_obj.awg_address = awg_address
                        node_obj.private_key = node_private_key
                        node_obj.public_key = node_public_key
                        node_obj.updated_at = datetime.now(timezone.utc)
                        await session.commit()
                else:
                    # Сохранить ключи в БД
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
                await conn.run("mkdir -p /opt/awg-node", check=True)
                async with conn.create_process(
                    "tar -xzf - -C /opt/awg-node --strip-components=1",
                    encoding=None,  # бинарный режим — stdin принимает bytes
                ) as proc:
                    proc.stdin.write(tar_bytes)
                    proc.stdin.write_eof()
                    await proc.wait()

                # ── Шаг 8: запись .env ────────────────────────────────────
                await emit("Writing .env to remote node...")

                # Перечитать awg1 обфускацию (актуальная)
                async with AsyncSessionLocal() as session:
                    awg1_fresh = await session.scalar(
                        select(Interface).where(Interface.name == "awg1")
                    )

                env_content = _make_env_content(
                    private_key=node_private_key,
                    awg_address=awg_address,
                    awg_port=awg_port,
                    awg1_public_key=awg1_public_key,
                    awg1=awg1_fresh or awg1,
                )
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open("/opt/awg-node/.env", "w") as f:
                        await f.write(env_content)

                # ── Шаг 9: docker build (стриминг построчно) ─────────────
                await emit("Building docker image (this may take 2-5 min)...")
                async with conn.create_process(
                    "docker build -t awg-node:local /opt/awg-node 2>&1"
                ) as proc:
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
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open("/opt/awg-node/docker-compose.yml", "w") as f:
                        await f.write(compose_content)

                # ── Шаг 11: убедиться что /dev/net/tun существует на хосте ──
                await emit("Ensuring /dev/net/tun exists on remote host...")
                await conn.run(
                    "[ -c /dev/net/tun ] || (mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 666 /dev/net/tun)",
                    check=False,
                )

                # ── Шаг 12 (бывший 11): docker-compose up ────────────────
                await emit("Starting awg-node container...")
                res = await conn.run(
                    "docker-compose -f /opt/awg-node/docker-compose.yml up -d",
                    check=False,
                )
                if res.returncode != 0:
                    raise RuntimeError(
                        f"docker-compose up failed: {(res.stderr or '')[:200]}"
                    )

                # ── Шаг 12: проверка запуска ──────────────────────────────
                await emit("Verifying container is running...")
                await asyncio.sleep(5)
                res = await conn.run("docker ps | grep awg-node", check=False)
                if res.returncode != 0:
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
            _run_cmd([
                "awg", "set", "awg1",
                "peer", node_public_key,
                "endpoint", f"{host}:{awg_port}",
                "allowed-ips", _UPSTREAM_ALLOWED_IPS,
                "persistent-keepalive", "25",
            ])

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
                    if node_obj.status == NodeStatus.deploying:
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
                node_private_key = node.private_key
                node_public_key = node.public_key

                awg1 = await session.scalar(select(Interface).where(Interface.name == "awg1"))

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
                await emit("Uploading fresh node sources...")
                tar_bytes = await asyncio.get_running_loop().run_in_executor(
                    None, _pack_node_sources
                )
                await conn.run("mkdir -p /opt/awg-node", check=True)
                async with conn.create_process(
                    "tar -xzf - -C /opt/awg-node --strip-components=1",
                    encoding=None,  # бинарный режим — stdin принимает bytes
                ) as proc:
                    proc.stdin.write(tar_bytes)
                    proc.stdin.write_eof()
                    await proc.wait()

                # Перезаписать .env (ключи из БД — не меняем)
                env_content = _make_env_content(
                    private_key=node_private_key,
                    awg_address=awg_address,
                    awg_port=awg_port,
                    awg1_public_key=awg1.public_key if awg1 else "",
                    awg1=awg1,
                )
                compose_content = _make_compose_content(awg_port)
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open("/opt/awg-node/.env", "w") as f:
                        await f.write(env_content)
                    async with sftp.open("/opt/awg-node/docker-compose.yml", "w") as f:
                        await f.write(compose_content)

                await emit("Rebuilding docker image...")
                async with conn.create_process(
                    "docker build -t awg-node:local /opt/awg-node 2>&1"
                ) as proc:
                    async for line in proc.stdout:
                        stripped = line.rstrip()
                        if stripped:
                            await emit_line(stripped)
                    await proc.wait()
                    if proc.returncode != 0:
                        raise RuntimeError("docker build failed")

                await conn.run(
                    "[ -c /dev/net/tun ] || (mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 666 /dev/net/tun)",
                    check=False,
                )
                await emit("Recreating container...")
                res = await conn.run(
                    "docker-compose -f /opt/awg-node/docker-compose.yml up -d --force-recreate",
                    check=False,
                )
                if res.returncode != 0:
                    raise RuntimeError(
                        f"docker-compose up failed: {(res.stderr or '')[:200]}"
                    )

                await emit("Verifying container...")
                await asyncio.sleep(5)
                res = await conn.run("docker ps | grep awg-node", check=False)
                if res.returncode != 0:
                    raise RuntimeError("awg-node container not found")

            async with AsyncSessionLocal() as session:
                node_obj = await _get_node(node_id, session)
                node_obj.status = NodeStatus.online
                node_obj.last_deploy = datetime.now(timezone.utc)
                node_obj.last_seen = datetime.now(timezone.utc)
                node_obj.updated_at = datetime.now(timezone.utc)
                await session.commit()

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
        Активная нода: парсит awg show awg1 dump → last_handshake.
        Неактивные: ICMP ping к host.
        Учитывает grace period после деплоя — нода считается живой в течение 5 минут после
        последнего деплоя даже без handshake (туннель только устанавливается).
        """
        async with AsyncSessionLocal() as session:
            node = await _get_node(node_id, session)
            host = node.host
            awg_port = node.awg_port
            is_active = node.is_active
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

        if is_active and public_key:
            rc, output = _run_cmd(["awg", "show", "awg1", "dump"])
            if rc == 0:
                now_ts = int(time.time())
                for line in output.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) < 7:
                        continue
                    if parts[0] != public_key:
                        continue
                    handshake = int(parts[4]) if parts[4].isdigit() else 0
                    rx = int(parts[5]) if parts[5].isdigit() else 0
                    tx = int(parts[6]) if parts[6].isdigit() else 0
                    age = (now_ts - handshake) if handshake > 0 else 9999

                    # В grace period считаем живой даже без handshake
                    result["alive"] = age < 180 or in_grace
                    result["handshake_age_sec"] = age
                    result["rx_bytes"] = rx
                    result["tx_bytes"] = tx

                    async with AsyncSessionLocal() as session:
                        node_obj = await _get_node(node_id, session)
                        node_obj.rx_bytes = rx
                        node_obj.tx_bytes = tx
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
            else:
                # awg show не работает — может awg1 упал
                logger.warning("[health] awg show awg1 dump failed (rc=%d)", rc)
                if in_grace:
                    result["alive"] = True
        else:
            # Неактивные ноды — ICMP ping (AWG использует UDP, TCP connect бесполезен)
            t0 = time.monotonic()
            rc_ping, _ = _run_cmd([
                "ping", "-c", "1", "-W",
                str(max(1, int(settings.node_health_check_timeout))),
                host,
            ])
            latency = (time.monotonic() - t0) * 1000
            result["alive"] = rc_ping == 0 or in_grace
            result["latency_ms"] = latency if rc_ping == 0 else None

            if result["alive"]:
                async with AsyncSessionLocal() as session:
                    node_obj = await _get_node(node_id, session)
                    node_obj.latency_ms = latency if rc_ping == 0 else node_obj.latency_ms
                    node_obj.last_seen = datetime.now(timezone.utc)
                    node_obj.updated_at = datetime.now(timezone.utc)
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

        _run_cmd([
            "awg", "set", "awg1",
            "peer", new_pubkey,
            "endpoint", f"{new_host}:{new_port}",
            "allowed-ips", _UPSTREAM_ALLOWED_IPS,
            "persistent-keepalive", "25",
        ])

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
                    await conn.run(
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

        async with AsyncSessionLocal() as session:
            node_obj = await _get_node(node_id, session)
            if node_obj.is_active:
                from backend.services.routing import update_upstream_host_route, update_vpn_route
                update_vpn_route(None)
                update_upstream_host_route(None)

        _health_fail_counts.pop(node_id, None)


# ── Singleton ─────────────────────────────────────────────────────────────
deployer = NodeDeployer()
