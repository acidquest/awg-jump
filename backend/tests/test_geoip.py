import asyncio
from unittest.mock import patch, AsyncMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_sources(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/geoip/sources", headers=auth_headers)
    assert resp.status_code == 200
    sources = resp.json()
    assert isinstance(sources, list)
    assert len(sources) >= 1
    src = sources[0]
    assert "country_code" in src
    assert "display_name" in src
    assert "url" in src
    assert "created_at" in src
    assert "ipset_name" not in src


@pytest.mark.asyncio
async def test_geoip_status(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.get("/api/geoip/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "update_running" in data
    assert "sources" in data
    assert isinstance(data["sources"], list)


@pytest.mark.asyncio
async def test_trigger_update_starts_background_task(
    client: AsyncClient, auth_headers: dict
) -> None:
    with patch(
        "backend.routers.geoip.run_geoip_update",
        new_callable=AsyncMock,
    ) as mock_update:
        resp = await client.post("/api/geoip/update", headers=auth_headers)
        assert resp.status_code == 202
        assert resp.json()["status"] == "started"
        # BackgroundTasks запускает задачу асинхронно — даём немного времени
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_trigger_update_conflict_when_running(
    client: AsyncClient, auth_headers: dict
) -> None:
    import backend.routers.geoip as geoip_module

    original = geoip_module._update_running
    geoip_module._update_running = True
    try:
        resp = await client.post("/api/geoip/update", headers=auth_headers)
        assert resp.status_code == 409
    finally:
        geoip_module._update_running = original


@pytest.mark.asyncio
async def test_geoip_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/geoip/sources")
    assert resp.status_code == 401


# ── Unit tests: geoip_fetcher ─────────────────────────────────────────────

def test_parse_prefixes_ipdeny_format() -> None:
    from backend.services.geoip_fetcher import _parse_prefixes

    text = """
# ipdeny.com RU
1.2.3.0/24
# comment
10.0.0.0/8
192.168.0.0/16

invalid_line_no_dot_or_colon
2001:db8::/32
"""
    result = _parse_prefixes(text)
    assert "1.2.3.0/24" in result
    assert "10.0.0.0/8" in result
    assert "192.168.0.0/16" in result
    assert "2001:db8::/32" in result
    # Комментарии и пустые строки не включаются
    for item in result:
        assert not item.startswith("#")
        assert item.strip()


def test_parse_prefixes_empty() -> None:
    from backend.services.geoip_fetcher import _parse_prefixes
    assert _parse_prefixes("") == []
    assert _parse_prefixes("# only comments\n# another") == []


@pytest.mark.asyncio
async def test_fetch_with_mock(tmp_path) -> None:
    from backend.services.geoip_fetcher import fetch
    from backend.models.geoip import GeoipSource
    from datetime import datetime, timezone

    source = GeoipSource(
        id=1,
        name="Test",
        display_name="Test",
        url="http://test.invalid/ru.zone",
        country_code="ru_test",
        ipset_name="geoip_local",
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )

    fake_content = "1.2.3.0/24\n4.5.6.0/23\n# comment\n"

    with (
        patch("backend.services.geoip_fetcher.settings") as mock_settings,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.geoip_cache_dir = str(tmp_path)
        mock_settings.geoip_fetch_timeout = 10

        mock_response = AsyncMock()
        mock_response.text = fake_content
        mock_response.raise_for_status = AsyncMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        prefixes = await fetch(source)

    assert "1.2.3.0/24" in prefixes
    assert "4.5.6.0/23" in prefixes


@pytest.mark.asyncio
async def test_create_source_uses_generated_url_and_rebuilds(
    client: AsyncClient, auth_headers: dict
) -> None:
    with (
        patch("backend.services.geoip_fetcher.validate_source_url", new_callable=AsyncMock) as mock_validate,
        patch("backend.routers.geoip.run_geoip_update", new_callable=AsyncMock) as mock_update,
    ):
        resp = await client.post(
            "/api/geoip/sources",
            headers=auth_headers,
            json={"country_code": "by", "display_name": "Belarus", "url": None},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["country_code"] == "by"
    assert data["display_name"] == "Belarus"
    assert data["url"] == "https://www.ipdeny.com/ipblocks/data/countries/by.zone"
    mock_validate.assert_awaited_once_with(data["url"])
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_delete_source_rejects_last_source(
    client: AsyncClient, auth_headers: dict
) -> None:
    resp = await client.delete("/api/geoip/sources/1", headers=auth_headers)
    assert resp.status_code == 422
    assert "At least one GeoIP source must remain" in resp.text


@pytest.mark.asyncio
async def test_update_source_toggle_enabled_rebuilds(
    client: AsyncClient, auth_headers: dict, db_session
) -> None:
    from datetime import datetime, timezone
    from backend.models.geoip import GeoipSource

    db_session.add(GeoipSource(
        name="Belarus",
        display_name="Belarus",
        url="https://www.ipdeny.com/ipblocks/data/countries/by.zone",
        country_code="by",
        ipset_name="geoip_local",
        enabled=True,
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    with patch("backend.routers.geoip.run_geoip_update", new_callable=AsyncMock):
        resp = await client.put(
            "/api/geoip/sources/2",
            headers=auth_headers,
            json={"enabled": False},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
