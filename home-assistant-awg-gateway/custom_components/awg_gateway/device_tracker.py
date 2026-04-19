"""Device tracker platform for AWG Gateway."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AwgGatewayConfigEntry
from .coordinator import AwgGatewayDevicesUpdateCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AwgGatewayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AWG Gateway device trackers."""
    known: dict[str, AwgGatewayDeviceTracker] = {}

    @callback
    def _sync_entities() -> None:
        new_entities: list[AwgGatewayDeviceTracker] = []
        devices = entry.runtime_data.devices_coordinator.data.devices

        for device in devices:
            identity_key = device.get("identity_key")
            if not identity_key or identity_key in known:
                continue
            entity = AwgGatewayDeviceTracker(entry, identity_key)
            known[identity_key] = entity
            new_entities.append(entity)

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(entry.runtime_data.devices_coordinator.async_add_listener(_sync_entities))
    _sync_entities()


class AwgGatewayDeviceTracker(CoordinatorEntity[AwgGatewayDevicesUpdateCoordinator], ScannerEntity):
    """Represent a tracked LAN device reported by the gateway."""

    _attr_has_entity_name = False

    def __init__(self, entry: AwgGatewayConfigEntry, identity_key: str) -> None:
        super().__init__(entry.runtime_data.devices_coordinator)
        self._entry = entry
        self._identity_key = identity_key
        self._last_device: dict[str, Any] | None = None
        self._attr_unique_id = identity_key

    @property
    def _current_device(self) -> dict[str, Any] | None:
        for device in self.coordinator.data.devices:
            if device.get("identity_key") == self._identity_key:
                self._last_device = device
                return device
        return None

    @property
    def _device(self) -> dict[str, Any] | None:
        current_device = self._current_device
        if current_device is not None:
            return current_device
        return self._last_device

    @property
    def name(self) -> str | None:
        """Return tracker name."""
        device = self._device
        if device is None:
            return None
        return device.get("display_name")

    @property
    def is_connected(self) -> bool:
        """Return connection state."""
        device = self._current_device
        if device is None:
            return False
        return bool(device.get("is_present"))

    @property
    def state(self) -> str:
        """Return Home Assistant tracker state."""
        return STATE_HOME if self.is_connected else STATE_NOT_HOME

    @property
    def source_type(self) -> SourceType:
        """Return tracker source type."""
        return SourceType.ROUTER

    @property
    def ip_address(self) -> str | None:
        """Return current IP address."""
        device = self._device
        return None if device is None else device.get("current_ip")

    @property
    def mac_address(self) -> str | None:
        """Return current MAC address."""
        device = self._device
        return None if device is None else device.get("mac_address")

    @property
    def device_info(self) -> DeviceInfo:
        """Return tracker device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._identity_key)},
            name=self.name,
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose selected raw fields from the API."""
        device = self._device
        if device is None:
            return None
        return {
            "current_ip": device.get("current_ip"),
            "hostname": device.get("hostname"),
            "manual_alias": device.get("manual_alias"),
            "presence_state": device.get("presence_state"),
            "is_active": device.get("is_active"),
            "last_route_target": device.get("last_route_target"),
            "total_bytes": device.get("total_bytes"),
            "first_seen_at": device.get("first_seen_at"),
            "last_seen_at": device.get("last_seen_at"),
            "last_traffic_at": device.get("last_traffic_at"),
            "last_presence_check_at": device.get("last_presence_check_at"),
            "last_present_at": device.get("last_present_at"),
            "last_absent_at": device.get("last_absent_at"),
        }
