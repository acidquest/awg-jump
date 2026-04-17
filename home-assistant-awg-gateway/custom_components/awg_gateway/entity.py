"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AwgGatewayConfigEntry
from .const import DOMAIN


class AwgGatewayCoordinatorEntity(CoordinatorEntity):
    """Base entity bound to the AWG Gateway coordinator."""

    _attr_has_entity_name = True

    def __init__(self, entry: AwgGatewayConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._entry = entry
        self._attr_device_info = entry.runtime_data.device_info

    @property
    def gateway_identifiers(self) -> set[tuple[str, str]]:
        """Return the gateway device identifiers."""
        return {(DOMAIN, self._entry.entry_id)}
