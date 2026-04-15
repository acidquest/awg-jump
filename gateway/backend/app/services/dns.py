from __future__ import annotations

from collections.abc import Iterable

from app.models import DnsDomainRule, DnsUpstream


def _to_dnsmasq_domain(domain: str) -> str:
    normalized = domain.strip().strip(".").lower()
    if not normalized:
        raise ValueError("Domain cannot be empty")
    return normalized.encode("idna").decode("ascii")


def build_dnsmasq_preview(
    upstreams: Iterable[DnsUpstream],
    domain_rules: Iterable[DnsDomainRule],
    fqdn_prefixes: Iterable[str] | None = None,
    ipset_name: str = "routing_prefixes",
    *,
    use_nftset: bool = False,
    nft_table_name: str = "awg_gw",
) -> str:
    upstream_by_zone = {item.zone: item for item in upstreams}
    local_servers = upstream_by_zone.get("local").servers if upstream_by_zone.get("local") else []
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
    lines.append("# Local zone overrides")
    for rule in sorted(domain_rules, key=lambda item: item.domain):
        if not rule.enabled or rule.zone != "local":
            continue
        dnsmasq_domain = _to_dnsmasq_domain(rule.domain)
        for server in local_servers:
            lines.append(f"server=/{dnsmasq_domain}/{server}")
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
    fqdn_prefixes: Iterable[str] | None = None,
    ipset_name: str = "routing_prefixes",
    *,
    use_nftset: bool = False,
    nft_table_name: str = "awg_gw",
) -> str:
    preview = build_dnsmasq_preview(
        upstreams,
        domain_rules,
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
