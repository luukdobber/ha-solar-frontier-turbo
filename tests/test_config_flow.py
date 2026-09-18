"""Tests for the config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_frontier_turbo.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import HOST, SERIAL, FakeDevice


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """The happy path asks only for an address."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == SERIAL
    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_flow_trims_whitespace(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """A pasted address often carries stray spaces."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f"  {HOST} "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}


async def test_duplicate_is_rejected(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """The serial number keeps the same inverter from being added twice."""
    mock_config_entry.add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_updates_the_host(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Re-adding after a DHCP change corrects the stored address."""
    mock_config_entry.add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.99"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.99"


async def test_offline_inverter_cannot_be_added(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """Setup reads the serial, so an inverter asleep for the night cannot be added."""
    fake_device.offline = True

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_offline_inverter_can_be_added_once_it_answers(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """The same form must accept a retry at dawn without restarting the flow."""
    fake_device.offline = True

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    fake_device.offline = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == SERIAL


async def test_wrong_device_is_reported(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """A device that answers but has no serial is not this inverter."""
    fake_device.set(
        "measurements.xml",
        "<?xml version='1.0'?><root><Device Name='Something'></Device></root>",
    )

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_device"}


async def test_recovers_after_a_wrong_address(
    hass: HomeAssistant, fake_device: FakeDevice, mock_setup_entry: AsyncMock
) -> None:
    """The form can be corrected without restarting the flow."""
    fake_device.set("measurements.xml", "not xml at all")

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50"}
    )
    assert result["errors"] == {"base": "invalid_device"}

    fake_device.set(
        "measurements.xml",
        (
            "<?xml version='1.0'?><root><Device Name='Turbo 1P' "
            f"Serial='{SERIAL}'></Device></root>"
        ),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_updates_the_host(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Changing the address must update the entry, not create a second one."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.223"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.223"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_rejects_a_different_inverter(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Pointing an entry at another unit would silently mix up two systems."""
    mock_config_entry.add_to_hass(hass)
    fake_device.set(
        "measurements.xml",
        "<?xml version='1.0'?><root><Device Name='Turbo 1P' "
        "Serial='000000AI000000000000'></Device></root>",
    )

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.224"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_inverter"
    assert mock_config_entry.data[CONF_HOST] == HOST


async def test_reconfigure_reports_an_unreachable_address(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Reconfiguring needs a real answer, so it cannot be done blind."""
    mock_config_entry.add_to_hass(hass)
    fake_device.offline = True

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.225"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_reports_a_wrong_device(
    hass: HomeAssistant,
    fake_device: FakeDevice,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """An address that answers with something else is not accepted."""
    mock_config_entry.add_to_hass(hass)
    fake_device.set("measurements.xml", "not xml at all")

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.226"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_device"}


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Users with the web UI open may want to back off the polling."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 60},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_SCAN_INTERVAL: 60}


@pytest.mark.parametrize("scan", [5, 30, 3600])
async def test_options_flow_accepts_the_allowed_range(
    hass: HomeAssistant, init_integration: MockConfigEntry, scan: int
) -> None:
    """Both bounds of the documented range must be usable."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: scan}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
