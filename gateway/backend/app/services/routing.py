from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone

from app.config import settings
from app.models import EntryNode, GatewaySettings, RoutingPolicy, TrafficSourceMode, TunnelStatus
from app.services.geoip import load_cached_country
from app.services import ipset_manager


logger = logging.getLogger(__name__)

MANGLE_PREROUTING_CHAIN = "AWG_GW_PREROUTING"
MANGLE_OUTPUT_CHAIN = "AWG_GW_OUTPUT"
FILTER_FORWARD_CHAIN = "AWG_GW_FORWARD"
FILTER_OUTPUT_CHAIN = "AWG_GW_OUTPUT"
NAT_POSTROUTING_CHAIN = "AWG_GW_POSTROUTING"


def _run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def _run_logged(args: list[str]) -> None:
    rc, out = _run(args)
    if rc != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {out}")


def _merge_prefixes(policy: RoutingPolicy) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for country in policy.geoip_countries:
        for prefix in load_cached_country(country):
            if prefix not in seen:
                merged.append(prefix)
                seen.add(prefix)
    for prefix in policy.manual_prefixes:
        if prefix not in seen:
            merged.append(prefix)
            seen.add(prefix)
    return merged


def _default_route() -> tuple[str | None, str | None]:
    rc, out = _run(["ip", "route", "show", "default"])
    if rc != 0:
        return None, None
    match = re.search(r"default via (\S+) dev (\S+)", out)
    if not match:
        return None, None
    return match.group(2), match.group(1)


def _source_selectors(gateway_settings: GatewaySettings) -> list[str]:
    if gateway_settings.traffic_source_mode == TrafficSourceMode.localhost.value:
        return ["127.0.0.1/32"]
    if gateway_settings.traffic_source_mode == TrafficSourceMode.selected_cidr.value:
        return list(gateway_settings.allowed_client_cidrs)
    if gateway_settings.traffic_source_mode == TrafficSourceMode.selected_hosts.value:
        return [f"{host}/32" for host in gateway_settings.allowed_client_hosts]
    return []


def _ensure_chain(table: str, chain: str) -> None:
    rc, _ = _run(["iptables", "-t", table, "-nL", chain])
    if rc != 0:
        _run_logged(["iptables", "-t", table, "-N", chain])
    _run_logged(["iptables", "-t", table, "-F", chain])


def _ensure_jump(table: str, builtin_chain: str, target_chain: str) -> None:
    rc, _ = _run(["iptables", "-t", table, "-C", builtin_chain, "-j", target_chain])
    if rc != 0:
        _run_logged(["iptables", "-t", table, "-I", builtin_chain, "-j", target_chain])


def _append(table: str, chain: str, rule_args: list[str]) -> None:
    _run_logged(["iptables", "-t", table, "-A", chain, *rule_args])


def _ensure_ip_rule() -> None:
    rc, out = _run(["ip", "rule", "show"])
    if f"fwmark {settings.fwmark_vpn}" not in out or f"lookup {settings.routing_table_vpn}" not in out:
        _run_logged(["ip", "rule", "add", "fwmark", settings.fwmark_vpn, "table", str(settings.routing_table_vpn)])


def _interface_exists(interface_name: str) -> bool:
    rc, _ = _run(["ip", "link", "show", "dev", interface_name])
    return rc == 0


def _ensure_table_routes() -> None:
    default_iface, default_gateway = _default_route()
    if not default_iface or not default_gateway:
        raise RuntimeError("Cannot determine default route for gateway host interface")
    _run_logged(
        ["ip", "route", "replace", "default", "dev", settings.tunnel_interface, "table", str(settings.routing_table_vpn)]
    )


def _physical_interface() -> str:
    default_iface, _ = _default_route()
    if not default_iface:
        raise RuntimeError("Cannot determine default route for gateway host interface")
    return default_iface


def _ensure_ipset(policy: RoutingPolicy, prefixes: list[str]) -> None:
    ipset_manager.create_or_update(policy.geoip_ipset_name, prefixes)


def _iptables_command(table: str, chain: str, rule_args: list[str]) -> str:
    return " ".join(["iptables", "-t", table, "-A", chain, *rule_args])


