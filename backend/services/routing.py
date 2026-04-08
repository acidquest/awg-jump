"""
Policy routing manager — ip rule/route + iptables mangle/nat.

Политика:
  fwmark FWMARK_LOCAL → table ROUTING_TABLE_LOCAL → default via eth0
  fwmark FWMARK_VPN → table ROUTING_TABLE_VPN → default dev awg1
"""
import logging
import re
import subprocess
from typing import Optional

from backend.config import settings
import backend.services.ipset_manager as ipset_mgr

logger = logging.getLogger(__name__)
_GEOIP_IPSET_NAME = "geoip_local"
_VPN_ROUTE_METRIC_PRIMARY = "100"
_VPN_ROUTE_METRIC_FALLBACK = "200"
_DNS_OUTPUT_PROTOCOLS = ("udp", "tcp")


def _run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _get_default_gateway(iface: Optional[str] = None) -> str:
    """Возвращает IP шлюза по умолчанию для физического интерфейса."""
    args = ["ip", "route", "show", "default"]
    if iface:
        args += ["dev", iface]
    rc, out = _run(args)
    match = re.search(r"default via (\S+)", out)
    if match:
        return match.group(1)
    # Fallback — любой default route
    rc2, out2 = _run(["ip", "route", "show", "default"])
    match2 = re.search(r"default via (\S+)", out2)
    return match2.group(1) if match2 else ""


def _rule_exists(fwmark: str, table: int) -> bool:
    rc, out = _run(["ip", "rule", "show"])
    return f"fwmark {fwmark}" in out and f"lookup {table}" in out


def _ipt_rule_exists(table: str, chain: str, rule_args: list[str]) -> bool:
    rc, _ = _run(["iptables", "-t", table, "-C", chain] + rule_args)
    return rc == 0


def _ipt_add(table: str, chain: str, rule_args: list[str]) -> None:
    """Добавляет правило iptables если его ещё нет."""
    if not _ipt_rule_exists(table, chain, rule_args):
        rc, out = _run(["iptables", "-t", table, "-A", chain] + rule_args)
        if rc != 0:
            raise RuntimeError(f"iptables -A {chain} failed: {out}")


def _ipt_del(table: str, chain: str, rule_args: list[str]) -> None:
    """Удаляет правило iptables если оно есть."""
    while _ipt_rule_exists(table, chain, rule_args):
        _run(["iptables", "-t", table, "-D", chain] + rule_args)


def _mark_rules(invert_geoip: bool) -> dict[str, str | list[str]]:
    geoip_mark = settings.fwmark_vpn if invert_geoip else settings.fwmark_local
    other_mark = settings.fwmark_local if invert_geoip else settings.fwmark_vpn

    return {
        "geoip_mark": geoip_mark,
        "other_mark": other_mark,
        "prerouting_geoip": [
            "-i", "awg0",
            "-m", "set", "--match-set", _GEOIP_IPSET_NAME, "dst",
            "-j", "MARK", "--set-mark", geoip_mark,
        ],
        "prerouting_other": [
            "-i", "awg0",
            "-m", "set", "!", "--match-set", _GEOIP_IPSET_NAME, "dst",
            "-j", "MARK", "--set-mark", other_mark,
        ],
        "output_geoip": [
            "-m", "set", "--match-set", _GEOIP_IPSET_NAME, "dst",
            "-j", "MARK", "--set-mark", geoip_mark,
        ],
        "output_other": [
            "-m", "set", "!", "--match-set", _GEOIP_IPSET_NAME, "dst",
            "-j", "MARK", "--set-mark", other_mark,
        ],
    }


def _remove_all_policy_mark_rules() -> None:
    for invert_geoip in (False, True):
        rules = _mark_rules(invert_geoip)
        _ipt_del("mangle", "PREROUTING", rules["prerouting_geoip"])  # type: ignore[arg-type]
        _ipt_del("mangle", "PREROUTING", rules["prerouting_other"])  # type: ignore[arg-type]
        for proto in _DNS_OUTPUT_PROTOCOLS:
            _ipt_del("mangle", "OUTPUT", [
                "-p", proto,
                "--dport", "53",
                *rules["output_geoip"],  # type: ignore[list-item]
            ])
            _ipt_del("mangle", "OUTPUT", [
                "-p", proto,
                "--dport", "53",
                *rules["output_other"],  # type: ignore[list-item]
            ])


def _ensure_route(table: int, route_args: list[str], *, description: str) -> None:
    rc, out = _run(["ip", "route", "replace"] + route_args + ["table", str(table)])
    if rc != 0:
        logger.warning("%s failed: %s", description, out)
    else:
        logger.info("%s", description)


def _delete_route(table: int, route_args: list[str], *, description: str) -> None:
    while True:
        rc, out = _run(["ip", "route", "del"] + route_args + ["table", str(table)])
        if rc != 0:
            if out:
                logger.info("%s: %s", description, out)
            break


