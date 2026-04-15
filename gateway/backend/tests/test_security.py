from app.security import hash_password, is_ip_allowed, verify_password


def test_password_hash_roundtrip() -> None:
    password_hash = hash_password("strong-password")
    assert verify_password("strong-password", password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_is_ip_allowed_matches_host_and_subnet() -> None:
    assert is_ip_allowed("203.0.113.10", ["203.0.113.10/32"]) is True
    assert is_ip_allowed("192.168.0.25", ["192.168.0.0/24"]) is True
    assert is_ip_allowed("192.168.1.25", ["192.168.0.0/24"]) is False


def test_is_ip_allowed_allows_all_when_empty_and_rejects_invalid_ip() -> None:
    assert is_ip_allowed("198.51.100.5", []) is True
    assert is_ip_allowed(None, ["198.51.100.5/32"]) is False
    assert is_ip_allowed("not-an-ip", ["198.51.100.5/32"]) is False