def _build_marking_rules(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    prefixes: list[str],
    active_node: EntryNode,
) -> list[tuple[str, str, list[str]]]:
    selectors = _source_selectors(gateway_settings)
    rules: list[tuple[str, str, list[str]]] = []

    rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-d", f"{active_node.endpoint_host}/32", "-j", "RETURN"]))
    rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-d", f"{active_node.endpoint_host}/32", "-j", "RETURN"]))

    if gateway_settings.traffic_source_mode == TrafficSourceMode.localhost.value:
        if policy.geoip_enabled and prefixes and not policy.invert_geoip:
            rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-m", "set", "--match-set", policy.geoip_ipset_name, "dst", "-j", "RETURN"]))
        if policy.geoip_enabled and prefixes and policy.invert_geoip:
            rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-m", "set", "--match-set", policy.geoip_ipset_name, "dst", "-j", "MARK", "--set-mark", settings.fwmark_vpn]))
        elif policy.default_policy == "vpn":
            rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-j", "MARK", "--set-mark", settings.fwmark_vpn]))

    for selector in selectors:
        if selector == "127.0.0.1/32":
            continue
        if policy.geoip_enabled and prefixes and not policy.invert_geoip:
            rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-s", selector, "-m", "set", "--match-set", policy.geoip_ipset_name, "dst", "-j", "RETURN"]))
        if policy.geoip_enabled and prefixes and policy.invert_geoip:
            rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-s", selector, "-m", "set", "--match-set", policy.geoip_ipset_name, "dst", "-j", "MARK", "--set-mark", settings.fwmark_vpn]))
        elif policy.default_policy == "vpn":
            rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-s", selector, "-j", "MARK", "--set-mark", settings.fwmark_vpn]))

    return rules


def build_routing_plan(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    active_node: EntryNode | None,
) -> dict:
    selectors = _source_selectors(gateway_settings)
    cached_prefixes = _merge_prefixes(policy) if policy.geoip_enabled else []
    warnings: list[str] = []
    default_iface, default_gateway = _default_route()

    if active_node is None:
        warnings.append("No active entry node selected")
    if not default_iface or not default_gateway:
        warnings.append("Default host route is missing")
    if gateway_settings.tunnel_status != TunnelStatus.running.value:
        warnings.append("Tunnel is not running")
    if gateway_settings.tunnel_status == TunnelStatus.running.value and not _interface_exists(settings.tunnel_interface):
        warnings.append(f"Tunnel interface {settings.tunnel_interface} is missing")
    if policy.geoip_enabled and not cached_prefixes:
        warnings.append("GeoIP cache is empty")

    safe_to_apply = (
        active_node is not None
        and default_iface is not None
        and gateway_settings.tunnel_status == TunnelStatus.running.value
        and _interface_exists(settings.tunnel_interface)
        and (not policy.strict_mode or not policy.geoip_enabled or bool(cached_prefixes))
    )

    commands: list[str] = [
        f"ip rule add fwmark {settings.fwmark_vpn} table {settings.routing_table_vpn}",
    ]
    if active_node is not None:
        commands.extend(
            [
                f"ip route replace default dev {settings.tunnel_interface} table {settings.routing_table_vpn}",
                f"ipset create/update {policy.geoip_ipset_name} ({len(cached_prefixes)} prefixes)",
                f"iptables -t nat -A {NAT_POSTROUTING_CHAIN} -o {settings.tunnel_interface} -j MASQUERADE",
                f"iptables -t filter -A {FILTER_FORWARD_CHAIN} -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j ACCEPT",
            ]
        )
        commands.extend(
            _iptables_command(table, chain, rule)
            for table, chain, rule in _build_marking_rules(gateway_settings, policy, cached_prefixes, active_node)
        )
        if default_iface is not None:
            for selector in selectors:
                if selector == "127.0.0.1/32":
                    continue
                commands.extend(
                    [
                        f"iptables -t nat -A {NAT_POSTROUTING_CHAIN} -s {selector} -o {default_iface} -j MASQUERADE",
                        f"iptables -t filter -A {FILTER_FORWARD_CHAIN} -s {selector} -o {default_iface} -j ACCEPT",
                        f"iptables -t filter -A {FILTER_FORWARD_CHAIN} -i {default_iface} -d {selector} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
                    ]
                )
    if policy.kill_switch_enabled:
        action = "REJECT" if policy.strict_mode else "DROP"
        commands.append(
            f"iptables -t filter -A {FILTER_OUTPUT_CHAIN} ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j {action}"
        )
        commands.append(
            f"iptables -t filter -A {FILTER_FORWARD_CHAIN} ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j {action}"
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_mode": gateway_settings.traffic_source_mode,
        "selectors": selectors,
        "geoip_prefix_count": len(cached_prefixes),
        "manual_prefixes": policy.manual_prefixes,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "strict_mode": policy.strict_mode,
        "warnings": warnings,
        "commands": commands,
        "safe_to_apply": safe_to_apply,
    }


