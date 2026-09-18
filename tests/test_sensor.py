"""Tests for the sensor platform against real captured responses."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from .conftest import SERIAL, FakeDevice, fixture

PREFIX = "sensor.solar_frontier_turbo_1p_"


@pytest.fixture(autouse=True)
def _frozen_clock(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin the clock to the day the fixtures describe."""
    freezer.move_to("2024-05-05T12:00:00+00:00")
    return freezer


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_measurement_sensors_from_fixture(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """All eight measurements become sensors with the right metadata."""
    power = hass.states.get(f"{PREFIX}ac_power")
    assert power is not None
    assert float(power.state) == pytest.approx(342.0)
    assert power.attributes[ATTR_DEVICE_CLASS] == "power"
    assert power.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert power.attributes[ATTR_UNIT_OF_MEASUREMENT] == "W"

    temperature = hass.states.get(f"{PREFIX}temperature")
    assert temperature is not None
    assert float(temperature.state) == pytest.approx(31.4)

    for suffix in (
        "ac_voltage",
        "ac_current",
        "ac_frequency",
        "dc_power",
        "dc_voltage",
        "dc_current",
    ):
        assert hass.states.get(f"{PREFIX}{suffix}") is not None


async def test_operating_state_is_plain_text(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Never a strict enum: the full value set is unknown."""
    state = hass.states.get(f"{PREFIX}operating_state")

    assert state.state == "Active"
    assert ATTR_DEVICE_CLASS not in state.attributes


async def test_standby_zeroes_power_and_current_only(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Missing power means zero; missing temperature means unavailable."""
    fake_device.set("all.xml", fixture("all_standby.xml"))
    await _advance(hass, freezer)

    assert hass.states.get(f"{PREFIX}ac_power").state == "0.0"
    assert hass.states.get(f"{PREFIX}dc_power").state == "0.0"
    assert hass.states.get(f"{PREFIX}ac_current").state == "0.0"
    assert hass.states.get(f"{PREFIX}dc_current").state == "0.0"

    # 0 degrees C would be a lie.
    assert hass.states.get(f"{PREFIX}temperature").state == STATE_UNAVAILABLE

    # Still reported at night, so still real values.
    assert float(hass.states.get(f"{PREFIX}ac_voltage").state) == pytest.approx(231.2)
    assert float(hass.states.get(f"{PREFIX}dc_voltage").state) == pytest.approx(205.6)


async def test_absent_voltage_is_unavailable_not_zero(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AC voltage vanished once while the grid was present at 230 V."""
    fake_device.set(
        "all.xml",
        fixture("all_standby.xml").replace(
            "<Measurement Value='231.200' Unit='V' Type='AC_Voltage'/>", ""
        ),
    )
    await _advance(hass, freezer)

    assert hass.states.get(f"{PREFIX}ac_voltage").state == STATE_UNAVAILABLE


async def test_unknown_state_does_not_zero_power(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An unrecognised state must not be recorded as zero production."""
    fake_device.set(
        "all.xml", fixture("all_standby.xml").replace("Standby", "GridMonitoring")
    )
    await _advance(hass, freezer)

    assert hass.states.get(f"{PREFIX}ac_power").state == STATE_UNAVAILABLE


async def test_measurement_set_can_drop_to_one(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The set was observed dropping to DC_Voltage alone, with AC voltage gone."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root><Device Name='Turbo 1P' "
        f"Serial='{SERIAL}'><Measurements>"
        "<Measurement Value='22.900' Unit='V' Type='DC_Voltage'/>"
        "</Measurements><State Value='Standby'/></Device></root>"
    )
    fake_device.set("all.xml", body)
    await _advance(hass, freezer)

    assert float(hass.states.get(f"{PREFIX}dc_voltage").state) == pytest.approx(22.9)
    assert hass.states.get(f"{PREFIX}ac_voltage").state == STATE_UNAVAILABLE
    assert hass.states.get(f"{PREFIX}ac_power").state == "0.0"


async def test_measurement_set_can_be_empty(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A zero-element response must not crash the platform."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root><Device Name='Turbo 1P' "
        f"Serial='{SERIAL}'><Measurements></Measurements>"
        "<State Value='Standby'/></Device></root>"
    )
    fake_device.set("all.xml", body)
    await _advance(hass, freezer)

    assert hass.states.get(f"{PREFIX}dc_voltage").state == STATE_UNAVAILABLE
    assert hass.states.get(f"{PREFIX}ac_power").state == "0.0"


async def test_measurement_entities_persist_when_the_set_shrinks(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities must not disappear at dusk and reappear at dawn."""
    registry = er.async_get(hass)
    before = len(er.async_entries_for_config_entry(registry, init_integration.entry_id))

    fake_device.set("all.xml", fixture("all_standby.xml"))
    await _advance(hass, freezer)

    after = len(er.async_entries_for_config_entry(registry, init_integration.entry_id))
    assert after == before


async def test_energy_total_sensor_metadata(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The Energy Dashboard source must be a total_increasing energy sensor."""
    state = hass.states.get(f"{PREFIX}energy_total")

    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == "energy"
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.TOTAL_INCREASING
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "kWh"


async def test_energy_total_accumulates_across_polls(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """126.68 W over two 31 s intervals is a small but non-zero amount."""
    assert float(hass.states.get(f"{PREFIX}energy_total").state) == 0.0

    await _advance(hass, freezer)
    await _advance(hass, freezer)

    accumulated = float(hass.states.get(f"{PREFIX}energy_total").state)
    assert accumulated > 0.0
    assert accumulated < 0.01


async def test_energy_total_stays_available_while_the_device_sleeps(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The accumulator is ours, so it survives the inverter powering down."""
    fake_device.offline = True
    for _ in range(6):
        await _advance(hass, freezer)

    assert hass.states.get(f"{PREFIX}energy_total").state != STATE_UNAVAILABLE


async def test_no_year_or_per_year_energy_sensors_exist(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The corrupt accumulator must not reach the Energy Dashboard at all."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, init_integration.entry_id)

    for entry in entries:
        assert "year" not in entry.unique_id.lower()


async def test_last_event_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The newest event carries its severity and timestamps as attributes."""
    last = hass.states.get(f"{PREFIX}last_event")

    assert last.state == "Grid voltage high"
    assert last.attributes["severity"] == "Warning"
    assert last.attributes["memory"] == "User"
    assert last.attributes["oldest_event_start"] == "2024-05-01T06:07:08"


async def test_unique_ids_are_serial_based(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Two inverters must not collide."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, init_integration.entry_id)

    assert entries
    for entry in entries:
        assert entry.unique_id.startswith(SERIAL)


async def test_energy_total_is_restored_after_a_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_device: FakeDevice,
) -> None:
    """The accumulator must survive a restart end to end, not just in theory."""
    entity_id = f"{PREFIX}energy_total"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "12.5"),
                {"native_value": 12.5, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(entity_id).state) == pytest.approx(12.5)


async def test_energy_total_starts_at_zero_without_a_restored_value(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A fresh install begins at zero rather than at an unknown state."""
    assert float(hass.states.get(f"{PREFIX}energy_total").state) == 0.0


async def test_energy_total_keeps_counting_after_a_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Restoring the value must not freeze the sensor."""
    entity_id = f"{PREFIX}energy_total"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "12.5"),
                {"native_value": 12.5, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await _advance(hass, freezer)
    await _advance(hass, freezer)

    assert float(hass.states.get(entity_id).state) > 12.5


async def test_energy_total_follows_the_measurement_interval(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """It integrates on every measurement poll, not on the history interval."""
    entity_id = f"{PREFIX}energy_total"
    assert float(hass.states.get(entity_id).state) == 0.0

    # A single measurement interval, far short of the 300 s history interval.
    await _advance(hass, freezer)
    await _advance(hass, freezer)
    after_two_polls = float(hass.states.get(entity_id).state)

    assert after_two_polls > 0.0
