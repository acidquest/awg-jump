"""Config flow for AWG Gateway."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import BooleanSelector, NumberSelector, NumberSelectorConfig, SelectSelector, SelectSelectorConfig

from .api import AwgGatewayApiDisabledError, AwgGatewayCannotConnectError, AwgGatewayClient, AwgGatewayInvalidAuthError
from .const import (
    CONF_DEVICE_SCOPE,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    DEFAULT_DEVICE_SCOPE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_HTTPS,
    DEFAULT_VERIFY_SSL,
    DEVICE_SCOPES,
    DOMAIN,
)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USE_HTTPS, default=DEFAULT_USE_HTTPS): BooleanSelector(),
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(min=5, max=300, mode="box")
        ),
    }
)


def _options_schema(config_entry: config_entries.ConfigEntry) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=config_entry.options.get(CONF_SCAN_INTERVAL, config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            ): NumberSelector(NumberSelectorConfig(min=5, max=300, mode="box")),
            vol.Required(
                CONF_VERIFY_SSL,
                default=config_entry.options.get(CONF_VERIFY_SSL, config_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
            ): BooleanSelector(),
            vol.Required(
                CONF_DEVICE_SCOPE,
                default=config_entry.options.get(CONF_DEVICE_SCOPE, config_entry.data.get(CONF_DEVICE_SCOPE, DEFAULT_DEVICE_SCOPE)),
            ): SelectSelector(SelectSelectorConfig(options=DEVICE_SCOPES)),
        }
    )


class AwgGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AWG Gateway."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return AwgGatewayOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            try:
                await self._async_validate(user_input)
            except AwgGatewayInvalidAuthError:
                errors["base"] = "invalid_auth"
            except AwgGatewayApiDisabledError:
                errors["base"] = "api_disabled"
            except AwgGatewayCannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                title = f"AWG Gateway ({user_input[CONF_HOST]})"
                return self.async_create_entry(
                    title=title,
                    data={
                        **user_input,
                        CONF_DEVICE_SCOPE: DEFAULT_DEVICE_SCOPE,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def _async_validate(self, user_input: dict[str, Any]) -> None:
        session = async_create_clientsession(self.hass, verify_ssl=user_input[CONF_VERIFY_SSL])
        client = AwgGatewayClient(
            session=session,
            host=user_input[CONF_HOST],
            port=user_input[CONF_PORT],
            api_key=user_input[CONF_API_KEY],
            use_https=user_input[CONF_USE_HTTPS],
            verify_ssl=user_input[CONF_VERIFY_SSL],
        )
        await client.async_get_status()


class AwgGatewayOptionsFlow(config_entries.OptionsFlow):
    """Manage AWG Gateway options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=_options_schema(self.config_entry))