def apply_routing_plan(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    active_node: EntryNode | None,
) -> dict:
    plan = build_routing_plan(gateway_settings, policy, active_node)
    if active_node is None:
        raise RuntimeError("No active entry node selected")
    if not plan["safe_to_apply"]:
        raise RuntimeError("Routing plan is not safe to apply")
    if gateway_settings.tunnel_status != TunnelStatus.running.value:
        raise RuntimeError("Tunnel is not running")
    if not _interface_exists(settings.tunnel_interface):
        raise RuntimeError(f"Tunnel interface {settings.tunnel_interface} is missing")

    prefixes = _merge_prefixes(policy) if policy.geoip_enabled else []
    selectors = _source_selectors(gateway_settings)
    default_iface = _physical_interface()
    _ensure_ip_rule()
    _ensure_table_routes()
    _ensure_chain("mangle", MANGLE_PREROUTING_CHAIN)
    _ensure_chain("mangle", MANGLE_OUTPUT_CHAIN)
    _ensure_chain("filter", FILTER_FORWARD_CHAIN)
    _ensure_chain("filter", FILTER_OUTPUT_CHAIN)
    _ensure_chain("nat", NAT_POSTROUTING_CHAIN)
    _ensure_jump("mangle", "PREROUTING", MANGLE_PREROUTING_CHAIN)
    _ensure_jump("mangle", "OUTPUT", MANGLE_OUTPUT_CHAIN)
    _ensure_jump("filter", "FORWARD", FILTER_FORWARD_CHAIN)
    _ensure_jump("filter", "OUTPUT", FILTER_OUTPUT_CHAIN)
    _ensure_jump("nat", "POSTROUTING", NAT_POSTROUTING_CHAIN)

    if policy.geoip_enabled:
        _ensure_ipset(policy, prefixes)

    for table, chain, rule in _build_marking_rules(gateway_settings, policy, prefixes, active_node):
        _append(table, chain, rule)

    _append("nat", NAT_POSTROUTING_CHAIN, ["-o", settings.tunnel_interface, "-j", "MASQUERADE"])
    for selector in selectors:
        if selector == "127.0.0.1/32":
            continue
        _append("nat", NAT_POSTROUTING_CHAIN, ["-s", selector, "-o", default_iface, "-j", "MASQUERADE"])
        _append("filter", FILTER_FORWARD_CHAIN, ["-s", selector, "-o", default_iface, "-j", "ACCEPT"])
        _append("filter", FILTER_FORWARD_CHAIN, ["-i", default_iface, "-d", selector, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"])
    _append("filter", FILTER_FORWARD_CHAIN, ["-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", "ACCEPT"])
    _append("filter", FILTER_FORWARD_CHAIN, ["-i", settings.tunnel_interface, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"])

    if policy.kill_switch_enabled:
        action = "REJECT" if policy.strict_mode else "DROP"
        _append("filter", FILTER_OUTPUT_CHAIN, ["!", "-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", action])
        _append("filter", FILTER_FORWARD_CHAIN, ["!", "-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", action])

    logger.info("[awg-routing] applied routing plan source_mode=%s kill_switch=%s strict_mode=%s", gateway_settings.traffic_source_mode, policy.kill_switch_enabled, policy.strict_mode)
    return plan
