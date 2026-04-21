from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(slots=True)
class ParsedUpstreamConf:
    name: str
    raw_conf: str
    endpoint: str
    endpoint_host: str
    endpoint_port: int
    public_key: str
    private_key: str
    preshared_key: str | None
    tunnel_address: str
    dns_servers: list[str]
    allowed_ips: list[str]
    persistent_keepalive: int | None
    obfuscation: dict[str, str | int]


def _parse_sections(conf_text: str) -> dict[str, dict[str, str]]:
    section = None
    parsed: dict[str, dict[str, str]] = {}
    for raw_line in conf_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            parsed.setdefault(section, {})
            continue
        if "=" not in line or section is None:
            raise ValueError(f"Invalid line in config: {raw_line}")
        key, value = [part.strip() for part in line.split("=", 1)]
        parsed[section][key.lower()] = value
    return parsed


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    parts = urlsplit(f"scheme://{endpoint}")
    if not parts.hostname or not parts.port:
        raise ValueError("Peer Endpoint must include host:port")
    return parts.hostname, parts.port


def parse_peer_conf(conf_text: str, *, name: str | None = None) -> ParsedUpstreamConf:
    parsed = _parse_sections(conf_text)
    interface = parsed.get("interface", {})
    peer = parsed.get("peer", {})
    required_interface = {"privatekey", "address"}
    required_peer = {"publickey", "endpoint", "allowedips"}
    missing = sorted((required_interface - set(interface)) | (required_peer - set(peer)))
    if missing:
        raise ValueError(f"Config is missing required keys: {', '.join(missing)}")

    endpoint = peer["endpoint"]
    endpoint_host, endpoint_port = _split_endpoint(endpoint)
    obfuscation: dict[str, str | int] = {}
    for key, value in interface.items():
        if key in {"jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4"}:
            try:
                obfuscation[key.upper()] = int(value)
            except ValueError:
                obfuscation[key.upper()] = value

    return ParsedUpstreamConf(
        name=name or endpoint_host,
        raw_conf=conf_text.strip() + "\n",
        endpoint=endpoint,
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        public_key=peer["publickey"],
        private_key=interface["privatekey"],
        preshared_key=peer.get("presharedkey"),
        tunnel_address=interface["address"],
        dns_servers=_split_csv(interface.get("dns")),
        allowed_ips=_split_csv(peer.get("allowedips")),
        persistent_keepalive=int(peer["persistentkeepalive"]) if peer.get("persistentkeepalive") else None,
        obfuscation=obfuscation,
    )


def render_peer_conf(
    *,
    private_key: str,
    tunnel_address: str,
    dns_servers: list[str],
    obfuscation: dict[str, str | int],
    public_key: str,
    endpoint: str,
    allowed_ips: list[str],
    preshared_key: str | None = None,
    persistent_keepalive: int | None = None,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {tunnel_address}",
    ]
    if dns_servers:
        lines.append(f"DNS = {', '.join(dns_servers)}")
    for key, value in sorted(obfuscation.items()):
        lines.append(f"{key} = {value}")
    lines.extend(
        [
            "",
            "[Peer]",
            f"PublicKey = {public_key}",
            f"Endpoint = {endpoint}",
            f"AllowedIPs = {', '.join(allowed_ips or ['0.0.0.0/0'])}",
        ]
    )
    if preshared_key:
        lines.append(f"PresharedKey = {preshared_key}")
    if persistent_keepalive is not None:
        lines.append(f"PersistentKeepalive = {persistent_keepalive}")
    return "\n".join(lines).strip() + "\n"
