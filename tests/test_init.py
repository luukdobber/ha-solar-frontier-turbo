"""Tests for setup, teardown and the nightly-outage behaviour."""

from __future__ import annotations

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.solar_frontier_turbo.const import (
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
)

from .conftest import HOST, SERIAL, FakeDevice, fixture

POLL = timedelta(seconds=31)


@pytest.fixture(autouse=True)
def _frozen_clock(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin the clock to the day the fixtures describe."""
    freezer.move_to("2024-05-05T12:00:00+00:00")
    return freezer


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(POLL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_device_registry_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The device is described from the XML and versions data."""
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, SERIAL), init_integration.entry_id
    )

    assert device is not None
    assert device.manufacturer == "Solar Frontier"
    assert device.model == "Turbo 1P"
    # The device reports no friendly name of its own, so its model identifies it.
    assert device.name == "Solar Frontier Turbo 1P"
    assert device.serial_number == SERIAL
    assert device.sw_version == "1.45.0"
    assert device.hw_version == "2"
    assert device.configuration_url == f"http://{HOST}/"


async def test_restart_while_asleep_loads_with_unavailable_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_device: FakeDevice,
) -> None:
    """A restart at night must not leave the integration unloaded until dawn."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    fake_device.offline = True
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state == (
        STATE_UNAVAILABLE
    )
    # The accumulator belongs to Home Assistant, so it reports without a poll.
    assert hass.states.get("sensor.solar_frontier_turbo_1p_energy_total").state == "0.0"

    # Loading blind must not wipe what an earlier poll stored.
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Solar Frontier Turbo 1P"
    assert device.model == "Turbo 1P"
    assert device.sw_version == "1.45.0"


async def test_first_ever_setup_while_asleep_falls_back_to_a_model_less_name(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_device: FakeDevice
) -> None:
    """With no poll and no stored device, the model is simply not known yet."""
    fake_device.offline = True
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Solar Frontier Turbo"
    assert device.model is None


async def test_recovers_at_dawn_without_user_action(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities come back by themselves once the inverter wakes up."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    fake_device.offline = True
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state == (
        STATE_UNAVAILABLE
    )

    fake_device.offline = False
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state == "342.0"


async def test_entities_survive_a_few_failed_polls(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A single timeout must not flip entities to unavailable."""
    assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state != (
        STATE_UNAVAILABLE
    )

    fake_device.offline = True
    for _ in range(FAILURES_BEFORE_UNAVAILABLE - 1):
        await _advance(hass, freezer)
        assert hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state != (
            STATE_UNAVAILABLE
        )


async def test_entities_go_unavailable_after_repeated_failures(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Once the threshold is reached the data really is stale."""
    fake_device.offline = True
    for _ in range(FAILURES_BEFORE_UNAVAILABLE):
        await _advance(hass, freezer)

    assert (
        hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state
        == STATE_UNAVAILABLE
    )


async def test_entities_recover_when_the_device_returns(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Dawn recovery needs no reload."""
    fake_device.offline = True
    for _ in range(FAILURES_BEFORE_UNAVAILABLE):
        await _advance(hass, freezer)
    assert (
        hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state
        == STATE_UNAVAILABLE
    )

    fake_device.offline = False
    await _advance(hass, freezer)

    assert (
        hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state
        != STATE_UNAVAILABLE
    )


async def test_nightly_outage_is_not_logged_every_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ten hours of unreachability must not fill the log."""
    caplog.set_level(logging.INFO, logger="custom_components.solar_frontier_turbo")
    fake_device.offline = True

    for _ in range(40):
        await _advance(hass, freezer)

    integration_records = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.solar_frontier_turbo")
    ]
    not_responding = [
        record
        for record in integration_records
        if "is not responding" in record.message
    ]
    assert len(not_responding) == 1
    assert not [
        record for record in integration_records if record.levelno >= logging.WARNING
    ]


async def test_return_is_logged_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Coming back online is worth exactly one log line."""
    caplog.set_level(logging.INFO, logger="custom_components.solar_frontier_turbo")
    fake_device.offline = True
    for _ in range(FAILURES_BEFORE_UNAVAILABLE + 2):
        await _advance(hass, freezer)

    fake_device.offline = False
    for _ in range(3):
        await _advance(hass, freezer)

    back = [record for record in caplog.records if "responding again" in record.message]
    assert len(back) == 1


async def test_truncated_response_is_absorbed_like_any_failed_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A body cut short is discarded, and one bad poll changes nothing."""
    fake_device.set("all.xml", fixture("all_active.xml")[:-200])

    await _advance(hass, freezer)

    power = hass.states.get("sensor.solar_frontier_turbo_1p_ac_power")
    assert power.state != STATE_UNAVAILABLE
    # The partial body must not have been parsed into a reading.
    assert float(power.state) == pytest.approx(342.0)


async def test_persistently_truncated_responses_mark_entities_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Repeated short reads must not be mistaken for a shrinking measurement set."""
    fake_device.set("all.xml", fixture("all_active.xml")[:-200])

    for _ in range(FAILURES_BEFORE_UNAVAILABLE):
        await _advance(hass, freezer)

    assert (
        hass.states.get("sensor.solar_frontier_turbo_1p_ac_power").state
        == STATE_UNAVAILABLE
    )


async def test_unknown_state_is_reported(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unobserved state must be logged with enough detail to act on."""
    caplog.set_level(logging.INFO, logger="custom_components.solar_frontier_turbo")
    fake_device.set(
        "all.xml", fixture("all_standby.xml").replace("Standby", "GridMonitoring")
    )

    await _advance(hass, freezer)

    assert "Unrecognised operating state 'GridMonitoring'" in caplog.text
    assert (
        hass.states.get("sensor.solar_frontier_turbo_1p_operating_state").state
        == "GridMonitoring"
    )


async def test_measurement_set_change_is_reported(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The set shrinking from eight to three is worth a line."""
    caplog.set_level(logging.INFO, logger="custom_components.solar_frontier_turbo")
    fake_device.set("all.xml", fixture("all_standby.xml"))

    await _advance(hass, freezer)

    assert "Measurement set changed" in caplog.text
    assert "'AC_Power'" in caplog.text


async def test_an_oscillating_measurement_set_is_only_reported_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_device: FakeDevice,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At dusk the set flaps every poll, which must not fill the log at info level."""
    caplog.set_level(logging.INFO, logger="custom_components.solar_frontier_turbo")
    active = fixture("all_active.xml")
    standby = fixture("all_standby.xml")

    for _cycle in range(4):
        fake_device.set("all.xml", standby)
        await _advance(hass, freezer)
        fake_device.set("all.xml", active)
        await _advance(hass, freezer)

    # Eight polls, seven changes, but only the standby set is new: setup already
    # recorded the active one.
    assert caplog.text.count("Measurement set changed") == 1


async def test_options_change_reloads(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Changing the poll interval takes effect without a restart."""
    hass.config_entries.async_update_entry(
        init_integration, options={"scan_interval": 60}
    )
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.LOADED
    assert (
        init_integration.runtime_data.coordinator.update_interval.total_seconds() == 60
    )
