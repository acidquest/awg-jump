from __future__ import annotations

from collections.abc import Iterable

from app.models import DnsDomainRule, DnsManualAddress, DnsUpstream


def _to_dnsmasq_domain(domain: str) -> str:
    normalized = domain.strip().strip(".").lower()
    if not normalized:
        raise ValueError("Domain cannot be empty")
    return normalized.encode("idna").decode("ascii")


def build_dnsmasq_preview(
    upstreams: Iterable[DnsUpstream],
    domain_rules: Iterable[DnsDomainRule],
    manual_addresses: Iterable[DnsManualAddress] | None = None,
    fqdn_prefixes: Iterable[str] | None = None,
    ipset_name: str = "routing_prefixes",
    *,
    use_nftset: bool = False,
    nft_table_name: str = "awg_gw",
) -> str:
    upstream_by_zone = {item.zone: item for item in upstreams}
    vpn_servers = upstream_by_zone.get("vpn").servers if upstream_by_zone.get("vpn") else []
    lines = [
        "# AWG Gateway split DNS preview",
        "no-resolv",
        "",
        "# VPN zone default upstreams",
    ]
    for server in vpn_servers:
        lines.append(f"server={server}")
    lines.append("")
    lines.append("# Special zone overrides")
    for rule in sorted(domain_rules, key=lambda item: item.domain):
        if not rule.enabled or rule.zone == "vpn":
            continue
        zone_servers = upstream_by_zone.get(rule.zone).servers if upstream_by_zone.get(rule.zone) else []
        if not zone_servers:
            continue
        dnsmasq_domain = _to_dnsmasq_domain(rule.domain)
        for server in zone_servers:
            lines.append(f"server=/{dnsmasq_domain}/{server}")
    manual_values = [item for item in (manual_addresses or []) if item.enabled]
    if manual_values:
        lines.append("")
        lines.append("# Manual replace addresses")
        for item in sorted(manual_values, key=lambda x: x.domain):
            dnsmasq_domain = _to_dnsmasq_domain(item.domain)
            lines.append(f"address=/{dnsmasq_domain}/{item.address}")
    fqdn_values = sorted(
        {
            _to_dnsmasq_domain(item)
            for item in (fqdn_prefixes or [])
            if item and item.strip().strip(".")
        }
    )
    if fqdn_values:
        lines.append("")
        lines.append(f"# FQDN prefixes -> {'nft set' if use_nftset else 'ipset'}")
        for fqdn in fqdn_values:
            if use_nftset:
                lines.append(f"nftset=/{fqdn}/4#ip#{nft_table_name}#{ipset_name}")
            else:
                lines.append(f"ipset=/{fqdn}/{ipset_name}")
    lines.append("")
    return "\n".join(lines)


def build_dnsmasq_config(
    upstreams: Iterable[DnsUpstream],
    domain_rules: Iterable[DnsDomainRule],
    manual_addresses: Iterable[DnsManualAddress] | None = None,
    fqdn_prefixes: Iterable[str] | None = None,
    ipset_name: str = "routing_prefixes",
    *,
    use_nftset: bool = False,
    nft_table_name: str = "awg_gw",
) -> str:
    preview = build_dnsmasq_preview(
        upstreams,
        domain_rules,
        manual_addresses=manual_addresses,
        fqdn_prefixes=fqdn_prefixes,
        ipset_name=ipset_name,
        use_nftset=use_nftset,
        nft_table_name=nft_table_name,
    ).splitlines()
    return "\n".join(
        [
            "# AWG Gateway dnsmasq runtime config",
            "port=53",
            "bind-dynamic",
            "domain-needed",
            "bogus-priv",
            *preview[1:],
            "",
        ]
    )
