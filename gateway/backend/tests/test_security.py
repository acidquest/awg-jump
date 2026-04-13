from app.security import hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    password_hash = hash_password("strong-password")
    assert verify_password("strong-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
