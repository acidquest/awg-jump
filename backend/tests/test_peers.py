import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.interface import Interface
from backend.models.peer import Peer


@pytest.mark.asyncio
async def test_list_peers_empty(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/peers", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_peer(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(
        select(Interface).where(Interface.name == "awg0")
    )
    iface = result.scalar_one()

    resp = await client.post(
        "/api/peers",
        json={
            "interface_id": iface.id,
            "name": "test-peer",
            "tunnel_address": "10.10.0.2/32",
            "allowed_ips": "0.0.0.0/0",
            "persistent_keepalive": 25,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-peer"
    assert data["interface_id"] == iface.id
    assert data["enabled"] is True
    assert "public_key" in data
    assert data["public_key"]  # не пустой


@pytest.mark.asyncio
async def test_get_peer(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={
            "interface_id": iface.id,
            "name": "peer-for-get",
            "tunnel_address": "10.10.0.3/32",
            "allowed_ips": "0.0.0.0/0",
        },
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]

    resp = await client.get(f"/api/peers/{peer_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == peer_id


@pytest.mark.asyncio
async def test_update_peer(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={"interface_id": iface.id, "name": "to-update", "allowed_ips": "0.0.0.0/0"},
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/peers/{peer_id}",
        json={"name": "updated-name", "persistent_keepalive": 30},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"
    assert resp.json()["persistent_keepalive"] == 30


@pytest.mark.asyncio
async def test_toggle_peer(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={"interface_id": iface.id, "name": "to-toggle", "allowed_ips": "0.0.0.0/0"},
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]
    assert create_resp.json()["enabled"] is True

    resp = await client.post(f"/api/peers/{peer_id}/toggle", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.post(f"/api/peers/{peer_id}/toggle", headers=auth_headers)
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_delete_peer(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={"interface_id": iface.id, "name": "to-delete", "allowed_ips": "0.0.0.0/0"},
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/peers/{peer_id}", headers=auth_headers)
    assert resp.status_code == 204

    resp = await client.get(f"/api/peers/{peer_id}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_peer_config(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={
            "interface_id": iface.id,
            "name": "config-peer",
            "tunnel_address": "10.10.0.10/32",
            "allowed_ips": "0.0.0.0/0",
        },
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/peers/{peer_id}/config",
        params={"server_endpoint": "1.2.3.4:51820"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    content = resp.text
    assert "[Interface]" in content
    assert "[Peer]" in content
    assert "1.2.3.4:51820" in content


@pytest.mark.asyncio
async def test_filter_peers_by_interface(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    resp = await client.get(
        "/api/peers", params={"interface_id": iface.id}, headers=auth_headers
    )
    assert resp.status_code == 200
    for p in resp.json():
        assert p["interface_id"] == iface.id


@pytest.mark.asyncio
async def test_peer_not_found(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/peers/99999", headers=auth_headers)
    assert resp.status_code == 404
