"""Coordinator for AWG Gateway data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AwgGatewayApiDisabledError,
    AwgGatewayCannotConnectError,
    AwgGatewayClient,
    AwgGatewayControlDisabledError,
    AwgGatewayInvalidAuthError,
    AwgGatewayUnexpectedResponseError,
)
from .const import CONF_DEVICE_SCOPE, CONF_SCAN_INTERVAL, DOMAIN


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AwgGatewayCoordinatorData:
    """Snapshot of the gateway API."""

    status: dict[str, Any]
    devices: list[dict[str, Any]]
    devices_payload: dict[str, Any]


class AwgGatewayDataUpdateCoordinator(DataUpdateCoordinator[AwgGatewayCoordinatorData]):
    """Fetches and stores gateway state."""

    def __init__(self, hass: HomeAssistant, client: AwgGatewayClient, entry_id: str, options: dict[str, Any]) -> None:
        self.client = client
        self.entry_id = entry_id
        self.device_scope = options[CONF_DEVICE_SCOPE]
        update_interval = timedelta(seconds=options[CONF_SCAN_INTERVAL])
        super().__init__(
            hass,
            logger=LOGGER,
            name=f"{DOMAIN}_{entry_id}",
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> AwgGatewayCoordinatorData:
        try:
            status = await self.client.async_get_status()
            devices_payload = await self.client.async_get_devices(self.device_scope)
        except (
            AwgGatewayCannotConnectError,
            AwgGatewayApiDisabledError,
            AwgGatewayInvalidAuthError,
            AwgGatewayUnexpectedResponseError,
        ) as err:
            raise UpdateFailed(str(err)) from err

        devices = devices_payload.get("devices", [])
        if not isinstance(devices, list):
            raise UpdateFailed("Devices payload is invalid")

        return AwgGatewayCoordinatorData(
            status=status,
            devices=devices,
            devices_payload=devices_payload,
        )

    @property
    def control_enabled(self) -> bool:
        """Whether the gateway reports control support."""
        if self.data is None:
            return False
        return bool(self.data.status.get("api_control_enabled"))

    async def async_set_tunnel(self, enabled: bool) -> None:
        """Set tunnel state and refresh."""
        try:
            await self.client.async_set_tunnel(enabled)
        except AwgGatewayControlDisabledError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()

    async def async_set_kill_switch(self, enabled: bool) -> None:
        """Set kill switch state and refresh."""
        try:
            await self.client.async_set_kill_switch(enabled)
        except AwgGatewayControlDisabledError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_request_refresh()
