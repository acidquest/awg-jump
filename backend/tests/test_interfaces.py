import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.interface import Interface


@pytest.mark.asyncio
async def test_get_interface_detail_returns_private_key(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    resp = await client.get(f"/api/interfaces/{iface.id}", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["private_key"] == iface.private_key
    assert resp.json()["public_key"] == iface.public_key


@pytest.mark.asyncio
async def test_update_interface_private_key_updates_public_key(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    resp = await client.put(
        f"/api/interfaces/{iface.id}",
        json={"private_key": "manual-interface-private-key=="},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["public_key"] == "fake_key_base64=="

    detail_resp = await client.get(f"/api/interfaces/{iface.id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["private_key"] == "manual-interface-private-key=="
    assert detail_resp.json()["public_key"] == "fake_key_base64=="


@pytest.mark.asyncio
async def test_update_interface_obfuscation_params(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg1"))
    iface = result.scalar_one()

    payload = {
        "obf_jc": 9,
        "obf_jmin": 60,
        "obf_jmax": 95,
        "obf_s1": 21,
        "obf_s2": 22,
        "obf_s3": 23,
        "obf_s4": 24,
        "obf_h1": 123456789,
        "obf_h2": 223456789,
        "obf_h3": 323456789,
        "obf_h4": 423456789,
    }

    resp = await client.put(
        f"/api/interfaces/{iface.id}",
        json=payload,
        headers=auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    for key, value in payload.items():
        assert body[key] == value
    assert body["obf_generated_at"] is not None

    detail_resp = await client.get(f"/api/interfaces/{iface.id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    for key, value in payload.items():
        assert detail_resp.json()[key] == value


@pytest.mark.asyncio
async def test_derive_interface_public_key(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "wg0"))
    iface = result.scalar_one_or_none()
    if iface is None:
        result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
        iface = result.scalar_one()

    resp = await client.post(
        f"/api/interfaces/{iface.id}/derive-public-key",
        json={"private_key": "manual-interface-private-key=="},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["public_key"] == "fake_key_base64=="
    assert resp.json()["protocol"] in {"awg", "wg"}
