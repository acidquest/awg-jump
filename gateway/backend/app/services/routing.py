from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.models import EntryNode, GatewaySettings, RoutingPolicy, TrafficSourceMode
from app.services.geoip import load_cached_country


def _source_selectors(gateway_settings: GatewaySettings) -> list[str]:
    if gateway_settings.traffic_source_mode == TrafficSourceMode.localhost.value:
        return ["-s 127.0.0.1/32"]
    if gateway_settings.traffic_source_mode == TrafficSourceMode.selected_cidr.value:
        return [f"-s {cidr}" for cidr in gateway_settings.allowed_client_cidrs]
    if gateway_settings.traffic_source_mode == TrafficSourceMode.selected_hosts.value:
        return [f"-s {host}/32" for host in gateway_settings.allowed_client_hosts]
    return []


def build_routing_plan(
    gateway_settings: GatewaySettings,
    policy: RoutingPolicy,
    active_node: EntryNode | None,
) -> dict:
    selectors = _source_selectors(gateway_settings)
    cached_prefixes = []
    for country in policy.geoip_countries:
        cached_prefixes.extend(load_cached_country(country))
    cached_prefixes.extend(policy.manual_prefixes)

    commands: list[str] = []
    warnings: list[str] = []

    if active_node is None:
        warnings.append("No active entry node selected")
    if policy.geoip_enabled and not cached_prefixes:
        warnings.append("GeoIP cache is empty")

    commands.append(f"ip rule add fwmark {settings.fwmark_vpn} table {settings.routing_table_vpn}")
    if active_node is not None:
        commands.append(
            f"ip route replace default dev {settings.tunnel_interface} table {settings.routing_table_vpn}"
        )
        commands.append(
            f"ip route replace {active_node.endpoint_host}/32 via $(ip route show default | awk '/default/ {{print $3; exit}}')"
        )

    for selector in selectors or ["OUTPUT-only"]:
        commands.append(
            f"iptables -t mangle -A PREROUTING {selector} -m set --match-set {policy.geoip_ipset_name} dst "
            f"-j MARK --set-mark {settings.fwmark_vpn}"
        )
    commands.append(
        f"iptables -t mangle -A OUTPUT -m set --match-set {policy.geoip_ipset_name} dst "
        f"-j MARK --set-mark {settings.fwmark_vpn}"
    )

    if policy.kill_switch_enabled:
        if active_node is None or (policy.geoip_enabled and not cached_prefixes):
            commands.append(
                f"iptables -A OUTPUT ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j REJECT"
            )
        else:
            commands.append(
                f"iptables -A OUTPUT ! -o {settings.tunnel_interface} -m mark --mark {settings.fwmark_vpn} -j DROP"
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_mode": gateway_settings.traffic_source_mode,
        "selectors": selectors,
        "geoip_prefix_count": len(cached_prefixes),
        "manual_prefixes": policy.manual_prefixes,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "warnings": warnings,
        "commands": commands,
        "safe_to_apply": active_node is not None and (not policy.geoip_enabled or bool(cached_prefixes)),
    }
