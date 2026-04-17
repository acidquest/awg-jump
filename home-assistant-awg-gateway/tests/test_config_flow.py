from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT

from custom_components.awg_gateway.api import AwgGatewayApiDisabledError, AwgGatewayInvalidAuthError
from custom_components.awg_gateway.config_flow import AwgGatewayConfigFlow
from custom_components.awg_gateway.const import CONF_SCAN_INTERVAL, CONF_USE_HTTPS, CONF_VERIFY_SSL


def _build_flow() -> AwgGatewayConfigFlow:
    flow = AwgGatewayConfigFlow()
    flow.hass = object()
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    return flow


@pytest.mark.asyncio
async def test_user_step_shows_form():
    flow = _build_flow()

    result = await flow.async_step_user()

    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_user_step_creates_entry():
    flow = _build_flow()

    with (
        patch("custom_components.awg_gateway.config_flow.async_create_clientsession", return_value=object()),
        patch(
            "custom_components.awg_gateway.config_flow.AwgGatewayClient.async_get_status",
            return_value={"status": {"vpn_enabled": True}},
        ),
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "gateway.local",
                CONF_PORT: 8081,
                CONF_USE_HTTPS: True,
                CONF_VERIFY_SSL: False,
                CONF_API_KEY: "secret",
                CONF_SCAN_INTERVAL: 30,
            }
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "AWG Gateway (gateway.local)"
    assert result["data"][CONF_HOST] == "gateway.local"


@pytest.mark.asyncio
async def test_user_step_invalid_auth():
    flow = _build_flow()

    with (
        patch("custom_components.awg_gateway.config_flow.async_create_clientsession", return_value=object()),
        patch(
            "custom_components.awg_gateway.config_flow.AwgGatewayClient.async_get_status",
            side_effect=AwgGatewayInvalidAuthError,
        ),
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "gateway.local",
                CONF_PORT: 8081,
                CONF_USE_HTTPS: True,
                CONF_VERIFY_SSL: True,
                CONF_API_KEY: "bad",
                CONF_SCAN_INTERVAL: 30,
            }
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_step_api_disabled():
    flow = _build_flow()

    with (
        patch("custom_components.awg_gateway.config_flow.async_create_clientsession", return_value=object()),
        patch(
            "custom_components.awg_gateway.config_flow.AwgGatewayClient.async_get_status",
            side_effect=AwgGatewayApiDisabledError,
        ),
    ):
        result = await flow.async_step_user(
            {
                CONF_HOST: "gateway.local",
                CONF_PORT: 8081,
                CONF_USE_HTTPS: True,
                CONF_VERIFY_SSL: True,
                CONF_API_KEY: "secret",
                CONF_SCAN_INTERVAL: 30,
            }
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "api_disabled"}
