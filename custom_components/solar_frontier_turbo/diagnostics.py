"""Diagnostics for the Solar Frontier Turbo integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import SolarFrontierConfigEntry
from .const import ATTR_IP_ADDRESS, ATTR_NETBIOS_NAME, ATTR_SERIAL

TO_REDACT = {CONF_HOST, ATTR_SERIAL, ATTR_IP_ADDRESS, ATTR_NETBIOS_NAME, "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarFrontierConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    poll = coordinator.data

    diagnostics: dict[str, Any] = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator": {
            "reachable": coordinator.reachable,
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
    }

    if poll is not None:
        diagnostics["poll"] = {
            "device": dict(poll.snapshot.device),
            "state": poll.state,
            "measurement_count": len(poll.measurements),
            "measurements": {
                key: {"value": value.value, "unit": value.unit}
                for key, value in poll.measurements.items()
            },
            "resolved_ac_power_w": poll.ac_power_w,
            "event_count": len(poll.snapshot.events),
            "newest_event": (
                asdict(poll.snapshot.events[0]) if poll.snapshot.events else None
            ),
            "software": [asdict(item) for item in poll.snapshot.software],
            "hardware": [asdict(item) for item in poll.snapshot.hardware],
        }

    return async_redact_data(diagnostics, TO_REDACT)
