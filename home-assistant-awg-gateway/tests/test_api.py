from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.awg_gateway.api import (
    AwgGatewayApiDisabledError,
    AwgGatewayClient,
    AwgGatewayControlDisabledError,
    AwgGatewayInvalidAuthError,
)


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def text(self):
        return str(self._payload)

    async def json(self, content_type=None):
        return self._payload


@pytest.mark.asyncio
async def test_get_status_success():
    session = AsyncMock()
    session.request.return_value = FakeResponse(200, {"status": {"vpn_enabled": True}})
    client = AwgGatewayClient(session=session, host="gw.local", port=8081, api_key="secret")

    payload = await client.async_get_status()

    assert payload["status"]["vpn_enabled"] is True


@pytest.mark.asyncio
async def test_invalid_auth_maps_to_exception():
    session = AsyncMock()
    session.request.return_value = FakeResponse(401, {"detail": "Invalid API key"})
    client = AwgGatewayClient(session=session, host="gw.local", port=8081, api_key="bad")

    with pytest.raises(AwgGatewayInvalidAuthError):
        await client.async_get_status()


@pytest.mark.asyncio
async def test_api_disabled_maps_to_exception():
    session = AsyncMock()
    session.request.return_value = FakeResponse(403, {"detail": "API access is disabled"})
    client = AwgGatewayClient(session=session, host="gw.local", port=8081, api_key="secret")

    with pytest.raises(AwgGatewayApiDisabledError):
        await client.async_get_status()


@pytest.mark.asyncio
async def test_control_disabled_maps_to_exception():
    session = AsyncMock()
    session.request.return_value = FakeResponse(403, {"detail": "API control mode is disabled"})
    client = AwgGatewayClient(session=session, host="gw.local", port=8081, api_key="secret")

    with pytest.raises(AwgGatewayControlDisabledError):
        await client.async_set_tunnel(True)