def update_upstream_host_route(peer_address: Optional[str], interface_name: str = "awg1") -> None:
    """
    Обновляет host-route до tunnel IP активной upstream-ноды в main table.
    Без этого пакеты к 10.20.0.x уходят в eth0, а не в awg1.
    """
    subnet = settings.node_vpn_subnet
    rc, out = _run(["ip", "-4", "route", "show", "table", "main", subnet])
    if rc == 0 and out:
        for line in out.splitlines():
            route = line.strip()
            if not route or "dev lo" in route:
                continue
            if peer_address and route.startswith(peer_address.split("/")[0]):
                continue
            _run(["ip", "route", "del"] + route.split())

    if peer_address:
        _ensure_route(
            254,
            [peer_address, "dev", interface_name],
            description=f"Main table: upstream host route {peer_address} dev {interface_name}",
        )


def _ensure_geoip_ipset() -> None:
    """
    Гарантирует существование geoip ipset до установки iptables правил.
    Иначе PREROUTING с --match-set падает, и NAT/MASQUERADE не успевает примениться.
    """
    if ipset_mgr.exists(_GEOIP_IPSET_NAME):
        return
    ipset_mgr.create(_GEOIP_IPSET_NAME)
    logger.warning("Created missing ipset %s as empty set", _GEOIP_IPSET_NAME)


def setup_policy_routing() -> None:
    """
    Создаёт ip rule и ip route для policy routing.
    Идемпотентно — проверяет существование перед добавлением.
    """
    fwmark_local = settings.fwmark_local
    fwmark_vpn = settings.fwmark_vpn
    table_local = settings.routing_table_local
    table_vpn = settings.routing_table_vpn
    phys_iface = settings.physical_iface

    # ip rule: fwmark → таблица
    if not _rule_exists(fwmark_local, table_local):
        rc, out = _run(["ip", "rule", "add", "fwmark", fwmark_local, "table", str(table_local)])
        if rc != 0:
            raise RuntimeError(f"ip rule add LOCAL failed: {out}")
        logger.info("Added ip rule: fwmark %s → table %d", fwmark_local, table_local)

    if not _rule_exists(fwmark_vpn, table_vpn):
        rc, out = _run(["ip", "rule", "add", "fwmark", fwmark_vpn, "table", str(table_vpn)])
        if rc != 0:
            raise RuntimeError(f"ip rule add VPN failed: {out}")
        logger.info("Added ip rule: fwmark %s → table %d", fwmark_vpn, table_vpn)

    # ip route: default в каждой таблице
    gw = _get_default_gateway(phys_iface)
    if gw:
        _ensure_route(
            table_local,
            ["default", "via", gw, "dev", phys_iface],
            description=f"LOCAL table: default via {gw} dev {phys_iface}",
        )
    else:
        logger.warning("Cannot determine default gateway for %s", phys_iface)

    update_vpn_route("awg1", fallback_gateway=gw)


def update_vpn_route(
    interface_name: Optional[str],
    fallback_gateway: Optional[str] = None,
) -> None:
    """
    Обновляет маршруты в VPN-таблице.
    Если interface_name задан — трафик идёт через awg1, а eth0 остаётся резервом.
    Если interface_name=None — primary route через awg1 удаляется и остаётся только eth0 fallback.
    """
    table_vpn = settings.routing_table_vpn
    phys_iface = settings.physical_iface

    if interface_name:
        _ensure_route(
            table_vpn,
            ["default", "dev", interface_name, "metric", _VPN_ROUTE_METRIC_PRIMARY],
            description=(
                f"VPN table: primary default dev {interface_name} "
                f"metric {_VPN_ROUTE_METRIC_PRIMARY} (table {table_vpn})"
            ),
        )
    else:
        _delete_route(
            table_vpn,
            ["default", "dev", "awg1", "metric", _VPN_ROUTE_METRIC_PRIMARY],
            description="VPN table: removed primary default via awg1",
        )

    gw = fallback_gateway or _get_default_gateway(phys_iface)
    if gw:
        _ensure_route(
            table_vpn,
            ["default", "via", gw, "dev", phys_iface, "metric", _VPN_ROUTE_METRIC_FALLBACK],
            description=(
                f"VPN table: fallback default via {gw} dev {phys_iface} "
                f"metric {_VPN_ROUTE_METRIC_FALLBACK} (table {table_vpn})"
            ),
        )
    else:
        logger.warning("Cannot determine fallback gateway for VPN table on %s", phys_iface)


