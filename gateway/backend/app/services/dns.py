from __future__ import annotations

from collections.abc import Iterable

from app.models import DnsDomainRule, DnsUpstream


def build_dnsmasq_preview(
    upstreams: Iterable[DnsUpstream],
    domain_rules: Iterable[DnsDomainRule],
) -> str:
    upstream_by_zone = {item.zone: item for item in upstreams}
    local_servers = upstream_by_zone.get("local").servers if upstream_by_zone.get("local") else []
    vpn_servers = upstream_by_zone.get("vpn").servers if upstream_by_zone.get("vpn") else []
    lines = [
        "# AWG Gateway split DNS preview",
        "no-resolv",
        "bind-interfaces",
        "",
        "# VPN zone default upstreams",
    ]
    for server in vpn_servers:
        lines.append(f"server={server}")
    lines.append("")
    lines.append("# Local zone overrides")
    for rule in sorted(domain_rules, key=lambda item: item.domain):
        if not rule.enabled or rule.zone != "local":
            continue
        for server in local_servers:
            lines.append(f"server=/{rule.domain}/{server}")
    lines.append("")
    return "\n".join(lines)
