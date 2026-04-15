"""
AWG сервис — управление AmneziaWG интерфейсами.

Демон amneziawg-go запускается как дочерний процесс.
PIDs хранятся в памяти (module-level singleton).
"""
import asyncio
import io
import logging
import os
import random
import secrets
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.interface import Interface, InterfaceMode
from backend.models.peer import Peer
from backend.models.upstream_node import NodeStatus, UpstreamNode


# ── Singleton — PID таблица запущенных демонов (userspace режим) ─────────
_awg_processes: dict[str, subprocess.Popen] = {}

# ── Режим работы: kernel module или userspace amneziawg-go ────────────────
_kernel_mode: bool | None = None  # None = ещё не определено
_INTERFACE_MTU = {
    "awg0": "1380",
    "awg1": "1300",
}


def _detect_kernel_mode() -> bool:
    """
    Определяет доступен ли нативный kernel module AmneziaWG (не обычный wireguard!).
    Только amneziawg поддерживает обфускацию — стандартный wireguard нам не подходит.

    Используем прямой ip link probe вместо grep /proc/modules — надёжнее,
    потому что имя модуля в /proc/modules может отличаться (amneziawg vs amnezia_wg).
    Именно этот же метод использует amneziawg-go для детекции ядра.
    """
    global _kernel_mode
    if _kernel_mode is not None:
        return _kernel_mode

    # Пробуем создать интерфейс типа amneziawg — если успешно, ядро поддерживает
    rc, out = _run_cmd(["ip", "link", "add", "awg_probe", "type", "amneziawg"])
    if rc == 0:
        _run_cmd(["ip", "link", "delete", "awg_probe"])
        logger.info("[awg] Kernel mode confirmed via ip link probe (amneziawg module present)")
        _kernel_mode = True
        return True

    logger.info("[awg] AmneziaWG kernel module not available (ip link probe rc=%d), using amneziawg-go userspace", rc)
    _kernel_mode = False
    return False


# ── Генерация ключей ─────────────────────────────────────────────────────

def generate_keypair() -> tuple[str, str]:
    """Возвращает (private_key, public_key) в base64 формате WireGuard."""
    priv = subprocess.check_output(["awg", "genkey"]).decode().strip()
    pub = subprocess.check_output(
        ["awg", "pubkey"], input=priv.encode()
    ).decode().strip()
    return priv, pub


def generate_preshared_key() -> str:
    return subprocess.check_output(["awg", "genpsk"]).decode().strip()


# ── Генерация параметров обфускации ──────────────────────────────────────

def generate_obfuscation_params() -> dict:
    """
    Генерирует независимый набор параметров обфускации для одного туннеля.
    Следует правилам из CLAUDE.md (секция «Параметры обфускации AmneziaWG»).
    """
    jc = random.randint(4, 12)
    jmin = random.randint(40, 80)
    jmax = min(jmin + random.randint(10, 50), 1279)  # строго < 1280

    s1 = random.randint(15, 150)
    s2 = random.randint(15, 150)
    s3 = random.randint(15, 150)
    s4 = random.randint(15, 150)

    reserved = {0, 1, 2, 3, 4}
    headers: set[int] = set()
    while len(headers) < 4:
        val = secrets.randbits(32)
        if val not in reserved and val not in headers:
            headers.add(val)
    h1, h2, h3, h4 = list(headers)

    return {
        "jc": jc, "jmin": jmin, "jmax": jmax,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4,
        "h1": h1, "h2": h2, "h3": h3, "h4": h4,
    }


async def ensure_obfuscation_params(iface: Interface, session: AsyncSession) -> None:
    """Генерирует и сохраняет параметры обфускации если они ещё не заданы."""
    if iface.obf_h1 is None:
        params = generate_obfuscation_params()
        iface.obf_jc = params["jc"]
        iface.obf_jmin = params["jmin"]
        iface.obf_jmax = params["jmax"]
        iface.obf_s1 = params["s1"]
        iface.obf_s2 = params["s2"]
        iface.obf_s3 = params["s3"]
        iface.obf_s4 = params["s4"]
        iface.obf_h1 = params["h1"]
        iface.obf_h2 = params["h2"]
        iface.obf_h3 = params["h3"]
        iface.obf_h4 = params["h4"]
        iface.obf_generated_at = datetime.now(timezone.utc)
        session.add(iface)
        await session.flush()


