"""Shared entity base class for the Solar Frontier Turbo integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_NAME, DEFAULT_DEVICE_NAME, DOMAIN, MANUFACTURER
from .coordinator import SolarFrontierCoordinator, SolarFrontierData
from .parser import DeviceSnapshot


def build_device_info(
    hass: HomeAssistant, serial: str, snapshot: DeviceSnapshot | None, base_url: str
) -> DeviceInfo:
    """Describe the inverter for the device registry.

    Without a poll, the fields only the device can report are left out rather than
    set to None, so the registry keeps what an earlier poll already stored.
    """
    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        manufacturer=MANUFACTURER,
        serial_number=serial,
        configuration_url=base_url,
    )

    if snapshot is not None:
        # The device reports no name of its own, only its model in @Name.
        model = snapshot.device.get(ATTR_NAME) or None
        device_info["model"] = model
        device_info["name"] = (
            f"{MANUFACTURER} {model}" if model else DEFAULT_DEVICE_NAME
        )
        # HMI APP is the user-facing firmware version.
        device_info["sw_version"] = snapshot.software_version("HMI", "APP")
        device_info["hw_version"] = snapshot.hardware_version("HMI")
    elif dr.async_get(hass).async_get_device(identifiers={(DOMAIN, serial)}) is None:
        device_info["name"] = DEFAULT_DEVICE_NAME

    return device_info


class SolarFrontierEntity(CoordinatorEntity[SolarFrontierCoordinator]):
    """Base entity backed by the `/all.xml` coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarFrontierCoordinator,
        serial: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_device_info = device_info

    @property
    def poll(self) -> SolarFrontierData | None:
        """The most recent poll, if any."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available while the device is answering."""
        data = self.coordinator.data
        return bool(super().available and data is not None and data.reachable)
