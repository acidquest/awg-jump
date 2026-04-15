from __future__ import annotations

import logging
import pwd
import re
import subprocess
from datetime import datetime, timezone

from app.config import settings
from app.models import EntryNode, GatewaySettings, RoutingPolicy, TunnelStatus
from app.services.external_ip import effective_fqdn_prefixes
from app.services import ipset_manager, nftables_manager
from app.services.geoip import load_cached_country
from app.services.traffic_sources import localhost_selector_enabled, non_localhost_selectors, source_selectors


logger = logging.getLogger(__name__)

IPTABLES_BACKEND = "iptables"
NFTABLES_BACKEND = "nftables"

MANGLE_PREROUTING_CHAIN = "AWG_GW_PREROUTING"
MANGLE_FORWARD_CHAIN = "AWG_GW_FORWARD_MANGLE"
MANGLE_OUTPUT_CHAIN = "AWG_GW_OUTPUT"
FILTER_FORWARD_CHAIN = "AWG_GW_FORWARD"
FILTER_OUTPUT_CHAIN = "AWG_GW_OUTPUT"
NAT_POSTROUTING_CHAIN = "AWG_GW_POSTROUTING"
NAT_DNS_PREROUTING_CHAIN = "AWG_GW_DNS_PREROUTING"
NAT_DNS_OUTPUT_CHAIN = "AWG_GW_DNS_OUTPUT"

NFT_CHAIN_MANGLE_PREROUTING = "mangle_prerouting"
NFT_CHAIN_MANGLE_FORWARD = "mangle_forward"
NFT_CHAIN_MANGLE_OUTPUT = "mangle_output"
NFT_CHAIN_FILTER_OUTPUT = "filter_output"
NFT_CHAIN_NAT_PREROUTING = "nat_prerouting"
NFT_CHAIN_NAT_OUTPUT = "nat_output"
NFT_CHAIN_NAT_POSTROUTING = "nat_postrouting"

DNS_RUNTIME_USER = "nobody"


def firewall_backend(gateway_settings: GatewaySettings | None) -> str:
    return NFTABLES_BACKEND if getattr(gateway_settings, "experimental_nftables", False) else IPTABLES_BACKEND


def firewall_set_label(gateway_settings: GatewaySettings | None) -> str:
    return "nft set" if firewall_backend(gateway_settings) == NFTABLES_BACKEND else "ipset"


def geoip_ipset_name(policy: RoutingPolicy) -> str:
    return f"{policy.geoip_ipset_name}_geoip"


def manual_ipset_name(policy: RoutingPolicy) -> str:
    return f"{policy.geoip_ipset_name}_manual"


def fqdn_ipset_name(policy: RoutingPolicy) -> str:
    return f"{policy.geoip_ipset_name}_fqdn"


def _dns_runtime_uid() -> int | None:
    try:
        return pwd.getpwnam(DNS_RUNTIME_USER).pw_uid
    except KeyError:
        return None


def _run(args: list[str], input_data: str | None = None) -> tuple[int, str]:
    result = subprocess.run(args, input=input_data, capture_output=True, text=True, check=False)
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def _run_logged(args: list[str], input_data: str | None = None) -> None:
    rc, out = _run(args, input_data=input_data)
    if rc != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {out}")