# ── Генерация конфигов ───────────────────────────────────────────────────

def _obf_server_lines(iface: Interface) -> str:
    """
    Строки обфускации для серверной стороны туннеля.
    Только симметричные параметры: S1-S4, H1-H4.
    Jc/Jmin/Jmax НЕ включаются (клиент несёт junk, сервер — нет).
    """
    if iface.obf_h1 is None:
        return ""
    lines = []
    for key, val in [
        ("S1", iface.obf_s1), ("S2", iface.obf_s2),
        ("S3", iface.obf_s3), ("S4", iface.obf_s4),
        ("H1", iface.obf_h1), ("H2", iface.obf_h2),
        ("H3", iface.obf_h3), ("H4", iface.obf_h4),
    ]:
        if val is not None:
            lines.append(f"{key} = {val}")
    return "\n".join(lines)


def _obf_client_lines(iface: Interface) -> str:
    """
    Строки обфускации для клиентской стороны туннеля.
    Все параметры: Jc, Jmin, Jmax + S1-S4, H1-H4.
    """
    if iface.obf_h1 is None:
        return ""
    lines = []
    for key, val in [
        ("Jc", iface.obf_jc), ("Jmin", iface.obf_jmin), ("Jmax", iface.obf_jmax),
        ("S1", iface.obf_s1), ("S2", iface.obf_s2),
        ("S3", iface.obf_s3), ("S4", iface.obf_s4),
        ("H1", iface.obf_h1), ("H2", iface.obf_h2),
        ("H3", iface.obf_h3), ("H4", iface.obf_h4),
    ]:
        if val is not None:
            lines.append(f"{key} = {val}")
    return "\n".join(lines)


def generate_interface_config(iface: Interface, peers: list[Peer]) -> str:
    """
    Генерирует wg-формат конфиг для интерфейса.

    awg0 (сервер): [Interface] с S*/H*, [Peer] для каждого клиента.
    awg1 (клиент): [Interface] с Jc/Jmin/Jmax+S*/H*, один [Peer] upstream.
    """
    lines = ["[Interface]"]
    lines.append(f"PrivateKey = {iface.private_key}")

    if iface.mode == InterfaceMode.server:
        lines.append(f"ListenPort = {iface.listen_port}")
        # Сервер: только симметричные параметры (без Junk)
        obf = _obf_server_lines(iface)
        if obf:
            lines.append(obf)
    else:
        # Клиент (awg1): все параметры включая Junk
        obf = _obf_client_lines(iface)
        if obf:
            lines.append(obf)

    lines.append("")

    for peer in peers:
        if not peer.enabled:
            continue
        lines.append("[Peer]")
        lines.append(f"PublicKey = {peer.public_key}")
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        server_allowed_ips = (
            peer.tunnel_address
            if iface.mode == InterfaceMode.server and peer.tunnel_address
            else peer.allowed_ips
        )
        lines.append(f"AllowedIPs = {server_allowed_ips}")
        if peer.persistent_keepalive:
            lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")
        # Для клиентского интерфейса (awg1): endpoint upstream ноды
        if iface.mode == InterfaceMode.client and iface.endpoint:
            lines.append(f"Endpoint = {iface.endpoint}")
        lines.append("")

    return "\n".join(lines)


