from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from custom_components.awg_gateway.api import AwgGatewayCannotConnectError
from custom_components.awg_gateway.coordinator import (
    DEVICE_POLL_OFFSET_SECONDS,
    AwgGatewayDevicesUpdateCoordinator,
    AwgGatewayStatusUpdateCoordinator,
)
from homeassistant.helpers.update_coordinator import UpdateFailed


@pytest.mark.asyncio
async def test_status_coordinator_loads_status():
    client = AsyncMock()
    client.async_get_status.return_value = {"api_control_enabled": True, "status": {"vpn_enabled": True}}

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayStatusUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    data = await coordinator._async_update_data()

    assert data.status["api_control_enabled"] is True


@pytest.mark.asyncio
async def test_devices_coordinator_loads_devices():
    client = AsyncMock()
    client.async_get_devices.return_value = {"devices": [{"identity_key": "mac:aa"}]}

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayDevicesUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    data = await coordinator._async_update_data()

    assert data.devices[0]["identity_key"] == "mac:aa"


@pytest.mark.asyncio
async def test_status_coordinator_preserves_monotonic_traffic():
    client = AsyncMock()
    client.async_get_status.return_value = {
        "api_control_enabled": True,
        "status": {"vpn_enabled": True},
        "traffic": {"current": {"local": {"rx_bytes": 100, "tx_bytes": 200}, "vpn": {"rx_bytes": 300, "tx_bytes": 400}}},
    }

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayStatusUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    first = await coordinator._async_update_data()
    coordinator.data = first

    client.async_get_status.return_value = {
        "api_control_enabled": True,
        "status": {"vpn_enabled": True},
        "traffic": {"current": {"local": {"rx_bytes": 90, "tx_bytes": 150}, "vpn": {"rx_bytes": 280, "tx_bytes": 390}}},
    }

    second = await coordinator._async_update_data()

    assert second.status["traffic"]["current"]["local"]["rx_bytes"] == 100
    assert second.status["traffic"]["current"]["local"]["tx_bytes"] == 200
    assert second.status["traffic"]["current"]["vpn"]["rx_bytes"] == 300
    assert second.status["traffic"]["current"]["vpn"]["tx_bytes"] == 400


@pytest.mark.asyncio
async def test_devices_coordinator_raises_on_failure_even_with_cached_data():
    client = AsyncMock()
    client.async_get_devices.return_value = {"devices": [{"identity_key": "mac:aa"}]}

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayDevicesUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    first = await coordinator._async_update_data()
    coordinator.data = first

    client.async_get_devices.side_effect = AwgGatewayCannotConnectError()

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_status_coordinator_raises_without_cached_data():
    client = AsyncMock()
    client.async_get_status.side_effect = AwgGatewayCannotConnectError()

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayStatusUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


def test_devices_poll_is_offset_from_status_poll():
    client = AsyncMock()

    with patch("homeassistant.helpers.frame.report_usage"):
        status_coordinator = AwgGatewayStatusUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )
        devices_coordinator = AwgGatewayDevicesUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    assert status_coordinator.update_interval == timedelta(seconds=30)
    assert devices_coordinator.update_interval == timedelta(seconds=30 + DEVICE_POLL_OFFSET_SECONDS)
