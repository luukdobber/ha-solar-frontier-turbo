"""Edge cases in setup, coordinator state and sensor value derivation."""

from __future__ import annotations

from datetime import datetime
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_frontier_turbo.const import DOMAIN
from custom_components.solar_frontier_turbo.coordinator import SolarFrontierData
from custom_components.solar_frontier_turbo.parser import DeviceSnapshot

from .conftest import HOST, SERIAL, FakeDevice, fixture


@pytest.fixture(autouse=True)
def _frozen_clock(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin the clock to the day the fixtures describe."""
    freezer.move_to("2024-05-05T12:00:00+00:00")
    return freezer


async def test_a_device_that_stops_reporting_its_serial_is_warned_about(
    hass: HomeAssistant, fake_device: FakeDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """Identity comes from the config entry, so this loads but must not pass silently."""
    fake_device.set(
        "all.xml",
        "<?xml version='1.0'?><root><Device Name='Turbo 1P'>"
        "<State Value='Active'/></Device></root>",
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=SERIAL)
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "may now point at a different inverter" in caplog.text


async def test_setup_fails_permanently_without_a_serial_in_the_entry(
    hass: HomeAssistant, fake_device: FakeDevice
) -> None:
    """Retrying cannot conjure a unique ID, so this must not sit in SETUP_RETRY."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=None)
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_serial_mismatch_is_warned_about(
    hass: HomeAssistant, fake_device: FakeDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """A reused IP address could silently point at another inverter."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: HOST}, unique_id="000000AI000000000000"
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "may now point at a different inverter" in caplog.text


async def test_all_measurement_entities_exist_when_set_up_at_night(
    hass: HomeAssistant, fake_device: FakeDevice
) -> None:
    """Standby reports three of eight measurements; every entity must still exist."""
    fake_device.set("all.xml", fixture("all_standby.xml"))
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=SERIAL)
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power") is not None
    assert hass.states.get("sensor.solar_frontier_turbo_1p_temperature") is not None


def test_producing_state_is_unknown_for_unobserved_values() -> None:
    """Anything unrecognised must not be classified either way."""
    snapshot = DeviceSnapshot(device={"Serial": SERIAL}, state="Starting")
    poll = SolarFrontierData(
        snapshot=snapshot,
        ac_power_w=None,
        received=datetime(2024, 5, 5, 12, 0, tzinfo=dt_util.UTC),
    )

    assert poll.is_producing_state is None


async def test_unsupported_measurement_type_is_logged_once(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A variant model reporting an unknown type should leave a trace, not an entity."""
    fake_device.set(
        "all.xml",
        "<?xml version='1.0'?><root>"
        f"<Device Serial='{SERIAL}' Name='Turbo 1P'><State Value='Active'/>"
        "<Measurements><Measurement Value='1.000' Unit='kOhm' Type='Insulation'/>"
        "</Measurements></Device></root>",
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=SERIAL)
    entry.add_to_hass(hass)

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.solar_frontier_turbo"
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert "no sensor: ['Insulation']" in caplog.text
    assert hass.states.get("sensor.solar_frontier_turbo_1p_insulation") is None


async def test_device_name_falls_back_when_the_model_is_absent(
    hass: HomeAssistant, fake_device: FakeDevice
) -> None:
    """Other firmware revisions may omit every <Device> attribute but Serial."""
    fake_device.set(
        "all.xml",
        "<?xml version='1.0'?><root>"
        f"<Device Serial='{SERIAL}'><State Value='Active'/></Device></root>",
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=SERIAL)
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), entry.entry_id
    )
    assert device is not None
    assert device.name == "Solar Frontier Turbo"
    assert device.model is None