def generate_client_config(peer: Peer, server: Interface, server_endpoint: str) -> str:
    """
    Генерирует конфиг для скачивания клиентом (пиром awg0).

    [Interface] клиента:
      - PrivateKey = peer.private_key
      - Address = peer.tunnel_address
      - DNS = server.dns
      - Jc/Jmin/Jmax + S*/H* из awg0 (клиент несёт все параметры)

    [Peer] = сервер:
      - PublicKey = server.public_key
      - Endpoint = server_endpoint
      - AllowedIPs = 0.0.0.0/0
    """
    lines = ["[Interface]"]
    if peer.private_key:
        lines.append(f"PrivateKey = {peer.private_key}")
    else:
        lines.append("# PrivateKey = <generated on client>")
    if peer.tunnel_address:
        lines.append(f"Address = {peer.tunnel_address}")
    if server.dns:
        lines.append(f"DNS = {server.dns}")

    # Клиент несёт все параметры обфускации (Jc + S*/H*)
    obf = _obf_client_lines(server)
    if obf:
        lines.append(obf)

    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {server.public_key}")
    if peer.preshared_key:
        lines.append(f"PresharedKey = {peer.preshared_key}")
    lines.append(f"Endpoint = {server_endpoint}")
    lines.append(f"AllowedIPs = {peer.allowed_ips or '0.0.0.0/0'}")
    if peer.persistent_keepalive:
        lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")
    lines.append("")

    return "\n".join(lines)


def generate_qr_bytes(config_str: str) -> bytes:
    """Генерирует PNG-изображение QR-кода для конфига."""
    import qrcode  # type: ignore

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(config_str)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Управление демоном ───────────────────────────────────────────────────

async def _wait_for_socket(ifname: str, timeout: float = 10.0) -> bool:
    """Ждёт появления UNIX-сокета amneziawg-go."""
    # amneziawg-go создаёт сокет в /var/run/amneziawg/ (не в /var/run/wireguard/)
    sock_path = f"/var/run/amneziawg/{ifname}.sock"
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if os.path.exists(sock_path):
            return True
        await asyncio.sleep(0.2)
    return False


def _run_cmd(args: list[str], input_data: Optional[bytes] = None) -> tuple[int, str]:
    """Запускает команду, возвращает (returncode, combined_output)."""
    result = subprocess.run(
        args,
        input=input_data,
        capture_output=True,
        text=(input_data is None),
    )
    if isinstance(result.stdout, bytes):
        out = result.stdout.decode(errors="replace")
        err = result.stderr.decode(errors="replace")
    else:
        out = result.stdout
        err = result.stderr
    return result.returncode, (out + err).strip()


