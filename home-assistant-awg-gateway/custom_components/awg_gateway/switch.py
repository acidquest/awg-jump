"""Switch platform for AWG Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AwgGatewayConfigEntry
from .entity import AwgGatewayCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class AwgGatewaySwitchDescription(SwitchEntityDescription):
    """Describe a gateway switch."""

    is_on_fn: Callable[[dict], bool]
    async_set_fn: Callable[[object, bool], Awaitable[None]]


SWITCHES: tuple[AwgGatewaySwitchDescription, ...] = (
    AwgGatewaySwitchDescription(
        key="tunnel",
        translation_key="tunnel",
        is_on_fn=lambda data: bool((data.get("status") or {}).get("vpn_enabled")),
        async_set_fn=lambda coordinator, enabled: coordinator.async_set_tunnel(enabled),
    ),
    AwgGatewaySwitchDescription(
        key="kill_switch",
        translation_key="kill_switch",
        is_on_fn=lambda data: bool(data.get("kill_switch_enabled")),
        async_set_fn=lambda coordinator, enabled: coordinator.async_set_kill_switch(enabled),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AwgGatewayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AWG Gateway switches."""
    if not entry.runtime_data.coordinator.control_enabled:
        return
    async_add_entities(AwgGatewaySwitch(entry, description) for description in SWITCHES)


class AwgGatewaySwitch(AwgGatewayCoordinatorEntity, SwitchEntity):
    """Represent a gateway control switch."""

    entity_description: AwgGatewaySwitchDescription

    def __init__(self, entry: AwgGatewayConfigEntry, description: AwgGatewaySwitchDescription) -> None:
        super().__init__(entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        """Return switch state."""
        return self.entity_description.is_on_fn(self.coordinator.data.status)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        await self.entity_description.async_set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        await self.entity_description.async_set_fn(self.coordinator, False)
