"""Coordinators for AWG Gateway data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, Generic, TypeVar

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
DEVICE_POLL_OFFSET_SECONDS = 7
_T = TypeVar("_T")


@dataclass(slots=True)
class AwgGatewayStatusData:
    """Snapshot of the gateway telemetry API."""

    status: dict[str, Any]


@dataclass(slots=True)
class AwgGatewayDevicesData:
    """Snapshot of the gateway device API."""

    devices: list[dict[str, Any]]
    devices_payload: dict[str, Any]


class _AwgGatewayBaseCoordinator(DataUpdateCoordinator[_T], Generic[_T]):
    """Base class with shared error handling."""

    def _handle_update_error(self, err: Exception, previous: Any) -> Any:
        if previous is None:
            raise UpdateFailed(str(err)) from err
        return previous


class AwgGatewayStatusUpdateCoordinator(_AwgGatewayBaseCoordinator[AwgGatewayStatusData]):
    """Fetches and stores gateway telemetry."""

    def __init__(self, hass: HomeAssistant, client: AwgGatewayClient, entry_id: str, options: dict[str, Any]) -> None:
        self.client = client
        super().__init__(
            hass,
            logger=LOGGER,
            name=f"{DOMAIN}_{entry_id}_status",
            update_interval=timedelta(seconds=options[CONF_SCAN_INTERVAL]),
        )

    async def _async_update_data(self) -> AwgGatewayStatusData:
        previous = self.data
        try:
            status = await self.client.async_get_status()
        except (
            AwgGatewayCannotConnectError,
            AwgGatewayApiDisabledError,
            AwgGatewayInvalidAuthError,
            AwgGatewayUnexpectedResponseError,
        ) as err:
            previous_data = self._handle_update_error(err, previous)
            LOGGER.warning("Using cached AWG Gateway status due to update error: %s", err)
            return previous_data

        if not isinstance(status, dict):
            raise UpdateFailed("Status payload is invalid")

        merged_status = self._merge_status_with_previous(status, previous.status if previous else None)
        return AwgGatewayStatusData(status=merged_status)

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

    def _merge_status_with_previous(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if previous is None:
            return current

        merged = dict(current)
        current_traffic = current.get("traffic")
        previous_traffic = previous.get("traffic")

        if isinstance(current_traffic, dict) and isinstance(previous_traffic, dict):
            merged["traffic"] = self._merge_traffic(current_traffic, previous_traffic)

        return merged

    def _merge_traffic(self, current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        current_current = current.get("current")
        previous_current = previous.get("current")

        if not isinstance(current_current, dict) or not isinstance(previous_current, dict):
            return merged

        current_snapshot = dict(current_current)
        for scope in ("local", "vpn"):
            merged_scope = self._merge_counter_scope(
                current_current.get(scope),
                previous_current.get(scope),
            )
            if merged_scope is not None:
                current_snapshot[scope] = merged_scope

        merged["current"] = current_snapshot
        return merged

    @staticmethod
    def _merge_counter_scope(current: Any, previous: Any) -> dict[str, int] | None:
        if not isinstance(current, dict):
            return previous if isinstance(previous, dict) else None
        if not isinstance(previous, dict):
            return current

        merged = dict(current)
        for key in ("rx_bytes", "tx_bytes"):
            current_value = current.get(key)
            previous_value = previous.get(key)
            if isinstance(current_value, int) and isinstance(previous_value, int):
                merged[key] = max(current_value, previous_value)
            elif current_value is None and isinstance(previous_value, int):
                merged[key] = previous_value
        return merged


class AwgGatewayDevicesUpdateCoordinator(_AwgGatewayBaseCoordinator[AwgGatewayDevicesData]):
    """Fetches and stores tracked devices."""

    def __init__(self, hass: HomeAssistant, client: AwgGatewayClient, entry_id: str, options: dict[str, Any]) -> None:
        self.client = client
        self.device_scope = options[CONF_DEVICE_SCOPE]
        super().__init__(
            hass,
            logger=LOGGER,
            name=f"{DOMAIN}_{entry_id}_devices",
            update_interval=timedelta(seconds=options[CONF_SCAN_INTERVAL] + DEVICE_POLL_OFFSET_SECONDS),
        )

    async def _async_update_data(self) -> AwgGatewayDevicesData:
        try:
            devices_payload = await self.client.async_get_devices(self.device_scope)
        except (
            AwgGatewayCannotConnectError,
            AwgGatewayApiDisabledError,
            AwgGatewayInvalidAuthError,
            AwgGatewayUnexpectedResponseError,
        ) as err:
            raise UpdateFailed(str(err)) from err

        if not isinstance(devices_payload, dict):
            raise UpdateFailed("Devices payload is invalid")

        devices = devices_payload.get("devices", [])
        if not isinstance(devices, list):
            raise UpdateFailed("Devices payload is invalid")

        return AwgGatewayDevicesData(
            devices=devices,
            devices_payload=devices_payload,
        )
