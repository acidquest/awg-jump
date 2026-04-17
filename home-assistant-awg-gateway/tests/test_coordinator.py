from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from custom_components.awg_gateway.coordinator import AwgGatewayDataUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_loads_status_and_devices():
    client = AsyncMock()
    client.async_get_status.return_value = {"api_control_enabled": True, "status": {"vpn_enabled": True}}
    client.async_get_devices.return_value = {"devices": [{"identity_key": "mac:aa"}]}

    with patch("homeassistant.helpers.frame.report_usage"):
        coordinator = AwgGatewayDataUpdateCoordinator(
            object(),
            client,
            "entry-1",
            {"scan_interval_seconds": 30, "device_scope": "marked"},
        )

    data = await coordinator._async_update_data()

    assert data.status["api_control_enabled"] is True
    assert data.devices[0]["identity_key"] == "mac:aa"