def _merge_prefixes(policy: RoutingPolicy, gateway_settings: GatewaySettings | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    if policy.countries_enabled:
        for country in policy.geoip_countries:
            for prefix in load_cached_country(country):
                if prefix not in seen:
                    merged.append(prefix)
                    seen.add(prefix)
    if policy.manual_prefixes_enabled:
        for prefix in policy.manual_prefixes:
            if prefix not in seen:
                merged.append(prefix)
                seen.add(prefix)
    if not policy.countries_enabled and not policy.manual_prefixes_enabled and not effective_fqdn_prefixes(policy, gateway_settings):
        merged.append("0.0.0.0/0")
    return merged


def _country_prefixes(policy: RoutingPolicy) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    if policy.countries_enabled:
        for country in policy.geoip_countries:
            for prefix in load_cached_country(country):
                if prefix not in seen:
                    prefixes.append(prefix)
                    seen.add(prefix)
    return prefixes


def _manual_prefixes(policy: RoutingPolicy) -> list[str]:
    if not policy.manual_prefixes_enabled:
        return []
    return list(dict.fromkeys(policy.manual_prefixes))


def _static_match_sets(policy: RoutingPolicy, gateway_settings: GatewaySettings | None = None) -> list[str]:
    sets: list[str] = []
    if policy.countries_enabled:
        sets.append(geoip_ipset_name(policy))
    if policy.manual_prefixes_enabled:
        sets.append(manual_ipset_name(policy))
    if not policy.countries_enabled and not policy.manual_prefixes_enabled and not effective_fqdn_prefixes(policy, gateway_settings):
        sets.append(policy.geoip_ipset_name)
    return sets


def _all_match_sets(policy: RoutingPolicy, gateway_settings: GatewaySettings | None = None) -> list[str]:
    sets = _static_match_sets(policy, gateway_settings)
    if effective_fqdn_prefixes(policy, gateway_settings):
        sets.append(fqdn_ipset_name(policy))
    return sets


def _set_manager(gateway_settings: GatewaySettings | None):
    return nftables_manager if firewall_backend(gateway_settings) == NFTABLES_BACKEND else ipset_manager


def build_prefix_summary(policy: RoutingPolicy, gateway_settings: GatewaySettings | None = None) -> dict:
    merged = _merge_prefixes(policy, gateway_settings)
    country_prefixes = _country_prefixes(policy)
    manual_prefixes = _manual_prefixes(policy)
    fqdn_prefix_values = effective_fqdn_prefixes(policy, gateway_settings)
    system_fqdn_prefixes = [item for item in fqdn_prefix_values if item not in set(policy.fqdn_prefixes)]
    manager = _set_manager(gateway_settings)
    backend = firewall_backend(gateway_settings)
    static_live_count = sum(
        manager.count(name)
        for name in {geoip_ipset_name(policy), manual_ipset_name(policy), policy.geoip_ipset_name}
    )
    fqdn_live_count = manager.count(fqdn_ipset_name(policy)) if fqdn_prefix_values else 0
    return {
        "ipset_name": policy.geoip_ipset_name,
        "geoip_ipset_name": geoip_ipset_name(policy),
        "manual_ipset_name": manual_ipset_name(policy),
        "fqdn_ipset_name": fqdn_ipset_name(policy),
        "set_name": policy.geoip_ipset_name,
        "geoip_set_name": geoip_ipset_name(policy),
        "manual_set_name": manual_ipset_name(policy),
        "fqdn_set_name": fqdn_ipset_name(policy),
        "firewall_backend": backend,
        "set_backend_label": firewall_set_label(gateway_settings),
        "total_prefixes": static_live_count + fqdn_live_count,
        "configured_prefixes": len(merged),
        "resolved_prefixes": fqdn_live_count,
        "fallback_default_route": merged == ["0.0.0.0/0"],
        "sources": [
            {
                "key": "countries",
                "enabled": policy.countries_enabled,
                "items_count": len(policy.geoip_countries),
                "prefix_count": len(country_prefixes),
                "description": ", ".join(policy.geoip_countries) if policy.geoip_countries else "—",
            },
            {
                "key": "manual",
                "enabled": policy.manual_prefixes_enabled,
                "items_count": len(policy.manual_prefixes),
                "prefix_count": len(manual_prefixes),
                "description": f"{len(policy.manual_prefixes)} entries",
            },
            {
                "key": "fqdn",
                "enabled": policy.fqdn_prefixes_enabled,
                "items_count": len(policy.fqdn_prefixes),
                "prefix_count": fqdn_live_count,
                "description": f"{len(policy.fqdn_prefixes)} domains",
            },
            {
                "key": "system",
                "enabled": bool(system_fqdn_prefixes),
                "items_count": len(system_fqdn_prefixes),
                "prefix_count": None,
                "description": ", ".join(system_fqdn_prefixes) if system_fqdn_prefixes else "—",
            },
        ],
    }


def sync_prefix_ipset(
    policy: RoutingPolicy,
    gateway_settings: GatewaySettings | None = None,
    *,
    flush_fqdn: bool = False,
) -> list[str]:
    prefixes = _merge_prefixes(policy, gateway_settings)
    country_prefixes = _country_prefixes(policy)
    manual_prefixes = _manual_prefixes(policy)
    fqdn_prefix_values = effective_fqdn_prefixes(policy, gateway_settings)
    manager = _set_manager(gateway_settings)

    if policy.countries_enabled:
        manager.create_or_update(geoip_ipset_name(policy), country_prefixes)
    else:
        manager.create_or_update(geoip_ipset_name(policy), [])

    if policy.manual_prefixes_enabled:
        manager.create_or_update(manual_ipset_name(policy), manual_prefixes)
    else:
        manager.create_or_update(manual_ipset_name(policy), [])

    if not policy.countries_enabled and not policy.manual_prefixes_enabled and not fqdn_prefix_values:
        manager.create_or_update(policy.geoip_ipset_name, ["0.0.0.0/0"])
    else:
        manager.create_or_update(policy.geoip_ipset_name, [])

    fqdn_set = fqdn_ipset_name(policy)
    if fqdn_prefix_values:
        if flush_fqdn:
            manager.create_or_update(fqdn_set, [])
        elif not manager.exists(fqdn_set):
            manager.create(fqdn_set)
    else:
        manager.create_or_update(fqdn_set, [])
    return prefixes


def _default_route() -> tuple[str | None, str | None]:
    rc, out = _run(["ip", "route", "show", "default"])
    if rc != 0:
        return None, None
    match = re.search(r"default via (\S+) dev (\S+)", out)
    if not match:
        return None, None
    return match.group(2), match.group(1)


def _source_selectors(gateway_settings: GatewaySettings) -> list[str]:
    return source_selectors(gateway_settings)


def _ensure_chain(table: str, chain: str) -> None:
    rc, _ = _run(["iptables", "-t", table, "-nL", chain])
    if rc != 0:
        _run_logged(["iptables", "-t", table, "-N", chain])
    _run_logged(["iptables", "-t", table, "-F", chain])


def _ensure_jump(table: str, builtin_chain: str, target_chain: str) -> None:
    rc, _ = _run(["iptables", "-t", table, "-C", builtin_chain, "-j", target_chain])
    if rc != 0:
        _run_logged(["iptables", "-t", table, "-I", builtin_chain, "-j", target_chain])


def _delete_jump(table: str, builtin_chain: str, target_chain: str) -> None:
    while True:
        rc, _ = _run(["iptables", "-t", table, "-C", builtin_chain, "-j", target_chain])
        if rc != 0:
            return
        _run(["iptables", "-t", table, "-D", builtin_chain, "-j", target_chain])


def _delete_chain(table: str, chain: str) -> None:
    _run(["iptables", "-t", table, "-F", chain])
    _run(["iptables", "-t", table, "-X", chain])


def _teardown_iptables_stack() -> None:
    for table, builtin_chain, target_chain in [
        ("mangle", "PREROUTING", MANGLE_PREROUTING_CHAIN),
        ("mangle", "FORWARD", MANGLE_FORWARD_CHAIN),
        ("mangle", "OUTPUT", MANGLE_OUTPUT_CHAIN),
        ("filter", "FORWARD", FILTER_FORWARD_CHAIN),
        ("filter", "OUTPUT", FILTER_OUTPUT_CHAIN),
        ("nat", "POSTROUTING", NAT_POSTROUTING_CHAIN),
        ("nat", "PREROUTING", NAT_DNS_PREROUTING_CHAIN),
        ("nat", "OUTPUT", NAT_DNS_OUTPUT_CHAIN),
    ]:
        _delete_jump(table, builtin_chain, target_chain)
    for table, chain in [
        ("mangle", MANGLE_PREROUTING_CHAIN),
        ("mangle", MANGLE_FORWARD_CHAIN),
        ("mangle", MANGLE_OUTPUT_CHAIN),
        ("filter", FILTER_FORWARD_CHAIN),
        ("filter", FILTER_OUTPUT_CHAIN),
        ("nat", NAT_POSTROUTING_CHAIN),
        ("nat", NAT_DNS_PREROUTING_CHAIN),
        ("nat", NAT_DNS_OUTPUT_CHAIN),
    ]:
        _delete_chain(table, chain)


def _append(table: str, chain: str, rule_args: list[str]) -> None:
    _run_logged(["iptables", "-t", table, "-A", chain, *rule_args])


def _ensure_ip_rules() -> None:
    rc, out = _run(["ip", "rule", "show"])
    if f"fwmark {settings.fwmark_local}" not in out or f"lookup {settings.routing_table_local}" not in out:
        _run_logged(["ip", "rule", "add", "fwmark", settings.fwmark_local, "table", str(settings.routing_table_local)])
    if f"fwmark {settings.fwmark_vpn}" not in out or f"lookup {settings.routing_table_vpn}" not in out:
        _run_logged(["ip", "rule", "add", "fwmark", settings.fwmark_vpn, "table", str(settings.routing_table_vpn)])


def _delete_ip_rule(fwmark: str, table: int) -> None:
    while True:
        rc, _ = _run(["ip", "rule", "del", "fwmark", fwmark, "table", str(table)])
        if rc != 0:
            return


def clear_policy_routing() -> None:
    _delete_ip_rule(settings.fwmark_local, settings.routing_table_local)
    _delete_ip_rule(settings.fwmark_vpn, settings.routing_table_vpn)
    _run(["ip", "route", "flush", "table", str(settings.routing_table_local)])
    _run(["ip", "route", "flush", "table", str(settings.routing_table_vpn)])


def _interface_exists(interface_name: str) -> bool:
    rc, _ = _run(["ip", "link", "show", "dev", interface_name])
    return rc == 0


def _ensure_table_routes() -> None:
    default_iface, default_gateway = _default_route()
    if not default_iface or not default_gateway:
        raise RuntimeError("Cannot determine default route for gateway host interface")
    _run_logged(
        [
            "ip",
            "route",
            "replace",
            "default",
            "via",
            default_gateway,
            "dev",
            default_iface,
            "table",
            str(settings.routing_table_local),
        ]
    )
    _run_logged(
        ["ip", "route", "replace", "default", "dev", settings.tunnel_interface, "table", str(settings.routing_table_vpn)]
    )


def _physical_interface() -> str:
    default_iface, _ = _default_route()
    if not default_iface:
        raise RuntimeError("Cannot determine default route for gateway host interface")
    return default_iface


def _connected_ipv4_prefixes(interface_name: str) -> list[str]:
    rc, out = _run(["ip", "-4", "route", "show", "dev", interface_name, "scope", "link"])
    if rc != 0:
        return []

    prefixes: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        prefix = parts[0]
        if prefix == "default" or "/" not in prefix:
            continue
        if prefix in seen:
            continue
        prefixes.append(prefix)
        seen.add(prefix)
    return prefixes


def _mark_targets(policy: RoutingPolicy) -> tuple[str, str]:
    if policy.prefixes_route_local:
        return settings.fwmark_local, settings.fwmark_vpn
    return settings.fwmark_vpn, settings.fwmark_local


def _iptables_command(table: str, chain: str, rule_args: list[str]) -> str:
    return " ".join(["iptables", "-t", table, "-A", chain, *rule_args])


def _nft_command(chain: str, expr: str) -> str:
    return f"nft add rule ip {nftables_manager.TABLE_NAME} {chain} {expr}"


def _append_nft(chain: str, expr: str) -> None:
    _run_logged(["nft", "-f", "-"], input_data=f"add rule ip {nftables_manager.TABLE_NAME} {chain} {expr}\n")


def _append_compat_nft(table: str, chain: str, expr: str) -> None:
    _run_logged(["nft", "-f", "-"], input_data=f"add rule ip {table} {chain} {expr}\n")


def _compat_chain_exists(table: str, chain: str) -> bool:
    rc, _ = _run(["nft", "list", "chain", "ip", table, chain])
    return rc == 0


def _ensure_compat_chain(table: str, chain: str) -> None:
    if not _compat_chain_exists(table, chain):
        _run_logged(["nft", "add", "chain", "ip", table, chain])
    _run_logged(["nft", "flush", "chain", "ip", table, chain])


def _compat_jump_exists(table: str, builtin_chain: str, target_chain: str) -> bool:
    rc, out = _run(["nft", "-a", "list", "chain", "ip", table, builtin_chain])
    if rc != 0:
        return False
    return any(f"jump {target_chain}" in line for line in out.splitlines())


def _ensure_compat_jump(table: str, builtin_chain: str, target_chain: str) -> None:
    if _compat_jump_exists(table, builtin_chain, target_chain):
        return
    _run_logged(["nft", "insert", "rule", "ip", table, builtin_chain, "jump", target_chain])


def _delete_compat_jump(table: str, builtin_chain: str, target_chain: str) -> None:
    rc, out = _run(["nft", "-a", "list", "chain", "ip", table, builtin_chain])
    if rc != 0:
        return
    for line in reversed(out.splitlines()):
        if f"jump {target_chain}" not in line:
            continue
        match = re.search(r"# handle (\d+)$", line.strip())
        if not match:
            continue
        _run(["nft", "delete", "rule", "ip", table, builtin_chain, "handle", match.group(1)])


def _delete_compat_chain(table: str, chain: str) -> None:
    if not _compat_chain_exists(table, chain):
        return
    _run(["nft", "flush", "chain", "ip", table, chain])
    _run(["nft", "delete", "chain", "ip", table, chain])


def _teardown_nftables_stack() -> None:
    for table, builtin_chain, target_chain in [
        ("filter", "FORWARD", FILTER_FORWARD_CHAIN),
        ("filter", "OUTPUT", FILTER_OUTPUT_CHAIN),
    ]:
        _delete_compat_jump(table, builtin_chain, target_chain)
    for table, chain in [
        ("filter", FILTER_FORWARD_CHAIN),
        ("filter", FILTER_OUTPUT_CHAIN),
    ]:
        _delete_compat_chain(table, chain)
    nftables_manager.flush_all()


def _ensure_nftables_base() -> None:
    _teardown_nftables_stack()
    script = "\n".join(
        [
            f"add table ip {nftables_manager.TABLE_NAME}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_PREROUTING} {{ type filter hook prerouting priority mangle; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_FORWARD} {{ type filter hook forward priority mangle; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_OUTPUT} {{ type route hook output priority mangle; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_FILTER_OUTPUT} {{ type filter hook output priority filter; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_PREROUTING} {{ type nat hook prerouting priority dstnat; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_OUTPUT} {{ type nat hook output priority -100; policy accept; }}",
            f"add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_POSTROUTING} {{ type nat hook postrouting priority srcnat; policy accept; }}",
            "",
        ]
    )
    _run_logged(["nft", "-f", "-"], input_data=script)


def _build_marking_rules(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    prefixes: list[str],
    active_node: EntryNode,
) -> list[tuple[str, str, list[str]]]:
    selectors = _source_selectors(gateway_settings)
    rules: list[tuple[str, str, list[str]]] = []
    matched_mark, other_mark = _mark_targets(policy)
    local_prefixes = _connected_ipv4_prefixes(_physical_interface())

    rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-d", f"{active_node.endpoint_host}/32", "-j", "RETURN"]))
    rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-d", f"{active_node.endpoint_host}/32", "-j", "RETURN"]))
    for prefix in local_prefixes:
        rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-d", prefix, "-j", "RETURN"]))
        rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-d", prefix, "-j", "RETURN"]))

    match_sets = _all_match_sets(policy, gateway_settings)

    if localhost_selector_enabled(gateway_settings):
        if match_sets:
            for match_set in match_sets:
                rules.append(
                    (
                        "mangle",
                        MANGLE_OUTPUT_CHAIN,
                        ["-m", "set", "--match-set", match_set, "dst", "-j", "MARK", "--set-mark", matched_mark],
                    )
                )
                rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-m", "set", "--match-set", match_set, "dst", "-j", "RETURN"]))
        rules.append(("mangle", MANGLE_OUTPUT_CHAIN, ["-j", "MARK", "--set-mark", other_mark]))

    for selector in non_localhost_selectors(gateway_settings):
        if match_sets:
            for match_set in match_sets:
                rules.append(
                    (
                        "mangle",
                        MANGLE_PREROUTING_CHAIN,
                        ["-s", selector, "-m", "set", "--match-set", match_set, "dst", "-j", "MARK", "--set-mark", matched_mark],
                    )
                )
                rules.append(
                    ("mangle", MANGLE_PREROUTING_CHAIN, ["-s", selector, "-m", "set", "--match-set", match_set, "dst", "-j", "RETURN"])
                )
        rules.append(("mangle", MANGLE_PREROUTING_CHAIN, ["-s", selector, "-j", "MARK", "--set-mark", other_mark]))

    return rules


def _build_dns_intercept_rules(gateway_settings: GatewaySettings) -> list[tuple[str, str, list[str]]]:
    if not gateway_settings.dns_intercept_enabled:
        return []

    rules: list[tuple[str, str, list[str]]] = []
    dns_uid = _dns_runtime_uid()

    if localhost_selector_enabled(gateway_settings):
        for protocol in ("udp", "tcp"):
            rule = ["-p", protocol, "--dport", "53"]
            if dns_uid is not None:
                rule.extend(["-m", "owner", "!", "--uid-owner", str(dns_uid)])
            rule.extend(["-j", "REDIRECT", "--to-ports", "53"])
            rules.append(("nat", NAT_DNS_OUTPUT_CHAIN, rule))

    for selector in non_localhost_selectors(gateway_settings):
        for protocol in ("udp", "tcp"):
            rules.append(
                (
                    "nat",
                    NAT_DNS_PREROUTING_CHAIN,
                    ["-s", selector, "-p", protocol, "--dport", "53", "-j", "REDIRECT", "--to-ports", "53"],
                )
            )

    return rules


def _build_marking_rules_nft(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    active_node: EntryNode,
) -> list[tuple[str, str]]:
    selectors = _source_selectors(gateway_settings)
    rules: list[tuple[str, str]] = []
    match_sets = _all_match_sets(policy, gateway_settings)
    matched_mark, other_mark = _mark_targets(policy)
    local_prefixes = _connected_ipv4_prefixes(_physical_interface())

    rules.append((NFT_CHAIN_MANGLE_PREROUTING, f"ip daddr {active_node.endpoint_host} return"))
    rules.append((NFT_CHAIN_MANGLE_OUTPUT, f"ip daddr {active_node.endpoint_host} return"))
    for prefix in local_prefixes:
        rules.append((NFT_CHAIN_MANGLE_PREROUTING, f"ip daddr {prefix} return"))
        rules.append((NFT_CHAIN_MANGLE_OUTPUT, f"ip daddr {prefix} return"))

    if localhost_selector_enabled(gateway_settings):
        for match_set in match_sets:
            rules.append((NFT_CHAIN_MANGLE_OUTPUT, f"ip daddr @{match_set} meta mark set {matched_mark} return"))
        rules.append((NFT_CHAIN_MANGLE_OUTPUT, f"meta mark set {other_mark}"))

    for selector in non_localhost_selectors(gateway_settings):
        for match_set in match_sets:
            rules.append(
                (NFT_CHAIN_MANGLE_PREROUTING, f"ip saddr {selector} ip daddr @{match_set} meta mark set {matched_mark} return")
            )
        rules.append((NFT_CHAIN_MANGLE_PREROUTING, f"ip saddr {selector} meta mark set {other_mark}"))

    return rules


def _build_dns_intercept_rules_nft(gateway_settings: GatewaySettings) -> list[tuple[str, str]]:
    if not gateway_settings.dns_intercept_enabled:
        return []

    rules: list[tuple[str, str]] = []
    dns_uid = _dns_runtime_uid()

    if localhost_selector_enabled(gateway_settings):
        for protocol in ("udp", "tcp"):
            expr = f"{protocol} dport 53"
            if dns_uid is not None:
                expr = f"meta skuid != {dns_uid} {expr}"
            rules.append((NFT_CHAIN_NAT_OUTPUT, f"{expr} redirect to :53"))

    for selector in non_localhost_selectors(gateway_settings):
        for protocol in ("udp", "tcp"):
            rules.append((NFT_CHAIN_NAT_PREROUTING, f"ip saddr {selector} {protocol} dport 53 redirect to :53"))

    return rules


def _build_mss_clamp_rules_nft(gateway_settings: GatewaySettings) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = [
        (
            NFT_CHAIN_MANGLE_OUTPUT,
            f'oifname "{settings.tunnel_interface}" tcp flags syn / syn,rst tcp option maxseg size set 1260',
        )
    ]
    for selector in non_localhost_selectors(gateway_settings):
        rules.append(
            (
                NFT_CHAIN_MANGLE_FORWARD,
                f'ip saddr {selector} oifname "{settings.tunnel_interface}" tcp flags syn / syn,rst tcp option maxseg size set 1260',
            )
        )
    return rules


def _build_postrouting_rules_nft(
    gateway_settings: GatewaySettings,
    default_iface: str | None,
) -> list[str]:
    rules = [f'oifname "{settings.tunnel_interface}" meta mark {settings.fwmark_vpn} masquerade']
    if default_iface is not None:
        for selector in non_localhost_selectors(gateway_settings):
            rules.append(
                f'ip saddr {selector} oifname "{default_iface}" meta mark {settings.fwmark_local} masquerade'
            )
    return rules


def _build_mss_clamp_rules(gateway_settings: GatewaySettings) -> list[tuple[str, str, list[str]]]:
    rules: list[tuple[str, str, list[str]]] = [
        (
            "mangle",
            MANGLE_OUTPUT_CHAIN,
            ["-o", settings.tunnel_interface, "-p", "tcp", "-m", "tcp", "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS", "--set-mss", "1260"],
        )
    ]
    for selector in non_localhost_selectors(gateway_settings):
        rules.append(
            (
                "mangle",
                MANGLE_FORWARD_CHAIN,
                ["-s", selector, "-o", settings.tunnel_interface, "-p", "tcp", "-m", "tcp", "--tcp-flags", "SYN,RST", "SYN", "-j", "TCPMSS", "--set-mss", "1260"],
            )
        )
    return rules


def _build_forward_rules(
    gateway_settings: GatewaySettings,
    default_iface: str | None,
) -> list[list[str]]:
    rules: list[list[str]] = []
    if default_iface is not None:
        for selector in non_localhost_selectors(gateway_settings):
            rules.extend(
                [
                    ["-s", selector, "-o", default_iface, "-m", "mark", "--mark", settings.fwmark_local, "-j", "ACCEPT"],
                    ["-i", default_iface, "-d", selector, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
                ]
            )
    rules.extend(
        [
            ["-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", "ACCEPT"],
            ["-i", settings.tunnel_interface, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
        ]
    )
    return rules


def _build_postrouting_rules(
    gateway_settings: GatewaySettings,
    default_iface: str | None,
) -> list[list[str]]:
    rules: list[list[str]] = [["-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", "MASQUERADE"]]
    if default_iface is not None:
        for selector in non_localhost_selectors(gateway_settings):
            rules.append(
                ["-s", selector, "-o", default_iface, "-m", "mark", "--mark", settings.fwmark_local, "-j", "MASQUERADE"]
            )
    return rules


def _build_filter_forward_rules_nft(
    gateway_settings: GatewaySettings,
    default_iface: str | None,
) -> list[str]:
    rules: list[str] = []
    for selector in non_localhost_selectors(gateway_settings):
        if default_iface is None:
            continue
        rules.extend(
            [
                f'ip saddr {selector} oifname "{default_iface}" meta mark {settings.fwmark_local} accept',
                f'iifname "{default_iface}" ip daddr {selector} ct state related,established accept',
            ]
        )
    rules.extend(
        [
            f'oifname "{settings.tunnel_interface}" meta mark {settings.fwmark_vpn} accept',
            f'iifname "{settings.tunnel_interface}" ct state related,established accept',
        ]
    )
    return rules


def _build_local_passthrough_rules(gateway_settings: GatewaySettings, default_iface: str | None) -> list[list[str]]:
    rules: list[list[str]] = []
    if default_iface is None:
        return rules
    for selector in non_localhost_selectors(gateway_settings):
        rules.extend(
            [
                ["-s", selector, "-o", default_iface, "-j", "ACCEPT"],
                ["-i", default_iface, "-d", selector, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
            ]
        )
    return rules


def _build_local_passthrough_nat_rules(gateway_settings: GatewaySettings, default_iface: str | None) -> list[list[str]]:
    if default_iface is None:
        return []
    return [["-s", selector, "-o", default_iface, "-j", "MASQUERADE"] for selector in non_localhost_selectors(gateway_settings)]


def _build_local_passthrough_rules_nft(gateway_settings: GatewaySettings, default_iface: str | None) -> list[str]:
    rules: list[str] = []
    if default_iface is None:
        return rules
    for selector in non_localhost_selectors(gateway_settings):
        rules.extend(
            [
                f'ip saddr {selector} oifname "{default_iface}" accept',
                f'iifname "{default_iface}" ip daddr {selector} ct state related,established accept',
            ]
        )
    return rules


def _build_local_passthrough_nat_rules_nft(gateway_settings: GatewaySettings, default_iface: str | None) -> list[str]:
    if default_iface is None:
        return []
    return [f'ip saddr {selector} oifname "{default_iface}" masquerade' for selector in non_localhost_selectors(gateway_settings)]


def build_routing_plan(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    active_node: EntryNode | None,
) -> dict:
    selectors = _source_selectors(gateway_settings)
    cached_prefixes = _merge_prefixes(policy, gateway_settings)
    warnings: list[str] = []
    default_iface, default_gateway = _default_route()
    backend = firewall_backend(gateway_settings)
    set_label = firewall_set_label(gateway_settings)

    if active_node is None:
        warnings.append("No active entry node selected")
    if not default_iface or not default_gateway:
        warnings.append("Default host route is missing")
    if gateway_settings.tunnel_status != TunnelStatus.running.value:
        warnings.append("Tunnel is not running")
    if gateway_settings.tunnel_status == TunnelStatus.running.value and not _interface_exists(settings.tunnel_interface):
        warnings.append(f"Tunnel interface {settings.tunnel_interface} is missing")
    if policy.countries_enabled and not any(load_cached_country(country) for country in policy.geoip_countries):
        warnings.append("GeoIP cache is empty")

    safe_to_apply = (
        active_node is not None
        and default_iface is not None
        and gateway_settings.tunnel_status == TunnelStatus.running.value
        and _interface_exists(settings.tunnel_interface)
        and (not policy.strict_mode or bool(cached_prefixes) or bool(effective_fqdn_prefixes(policy, gateway_settings)))
    )

    commands: list[str] = [
        f"ip rule add fwmark {settings.fwmark_local} table {settings.routing_table_local}",
        f"ip rule add fwmark {settings.fwmark_vpn} table {settings.routing_table_vpn}",
    ]

    if active_node is not None:
        if default_iface is not None and default_gateway is not None:
            commands.append(
                f"ip route replace default via {default_gateway} dev {default_iface} table {settings.routing_table_local}"
            )
        commands.append(f"ip route replace default dev {settings.tunnel_interface} table {settings.routing_table_vpn}")
        commands.extend(
            [
                f"{set_label} create/update {geoip_ipset_name(policy)} ({len(_country_prefixes(policy))} prefixes)",
                f"{set_label} create/update {manual_ipset_name(policy)} ({len(_manual_prefixes(policy))} prefixes)",
                f"{set_label} create/update {policy.geoip_ipset_name} ({1 if not policy.countries_enabled and not policy.manual_prefixes_enabled and not effective_fqdn_prefixes(policy, gateway_settings) else 0} prefixes)",
                f"{set_label} create/update {fqdn_ipset_name(policy)} (dnsmasq-managed)",
            ]
        )

        if backend == IPTABLES_BACKEND:
            commands.extend(
                _iptables_command(table, chain, rule)
                for table, chain, rule in _build_marking_rules(gateway_settings, policy, cached_prefixes, active_node)
            )
            commands.extend(
                _iptables_command(table, chain, rule)
                for table, chain, rule in _build_dns_intercept_rules(gateway_settings)
            )
            commands.extend(
                _iptables_command(table, chain, rule)
                for table, chain, rule in _build_mss_clamp_rules(gateway_settings)
            )
            commands.extend(
                _iptables_command("nat", NAT_POSTROUTING_CHAIN, rule)
                for rule in _build_postrouting_rules(gateway_settings, default_iface)
            )
            commands.extend(
                _iptables_command("filter", FILTER_FORWARD_CHAIN, rule)
                for rule in _build_forward_rules(gateway_settings, default_iface)
            )
        else:
            commands.extend(
                [
                    f"nft add table ip {nftables_manager.TABLE_NAME}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_PREROUTING} {{ type filter hook prerouting priority mangle; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_FORWARD} {{ type filter hook forward priority mangle; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_MANGLE_OUTPUT} {{ type route hook output priority mangle; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_FILTER_OUTPUT} {{ type filter hook output priority filter; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_PREROUTING} {{ type nat hook prerouting priority dstnat; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_OUTPUT} {{ type nat hook output priority -100; policy accept; }}",
                    f"nft add chain ip {nftables_manager.TABLE_NAME} {NFT_CHAIN_NAT_POSTROUTING} {{ type nat hook postrouting priority srcnat; policy accept; }}",
                    f"nft add chain ip filter {FILTER_FORWARD_CHAIN}",
                    f"nft insert rule ip filter FORWARD jump {FILTER_FORWARD_CHAIN}",
                    f"nft add chain ip filter {FILTER_OUTPUT_CHAIN}",
                    f"nft insert rule ip filter OUTPUT jump {FILTER_OUTPUT_CHAIN}",
                ]
            )
            commands.extend(_nft_command(chain, expr) for chain, expr in _build_marking_rules_nft(gateway_settings, policy, active_node))
            commands.extend(_nft_command(chain, expr) for chain, expr in _build_dns_intercept_rules_nft(gateway_settings))
            commands.extend(_nft_command(chain, expr) for chain, expr in _build_mss_clamp_rules_nft(gateway_settings))
            commands.extend(
                _nft_command(NFT_CHAIN_NAT_POSTROUTING, expr)
                for expr in _build_postrouting_rules_nft(gateway_settings, default_iface)
            )
            commands.extend(
                f"nft add rule ip filter {FILTER_FORWARD_CHAIN} {expr}"
                for expr in _build_filter_forward_rules_nft(gateway_settings, default_iface)
            )

    if policy.kill_switch_enabled:
        action = "reject" if policy.strict_mode else "drop"
        if backend == IPTABLES_BACKEND:
            commands.append(
                f"iptables -t filter -A {FILTER_OUTPUT_CHAIN} ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j {action.upper()}"
            )
            commands.append(
                f"iptables -t filter -A {FILTER_FORWARD_CHAIN} ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j {action.upper()}"
            )
        else:
            commands.append(_nft_command(NFT_CHAIN_FILTER_OUTPUT, f'oifname != "{settings.tunnel_interface}" meta mark {settings.fwmark_vpn} {action}'))

    manager = _set_manager(gateway_settings)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_mode": "cidr_list",
        "selectors": selectors,
        "geoip_prefix_count": len(cached_prefixes) + manager.count(fqdn_ipset_name(policy)),
        "prefixes_route_local": policy.prefixes_route_local,
        "prefix_summary": build_prefix_summary(policy, gateway_settings),
        "manual_prefixes": policy.manual_prefixes,
        "fqdn_prefixes": effective_fqdn_prefixes(policy, gateway_settings),
        "kill_switch_enabled": policy.kill_switch_enabled,
        "strict_mode": policy.strict_mode,
        "dns_intercept_enabled": gateway_settings.dns_intercept_enabled,
        "warnings": warnings,
        "commands": commands,
        "safe_to_apply": safe_to_apply,
        "firewall_backend": backend,
        "set_backend_label": set_label,
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

    selectors = _source_selectors(gateway_settings)
    default_iface = _physical_interface()
    _ensure_ip_rules()
    _ensure_table_routes()

    if firewall_backend(gateway_settings) == NFTABLES_BACKEND:
        _teardown_iptables_stack()
        _ensure_nftables_base()
        _ensure_compat_chain("filter", FILTER_FORWARD_CHAIN)
        _ensure_compat_chain("filter", FILTER_OUTPUT_CHAIN)
        _ensure_compat_jump("filter", "FORWARD", FILTER_FORWARD_CHAIN)
        _ensure_compat_jump("filter", "OUTPUT", FILTER_OUTPUT_CHAIN)
        prefixes = sync_prefix_ipset(policy, gateway_settings)
        for chain, expr in _build_marking_rules_nft(gateway_settings, policy, active_node):
            _append_nft(chain, expr)
        for chain, expr in _build_dns_intercept_rules_nft(gateway_settings):
            _append_nft(chain, expr)
        for chain, expr in _build_mss_clamp_rules_nft(gateway_settings):
            _append_nft(chain, expr)
        for expr in _build_postrouting_rules_nft(gateway_settings, default_iface):
            _append_nft(NFT_CHAIN_NAT_POSTROUTING, expr)
        for expr in _build_filter_forward_rules_nft(gateway_settings, default_iface):
            _append_compat_nft("filter", FILTER_FORWARD_CHAIN, expr)
        if policy.kill_switch_enabled:
            action = "reject" if policy.strict_mode else "drop"
            _append_nft(NFT_CHAIN_FILTER_OUTPUT, f'oifname != "{settings.tunnel_interface}" meta mark {settings.fwmark_vpn} {action}')
    else:
        _teardown_nftables_stack()
        prefixes = sync_prefix_ipset(policy, gateway_settings)
        _ensure_chain("mangle", MANGLE_PREROUTING_CHAIN)
        _ensure_chain("mangle", MANGLE_FORWARD_CHAIN)
        _ensure_chain("mangle", MANGLE_OUTPUT_CHAIN)
        _ensure_chain("filter", FILTER_FORWARD_CHAIN)
        _ensure_chain("filter", FILTER_OUTPUT_CHAIN)
        _ensure_chain("nat", NAT_POSTROUTING_CHAIN)
        _ensure_chain("nat", NAT_DNS_PREROUTING_CHAIN)
        _ensure_chain("nat", NAT_DNS_OUTPUT_CHAIN)
        _ensure_jump("mangle", "PREROUTING", MANGLE_PREROUTING_CHAIN)
        _ensure_jump("mangle", "FORWARD", MANGLE_FORWARD_CHAIN)
        _ensure_jump("mangle", "OUTPUT", MANGLE_OUTPUT_CHAIN)
        _ensure_jump("filter", "FORWARD", FILTER_FORWARD_CHAIN)
        _ensure_jump("filter", "OUTPUT", FILTER_OUTPUT_CHAIN)
        _ensure_jump("nat", "POSTROUTING", NAT_POSTROUTING_CHAIN)
        _ensure_jump("nat", "PREROUTING", NAT_DNS_PREROUTING_CHAIN)
        _ensure_jump("nat", "OUTPUT", NAT_DNS_OUTPUT_CHAIN)

        for table, chain, rule in _build_marking_rules(gateway_settings, policy, prefixes, active_node):
            _append(table, chain, rule)
        for table, chain, rule in _build_dns_intercept_rules(gateway_settings):
            _append(table, chain, rule)
        for table, chain, rule in _build_mss_clamp_rules(gateway_settings):
            _append(table, chain, rule)

        for rule in _build_postrouting_rules(gateway_settings, default_iface):
            _append("nat", NAT_POSTROUTING_CHAIN, rule)
        for rule in _build_forward_rules(gateway_settings, default_iface):
            _append("filter", FILTER_FORWARD_CHAIN, rule)

        if policy.kill_switch_enabled:
            action = "REJECT" if policy.strict_mode else "DROP"
            _append("filter", FILTER_OUTPUT_CHAIN, ["!", "-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", action])
            _append("filter", FILTER_FORWARD_CHAIN, ["!", "-o", settings.tunnel_interface, "-m", "mark", "--mark", settings.fwmark_vpn, "-j", action])

    logger.info(
        "[awg-routing] applied routing plan backend=%s source_mode=%s kill_switch=%s strict_mode=%s",
        firewall_backend(gateway_settings),
        gateway_settings.traffic_source_mode,
        policy.kill_switch_enabled,
        policy.strict_mode,
    )
    return plan


def apply_local_passthrough(gateway_settings: GatewaySettings) -> None:
    default_iface = _physical_interface()
    clear_policy_routing()

    if firewall_backend(gateway_settings) == NFTABLES_BACKEND:
        _teardown_iptables_stack()
        _ensure_nftables_base()
        _ensure_compat_chain("filter", FILTER_FORWARD_CHAIN)
        _ensure_compat_jump("filter", "FORWARD", FILTER_FORWARD_CHAIN)
        for expr in _build_local_passthrough_nat_rules_nft(gateway_settings, default_iface):
            _append_nft(NFT_CHAIN_NAT_POSTROUTING, expr)
        for expr in _build_local_passthrough_rules_nft(gateway_settings, default_iface):
            _append_compat_nft("filter", FILTER_FORWARD_CHAIN, expr)
        return

    _teardown_nftables_stack()
    _ensure_chain("filter", FILTER_FORWARD_CHAIN)
    _ensure_chain("nat", NAT_POSTROUTING_CHAIN)
    _ensure_jump("filter", "FORWARD", FILTER_FORWARD_CHAIN)
    _ensure_jump("nat", "POSTROUTING", NAT_POSTROUTING_CHAIN)
    for rule in _build_local_passthrough_nat_rules(gateway_settings, default_iface):
        _append("nat", NAT_POSTROUTING_CHAIN, rule)
    for rule in _build_local_passthrough_rules(gateway_settings, default_iface):
        _append("filter", FILTER_FORWARD_CHAIN, rule)


def sync_firewall_backend(gateway_settings: GatewaySettings, policy: RoutingPolicy) -> None:
    if not getattr(gateway_settings, "gateway_enabled", True):
        apply_local_passthrough(gateway_settings)
        return

    if firewall_backend(gateway_settings) == NFTABLES_BACKEND:
        _teardown_iptables_stack()
        _ensure_nftables_base()
        sync_prefix_ipset(policy, gateway_settings)
        return

    _teardown_nftables_stack()
    sync_prefix_ipset(policy, gateway_settings)
