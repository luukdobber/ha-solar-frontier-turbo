"""The Solar Frontier Turbo integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo

from .client import SolarFrontierClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, LOGGER
from .coordinator import SolarFrontierCoordinator
from .entity import build_device_info

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class SolarFrontierRuntimeData:
    """Runtime data for one configured inverter."""

    client: SolarFrontierClient
    coordinator: SolarFrontierCoordinator
    serial: str
    device_info: DeviceInfo


type SolarFrontierConfigEntry = ConfigEntry[SolarFrontierRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarFrontierConfigEntry
) -> bool:
    """Set up Solar Frontier Turbo from a config entry."""
    client = SolarFrontierClient(async_get_clientsession(hass), entry.data[CONF_HOST])

    coordinator = SolarFrontierCoordinator(
        hass,
        entry,
        client,
        timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    # Deliberately not async_config_entry_first_refresh: the inverter is asleep for
    # hours every night, and setup must not wait for dawn.
    await coordinator.async_refresh()

    if (serial := entry.unique_id) is None:
        raise ConfigEntryError(
            "config entry has no serial number; remove the inverter and add it again"
        )

    snapshot = coordinator.data.snapshot if coordinator.data else None
    if snapshot is not None and snapshot.serial != serial:
        LOGGER.warning(
            "%s reports serial %s but this entry was configured for %s; the IP "
            "address may now point at a different inverter",
            client.host,
            snapshot.serial or "nothing",
            serial,
        )

    entry.runtime_data = SolarFrontierRuntimeData(
        client=client,
        coordinator=coordinator,
        serial=serial,
        device_info=build_device_info(hass, serial, snapshot, client.base_url),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarFrontierConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: SolarFrontierConfigEntry
) -> None:
    """Reload when the poll interval changes."""
    await hass.config_entries.async_reload(entry.entry_id)