async def apply_interface(iface: Interface, peers: list[Peer]) -> None:
    """
    Запускает или перезапускает amneziawg-go для интерфейса
    и применяет конфигурацию через wg setconf.
    """
    ifname = iface.name
    logger.info("[awg] apply_interface: %s (mode=%s, addr=%s, port=%s, peers=%d)",
                ifname, iface.mode, iface.address, iface.listen_port, len(peers))

    use_kernel = _detect_kernel_mode()
    logger.info("[awg] Using %s mode for %s", "kernel" if use_kernel else "userspace", ifname)

    # Остановить/удалить старый интерфейс если есть
    if ifname in _awg_processes:
        proc = _awg_processes[ifname]
        if proc.poll() is None:
            logger.info("[awg] Stopping existing %s process (pid=%d)", ifname, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.warning("[awg] %s did not terminate, killing", ifname)
                proc.kill()
        del _awg_processes[ifname]

    rc_del, out_del = _run_cmd(["ip", "link", "delete", ifname])
    logger.debug("[awg] ip link delete %s: rc=%d %s", ifname, rc_del, out_del)

    if use_kernel:
        # ── Kernel module: создать интерфейс через ip link ────────────────
        rc, out = _run_cmd(["ip", "link", "add", ifname, "type", "amneziawg"])
        if rc != 0:
            raise RuntimeError(f"ip link add {ifname} type amneziawg failed: {out}")
        logger.info("[awg] Kernel interface %s created", ifname)
    else:
        # ── Userspace: запустить amneziawg-go демон ───────────────────────
        rc_which, which_out = _run_cmd(["which", "amneziawg-go"])
        if rc_which != 0:
            raise RuntimeError("amneziawg-go binary not found in PATH")
        logger.info("[awg] amneziawg-go found at: %s", which_out.strip())

        os.makedirs("/var/run/amneziawg", exist_ok=True)

        logger.info("[awg] Starting amneziawg-go %s...", ifname)
        env = os.environ.copy()
        # Foreground режим — amneziawg-go НЕ форкается в демон.
        # Без этого флага: родитель форкает дочерний процесс и завершается с кодом 0,
        # пока дочерний работает в фоне. Popen отслеживает родителя → exit=0 → мы думаем что упал.
        # С WG_PROCESS_FOREGROUND=1: процесс остаётся живым, сокет создаётся в основном потоке.
        env["WG_PROCESS_FOREGROUND"] = "1"
        proc = subprocess.Popen(
            ["amneziawg-go", ifname],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        _awg_processes[ifname] = proc

        sock_ok = await _wait_for_socket(ifname)
        if not sock_ok:
            exit_code = proc.poll()
            try:
                daemon_out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            except Exception:
                daemon_out = "(could not read output)"
            logger.error(
                "[awg] amneziawg-go failed to create socket for %s. exit_code=%s, output: %s",
                ifname, exit_code, daemon_out or "(no output)"
            )
            raise RuntimeError(
                f"amneziawg-go socket not created for {ifname} "
                f"(exit={exit_code}): {daemon_out[:300]}"
            )
        logger.info("[awg] amneziawg-go socket ready for %s (pid=%d)", ifname, proc.pid)

    # Сгенерировать конфиг (логируем без приватного ключа)
    config_str = generate_interface_config(iface, peers)
    config_preview = "\n".join(
        line if "PrivateKey" not in line and "PresharedKey" not in line else line.split("=")[0] + "= [REDACTED]"
        for line in config_str.splitlines()
    )
    logger.debug("[awg] Config for %s:\n%s", ifname, config_preview)

    # Записать конфиг во временный файл и применить
    # mode=0o600 — только владелец может читать (файл содержит приватный ключ)
    fd, conf_path = tempfile.mkstemp(suffix=".conf")
    try:
        os.chmod(conf_path, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(config_str)
        rc, out = _run_cmd(["awg", "setconf", ifname, conf_path])
        logger.info("[awg] awg setconf %s: rc=%d %s", ifname, rc, out)
        if rc != 0:
            raise RuntimeError(f"awg setconf failed: {out}")
    finally:
        try:
            os.unlink(conf_path)
        except OSError:
            pass

    # Назначить IP-адрес
    _run_cmd(["ip", "addr", "flush", "dev", ifname])
    addr = iface.address
    rc, out = _run_cmd(["ip", "addr", "add", addr, "dev", ifname])
    logger.info("[awg] ip addr add %s dev %s: rc=%d %s", addr, ifname, rc, out)
    if rc != 0:
        raise RuntimeError(f"ip addr add failed: {out}")

    rc, out = _run_cmd(["ip", "link", "set", ifname, "up"])
    logger.info("[awg] ip link set %s up: rc=%d %s", ifname, rc, out)
    if rc != 0:
        raise RuntimeError(f"ip link set up failed: {out}")

    mtu = _INTERFACE_MTU.get(ifname)
    if mtu:
        rc, out = _run_cmd(["ip", "link", "set", "dev", ifname, "mtu", mtu])
        logger.info("[awg] ip link set dev %s mtu %s: rc=%d %s", ifname, mtu, rc, out)
        if rc != 0:
            raise RuntimeError(f"ip link set mtu failed: {out}")

    logger.info("[awg] Interface %s is UP ✓", ifname)


async def sync_peers(iface: Interface, peers: list[Peer]) -> None:
    """Hot-reload пиров через wg syncconf (без перезапуска демона)."""
    ifname = iface.name
    config_str = generate_interface_config(iface, peers)
    fd, conf_path = tempfile.mkstemp(suffix=".conf")
    try:
        os.chmod(conf_path, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(config_str)
        rc, out = _run_cmd(["awg", "syncconf", ifname, conf_path])
        if rc != 0:
            raise RuntimeError(f"awg syncconf failed: {out}")
    finally:
        try:
            os.unlink(conf_path)
        except OSError:
            pass


async def stop_interface(ifname: str) -> None:
    """Останавливает интерфейс и завершает демон (если userspace)."""
    _run_cmd(["ip", "link", "delete", ifname])
    if ifname in _awg_processes:
        proc = _awg_processes.pop(ifname)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    logger.info("[awg] Interface %s stopped", ifname)


# ── Статус ───────────────────────────────────────────────────────────────

def get_status() -> dict:
    """
    Парсит вывод `wg show all dump`.

    Формат строк:
      interface  private_key  public_key  listen_port  fwmark
      <TAB>peer_pubkey  psk  endpoint  allowed_ips  handshake  rx  tx  keepalive
    """
    rc, output = _run_cmd(["awg", "show", "all", "dump"])
    if rc != 0:
        return {}

    result: dict[str, dict] = {}
    current_iface: Optional[str] = None

    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 4 and parts[3].isdigit():
            ifname, priv, pub, port = (parts + [""] * 4)[:4]
            current_iface = ifname
            result[ifname] = {
                "name": ifname,
                "public_key": pub,
                "listen_port": int(port) if port.isdigit() else None,
                "running": ifname in _awg_processes,
                "peers": {},
            }
        elif len(parts) >= 9:
            ifname, pubkey, psk, endpoint, allowed_ips, handshake, rx, tx, keepalive = (
                parts + [""] * 9
            )[:9]
            ifname = ifname.strip()
            if ifname not in result:
                result[ifname] = {
                    "name": ifname,
                    "public_key": "",
                    "listen_port": None,
                    "running": ifname in _awg_processes,
                    "peers": {},
                }
            peer_data = {
                "public_key": pubkey,
                "endpoint": endpoint if endpoint != "(none)" else None,
                "allowed_ips": allowed_ips,
                "latest_handshake": int(handshake) if handshake.isdigit() else 0,
                "rx_bytes": int(rx) if rx.isdigit() else 0,
                "tx_bytes": int(tx) if tx.isdigit() else 0,
                "persistent_keepalive": int(keepalive) if keepalive.isdigit() else None,
            }
            result[ifname]["peers"][pubkey] = peer_data
    return result


def is_running(ifname: str) -> bool:
    if _detect_kernel_mode():
        # Kernel mode: проверяем наличие интерфейса
        rc, _ = _run_cmd(["ip", "link", "show", ifname])
        return rc == 0
    # Userspace mode: проверяем живой процесс
    if ifname not in _awg_processes:
        return False
    return _awg_processes[ifname].poll() is None


async def load_interface(iface: Interface, session: AsyncSession) -> None:
    """
    Загружает интерфейс из БД и применяет.
    Вспомогательная функция для lifespan.
    """
    result = await session.execute(
        select(Peer).where(Peer.interface_id == iface.id, Peer.enabled == True)  # noqa: E712
    )
    peers = list(result.scalars().all())

    if iface.mode == InterfaceMode.client and iface.name == "awg1" and not peers:
        active_node = await session.scalar(
            select(UpstreamNode).where(
                UpstreamNode.is_active == True,  # noqa: E712
                UpstreamNode.status == NodeStatus.online,
                UpstreamNode.public_key.isnot(None),
                UpstreamNode.awg_address.isnot(None),
            )
        )
        if active_node:
            iface.endpoint = f"{active_node.host}:{active_node.awg_port}"
            iface.allowed_ips = iface.allowed_ips or "0.0.0.0/0"
            peers = [
                Peer(
                    interface_id=iface.id,
                    name=f"upstream-node-{active_node.id}",
                    public_key=active_node.public_key,
                    preshared_key=active_node.preshared_key,
                    allowed_ips=iface.allowed_ips,
                    persistent_keepalive=iface.persistent_keepalive,
                    enabled=True,
                )
            ]
            logger.info(
                "[awg] Loaded active upstream node %d into awg1 config",
                active_node.id,
            )

    await apply_interface(iface, peers)