def setup_iptables(invert_geoip: bool = False) -> None:
    """
    Настраивает правила iptables для policy routing + NAT.
    Идемпотентно.
    """
    phys_iface = settings.physical_iface
    rules = _mark_rules(invert_geoip)

    _ensure_geoip_ipset()
    _remove_all_policy_mark_rules()

    # mangle PREROUTING: fwmark для трафика от AWG-клиентов (-i awg0).
    # Ограничение по интерфейсу обязательно: без него маркируются и ответные пакеты
    # из интернета, что ломает маршрутизацию обратно к клиентам.
    _ipt_add("mangle", "PREROUTING", rules["prerouting_geoip"])  # type: ignore[arg-type]
    _ipt_add("mangle", "PREROUTING", rules["prerouting_other"])  # type: ignore[arg-type]
    logger.info("iptables mangle PREROUTING rules configured")

    # mangle OUTPUT: fwmark только для DNS-трафика самого контейнера.
    # PREROUTING не охватывает locally-generated пакеты — для них нужна цепочка OUTPUT.
    # Ограничиваемся DNS, чтобы не ломать обычный container-to-container трафик
    # (например nginx -> awg-jump по Docker bridge).
    for proto in _DNS_OUTPUT_PROTOCOLS:
        _ipt_add("mangle", "OUTPUT", ["-p", proto, "--dport", "53", *rules["output_geoip"]])  # type: ignore[list-item]
        _ipt_add("mangle", "OUTPUT", ["-p", proto, "--dport", "53", *rules["output_other"]])  # type: ignore[list-item]
    logger.info("iptables mangle OUTPUT rules configured (DNS only)")

    # nat POSTROUTING: MASQUERADE исходящего трафика
    _ipt_add("nat", "POSTROUTING", ["-o", phys_iface, "-j", "MASQUERADE"])
    _ipt_add("nat", "POSTROUTING", ["-o", "awg1", "-j", "MASQUERADE"])
    logger.info("iptables NAT MASQUERADE rules configured")


def teardown() -> None:
    """Удаляет все установленные правила (для тестов и graceful shutdown)."""
    fwmark_local = settings.fwmark_local
    fwmark_vpn = settings.fwmark_vpn
    table_local = settings.routing_table_local
    table_vpn = settings.routing_table_vpn
    phys_iface = settings.physical_iface

    # ip rule
    _run(["ip", "rule", "del", "fwmark", fwmark_local, "table", str(table_local)])
    _run(["ip", "rule", "del", "fwmark", fwmark_vpn, "table", str(table_vpn)])

    # ip route: полностью очищаем управляемые таблицы, т.к. в VPN-таблице
    # теперь может быть и primary route через awg1, и fallback через physical iface.
    _run(["ip", "route", "flush", "table", str(table_local)])
    _run(["ip", "route", "flush", "table", str(table_vpn)])

    _remove_all_policy_mark_rules()

    # iptables nat (не трогаем — могут использоваться другими процессами)
    logger.info("Routing teardown complete")


def get_status(invert_geoip: bool = False) -> dict:
    """Возвращает текущее состояние правил маршрутизации."""
    fwmark_local = settings.fwmark_local
    fwmark_vpn = settings.fwmark_vpn
    table_local = settings.routing_table_local
    table_vpn = settings.routing_table_vpn
    phys_iface = settings.physical_iface
    rules = _mark_rules(invert_geoip)

    _, rules_out = _run(["ip", "rule", "show"])
    _, route_local_out = _run(["ip", "route", "show", "table", str(table_local)])
    _, route_vpn_out = _run(["ip", "route", "show", "table", str(table_vpn)])

    return {
        "rule_local": _rule_exists(fwmark_local, table_local),
        "rule_vpn": _rule_exists(fwmark_vpn, table_vpn),
        "route_local": route_local_out.strip() or None,
        "route_vpn": route_vpn_out.strip() or None,
        "invert_geoip": invert_geoip,
        "geoip_mark": rules["geoip_mark"],
        "other_mark": rules["other_mark"],
        "prerouting_geoip": _ipt_rule_exists("mangle", "PREROUTING", rules["prerouting_geoip"]),  # type: ignore[arg-type]
        "prerouting_other": _ipt_rule_exists("mangle", "PREROUTING", rules["prerouting_other"]),  # type: ignore[arg-type]
        "nat_eth0": _ipt_rule_exists("nat", "POSTROUTING", ["-o", phys_iface, "-j", "MASQUERADE"]),
        "nat_awg1": _ipt_rule_exists("nat", "POSTROUTING", ["-o", "awg1", "-j", "MASQUERADE"]),
        "output_geoip": all(
            _ipt_rule_exists("mangle", "OUTPUT", [
                "-p", proto,
                "--dport", "53",
                *rules["output_geoip"],  # type: ignore[list-item]
            ])
            for proto in _DNS_OUTPUT_PROTOCOLS
        ),
        "output_other": all(
            _ipt_rule_exists("mangle", "OUTPUT", [
                "-p", proto,
                "--dport", "53",
                *rules["output_other"],  # type: ignore[list-item]
            ])
            for proto in _DNS_OUTPUT_PROTOCOLS
        ),
        "geoip_destination": "vpn" if invert_geoip else "local",
        "other_destination": "local" if invert_geoip else "vpn",
        "ip_rules": [line.strip() for line in rules_out.splitlines() if line.strip()],
        "ip_routes": {
            str(table_local): [line.strip() for line in route_local_out.splitlines() if line.strip()],
            str(table_vpn): [line.strip() for line in route_vpn_out.splitlines() if line.strip()],
        },
        "physical_iface": phys_iface,
    }
