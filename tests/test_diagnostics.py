"""Tests for the diagnostics download."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_frontier_turbo.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import SERIAL, FakeDevice, fixture


@pytest.fixture(autouse=True)
def _frozen_clock(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin the clock to the day the fixtures describe."""
    freezer.move_to("2024-05-05T12:00:00+00:00")
    return freezer


async def test_diagnostics_describe_the_current_poll(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The download must carry enough to diagnose the open questions."""
    data = await async_get_config_entry_diagnostics(hass, init_integration)

    assert data["poll"]["state"] == "Active"
    assert data["poll"]["measurement_count"] == 8
    assert data["poll"]["resolved_ac_power_w"] == pytest.approx(342.0)
    assert sorted(data["poll"]["measurements"]) == [
        "AC_Current",
        "AC_Frequency",
        "AC_Power",
        "AC_Voltage",
        "DC_Current",
        "DC_Power",
        "DC_Voltage",
        "Temp",
    ]
    assert data["coordinator"]["reachable"] is True
    assert data["coordinator"]["update_interval_seconds"] == 30


async def test_diagnostics_redact_identifiers(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The serial and addresses are redacted from a shared download."""
    data = await async_get_config_entry_diagnostics(hass, init_integration)

    assert data["entry"]["data"]["host"] == "**REDACTED**"
    assert data["poll"]["device"]["Serial"] == "**REDACTED**"
    assert data["poll"]["device"]["IpAddress"] == "**REDACTED**"
    assert SERIAL not in str(data)


async def test_diagnostics_survive_a_standby_night(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_device: FakeDevice,
) -> None:
    """Diagnostics must work with the shrunken night-time measurement set."""
    fake_device.set("all.xml", fixture("all_standby.xml"))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    data = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert data["poll"]["state"] == "Standby"
    assert data["poll"]["measurement_count"] == 3
    assert data["poll"]["resolved_ac_power_w"] == 0.0
