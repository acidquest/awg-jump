from app.services.conf_parser import parse_peer_conf


VALID_CONF = """
[Interface]
PrivateKey = test-private
Address = 10.44.0.2/32
DNS = 1.1.1.1, 8.8.8.8
Jc = 5
S1 = 40
H1 = 12345

[Peer]
PublicKey = peer-public
PresharedKey = psk
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0, 10.10.0.0/24
PersistentKeepalive = 25
"""


def test_parse_peer_conf_happy_path() -> None:
    parsed = parse_peer_conf(VALID_CONF, name="Node A")
    assert parsed.name == "Node A"
    assert parsed.endpoint_host == "vpn.example.com"
    assert parsed.endpoint_port == 51820
    assert parsed.dns_servers == ["1.1.1.1", "8.8.8.8"]
    assert parsed.allowed_ips == ["0.0.0.0/0", "10.10.0.0/24"]
    assert parsed.obfuscation["JC"] == 5


def test_parse_peer_conf_requires_core_fields() -> None:
    try:
        parse_peer_conf("[Interface]\nAddress = 10.0.0.2/32\n[Peer]\nEndpoint = host:51820\n")
    except ValueError as exc:
        assert "privatekey" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for incomplete config")
