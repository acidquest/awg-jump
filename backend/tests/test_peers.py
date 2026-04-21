import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.interface import Interface, InterfaceMode, InterfaceProtocol
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
    assert data["interface_protocol"] == "awg"
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
async def test_update_peer_private_key_updates_public_key(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    create_resp = await client.post(
        "/api/peers",
        json={"interface_id": iface.id, "name": "keys-peer", "allowed_ips": "0.0.0.0/0"},
        headers=auth_headers,
    )
    peer_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/peers/{peer_id}",
        json={
            "private_key": "manual-private-key-base64==",
            "preshared_key": None,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["public_key"] == "fake_key_base64=="
    assert resp.json()["preshared_key"] is None

    detail_resp = await client.get(f"/api/peers/{peer_id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["private_key"] == "manual-private-key-base64=="
    assert detail_resp.json()["public_key"] == "fake_key_base64=="


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
async def test_wg_peer_config_plain_wireguard(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    wg_iface = Interface(
        name="wg0",
        mode=InterfaceMode.server,
        protocol=InterfaceProtocol.wg,
        private_key="aGVsbG8gd2cga2V5IGhlbGxvIHdnIGtleQ==",
        public_key="d2ctcHVibGljLWtleS10ZXN0",
        listen_port=51821,
        address="10.11.0.1/24",
        dns="10.11.0.1",
        enabled=True,
    )
    db_session.add(wg_iface)
    await db_session.commit()
    await db_session.refresh(wg_iface)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CLASSIC_WG", "on")
        from backend.config import reload_settings
        reload_settings()

        create_resp = await client.post(
            "/api/peers",
            json={
                "interface_id": wg_iface.id,
                "name": "wg-peer",
                "tunnel_address": "10.11.0.2/32",
                "allowed_ips": "10.11.0.2/32",
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        peer_id = create_resp.json()["id"]

        resp = await client.get(
            f"/api/peers/{peer_id}/config",
            params={"server_endpoint": "1.2.3.4:51821"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        content = resp.text
        assert "1.2.3.4:51821" in content
        assert "DNS = 10.11.0.1" in content
        assert "Jc =" not in content
        assert "S1 =" not in content
        assert "H1 =" not in content

    from backend.config import reload_settings
    reload_settings()


@pytest.mark.asyncio
async def test_create_peer_from_awg_conf(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    resp = await client.post(
        "/api/peers",
        json={
            "interface_id": iface.id,
            "name": "imported-awg-peer",
            "conf_text": """
[Interface]
PrivateKey = imported-private-key==
Address = 10.10.0.50/32
DNS = 1.1.1.1
Jc = 7
Jmin = 50
Jmax = 90
S1 = 83
S2 = 47
S3 = 121
S4 = 33
H1 = 3928541027
H2 = 1847392610
H3 = 2938471056
H4 = 847392015

[Peer]
PublicKey = imported-public-key==
PresharedKey = imported-psk==
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
""".strip(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "imported-awg-peer"
    assert data["tunnel_address"] == "10.10.0.50/32"
    assert data["allowed_ips"] == "0.0.0.0/0"
    assert data["preshared_key"] == "imported-psk=="
    assert data["public_key"] == "fake_key_base64=="


@pytest.mark.asyncio
async def test_reject_wg_conf_on_awg_interface(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    result = await db_session.execute(select(Interface).where(Interface.name == "awg0"))
    iface = result.scalar_one()

    resp = await client.post(
        "/api/peers",
        json={
            "interface_id": iface.id,
            "conf_text": """
[Interface]
PrivateKey = imported-private-key==
Address = 10.10.0.60/32

[Peer]
PublicKey = imported-public-key==
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0
""".strip(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "does not match interface protocol" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_peer_not_found(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/peers/99999", headers=auth_headers)
    assert resp.status_code == 404
