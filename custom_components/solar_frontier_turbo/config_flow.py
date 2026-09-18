"""Config flow for the Solar Frontier Turbo integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .client import SolarFrontierClient
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .exceptions import SolarFrontierConnectionError, SolarFrontierError

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="off")
        )
    }
)


def _interval_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=1,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


class SolarFrontierTurboConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar Frontier Turbo."""

    VERSION = 1

    async def _async_read_serial(self, host: str) -> str:
        """Confirm the host is an inverter and return its serial number."""
        client = SolarFrontierClient(async_get_clientsession(self.hass), host)
        snapshot = await client.async_verify()
        return snapshot.serial or ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                serial = await self._async_read_serial(host)
            except SolarFrontierConnectionError as err:
                LOGGER.debug("Could not reach %s during setup: %s", host, err)
                errors["base"] = "cannot_connect"
            except SolarFrontierError as err:
                LOGGER.debug("Unexpected response from %s during setup: %s", host, err)
                errors["base"] = "invalid_device"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(title=host, data={CONF_HOST: host})

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the inverter's address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                serial = await self._async_read_serial(host)
            except SolarFrontierConnectionError:
                errors["base"] = "cannot_connect"
            except SolarFrontierError:
                errors["base"] = "invalid_device"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_mismatch(reason="wrong_inverter")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> SolarFrontierTurboOptionsFlow:
        """Return the options flow."""
        return SolarFrontierTurboOptionsFlow()


class SolarFrontierTurboOptionsFlow(OptionsFlow):
    """Handle the poll interval options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): _interval_selector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
