"""The AWG Gateway integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from aiohttp import ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

from .api import AwgGatewayClient
from .const import CONF_DEVICE_SCOPE, CONF_SCAN_INTERVAL, CONF_USE_HTTPS, CONF_VERIFY_SSL, DEFAULT_DEVICE_SCOPE, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import AwgGatewayDataUpdateCoordinator


@dataclass(slots=True)
class AwgGatewayRuntimeData:
    """Runtime state for a config entry."""

    session: ClientSession
    client: AwgGatewayClient
    coordinator: AwgGatewayDataUpdateCoordinator
    device_info: DeviceInfo


AwgGatewayConfigEntry: TypeAlias = ConfigEntry[AwgGatewayRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AwgGatewayConfigEntry) -> bool:
    """Set up AWG Gateway from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.options.get(CONF_VERIFY_SSL, entry.data[CONF_VERIFY_SSL]))
    client = AwgGatewayClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        api_key=entry.data[CONF_API_KEY],
        use_https=entry.data[CONF_USE_HTTPS],
        verify_ssl=entry.options.get(CONF_VERIFY_SSL, entry.data[CONF_VERIFY_SSL]),
    )
    options = {
        CONF_SCAN_INTERVAL: entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        CONF_DEVICE_SCOPE: entry.options.get(CONF_DEVICE_SCOPE, entry.data.get(CONF_DEVICE_SCOPE, DEFAULT_DEVICE_SCOPE)),
    }
    coordinator = AwgGatewayDataUpdateCoordinator(hass, client, entry.entry_id, options)
    await coordinator.async_config_entry_first_refresh()

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="AWG",
        model="Gateway",
        name=entry.title,
        configuration_url=client.base_url.removesuffix("/api/access"),
    )

    runtime_data = AwgGatewayRuntimeData(
        session=session,
        client=client,
        coordinator=coordinator,
        device_info=device_info,
    )
    entry.runtime_data = runtime_data
    hass.data[DOMAIN][entry.entry_id] = runtime_data
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AwgGatewayConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: AwgGatewayConfigEntry) -> None:
    """Reload a config entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)
