"""Golden-file tests for the parsers, using real captured device responses."""

from __future__ import annotations

import pytest

from custom_components.solar_frontier_turbo.exceptions import (
    IncompleteResponseError,
    InvalidResponseError,
)
from custom_components.solar_frontier_turbo.parser import (
    parse_device_xml,
    resolve_power,
)

from .conftest import SERIAL, fixture


def test_parse_all_xml_daytime() -> None:
    """The generating capture yields all eight measurements plus state."""
    snapshot = parse_device_xml(fixture("all_active.xml"))

    assert snapshot.serial == SERIAL
    assert snapshot.device["Name"] == "Turbo 1P"
    assert snapshot.state == "Active"
    assert set(snapshot.measurements) == {
        "AC_Voltage",
        "AC_Current",
        "AC_Power",
        "AC_Frequency",
        "DC_Voltage",
        "DC_Current",
        "DC_Power",
        "Temp",
    }
    assert snapshot.measurements["AC_Power"].value == pytest.approx(342.0)
    # &#176;C must be resolved by the XML parser, not by a regex.
    assert snapshot.measurements["Temp"].unit == "\u00b0C"
    assert snapshot.software_version("HMI", "APP") == "1.45.0"
    assert snapshot.hardware_version("HMI") == "2"


def test_parse_all_xml_night() -> None:
    """The Standby capture has only three measurements and no AC power."""
    snapshot = parse_device_xml(fixture("all_standby.xml"))

    assert snapshot.state == "Standby"
    assert set(snapshot.measurements) == {"AC_Voltage", "AC_Frequency", "DC_Voltage"}
    assert "AC_Power" not in snapshot.measurements
    assert "Temp" not in snapshot.measurements


def test_event_attribute_names_are_remapped() -> None:
    """The XML's Severity/Type attributes carry misleading names."""
    snapshot = parse_device_xml(fixture("all_active.xml"))

    assert len(snapshot.events) == 3
    newest = snapshot.events[0]
    assert newest.message == "Grid voltage high"
    assert newest.severity == "Warning"
    assert newest.memory == "User"


def test_truncated_all_xml_is_rejected() -> None:
    """A body cut short must be refused, not parsed."""
    truncated = fixture("all_active.xml").rstrip()[:-200]

    with pytest.raises(IncompleteResponseError):
        parse_device_xml(truncated)


@pytest.mark.parametrize("chopped", [1, 7, 50, 200, 2000])
def test_truncation_at_various_points_is_rejected(chopped: int) -> None:
    """Truncation is only detectable from the payload, so check several cuts."""
    # Trailing whitespace is stripped before the check, so cut real content.
    with pytest.raises(IncompleteResponseError):
        parse_device_xml(fixture("all_active.xml").rstrip()[:-chopped])


def test_empty_yields_element_does_not_crash() -> None:
    """yields.xml is always an empty <Yields> on this firmware."""
    snapshot = parse_device_xml(fixture("yields_empty.xml"))

    assert snapshot.serial == SERIAL
    assert snapshot.measurements == {}
    assert snapshot.state is None


def test_missing_device_element_is_rejected() -> None:
    """A complete document without <Device> is not usable."""
    with pytest.raises(InvalidResponseError):
        parse_device_xml("<?xml version='1.0'?><root></root>")


def test_measurements_endpoint() -> None:
    """measurements.xml carries the live values used to validate setup."""
    snapshot = parse_device_xml(fixture("measurements.xml"))

    assert snapshot.serial == SERIAL
    assert len(snapshot.measurements) == 8
    assert snapshot.state is None


class TestResolvePower:
    """AC power resolution is what keeps phantom energy out of the total."""

    def test_present_value_is_used(self) -> None:
        """Present value is used."""
        snapshot = parse_device_xml(fixture("all_active.xml"))
        assert resolve_power(snapshot.measurements, snapshot.state) == pytest.approx(
            342.0
        )

    def test_absent_in_standby_is_zero(self) -> None:
        """Absent in standby is zero."""
        snapshot = parse_device_xml(fixture("all_standby.xml"))
        assert resolve_power(snapshot.measurements, snapshot.state) == 0.0

    def test_absent_in_unknown_state_is_none(self) -> None:
        """An unrecognised state must not be recorded as zero production."""
        assert resolve_power({}, "GridMonitoring") is None

    def test_absent_without_state_is_none(self) -> None:
        """Absent without state is none."""
        assert resolve_power({}, None) is None


def test_single_measurement_response() -> None:
    """The set was observed dropping to DC_Voltage alone."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root><Device "
        f"Name='Turbo 1P' Serial='{SERIAL}'><Measurements>"
        "<Measurement Value='22.900' Unit='V' Type='DC_Voltage'/>"
        "</Measurements><State Value='Standby'/></Device></root>"
    )
    snapshot = parse_device_xml(body)

    assert set(snapshot.measurements) == {"DC_Voltage"}
    assert resolve_power(snapshot.measurements, snapshot.state) == 0.0


def test_zero_measurement_response() -> None:
    """An empty measurement set must parse rather than raise."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root><Device "
        f"Name='Turbo 1P' Serial='{SERIAL}'><Measurements></Measurements>"
        "<State Value='Standby'/></Device></root>"
    )
    snapshot = parse_device_xml(body)

    assert snapshot.measurements == {}
    assert resolve_power(snapshot.measurements, snapshot.state) == 0.0


def test_device_attributes_beyond_serial_are_optional() -> None:
    """Other firmware revisions omit or add <Device> attributes."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root>"
        f"<Device Serial='{SERIAL}'></Device></root>"
    )
    snapshot = parse_device_xml(body)

    assert snapshot.serial == SERIAL
    assert snapshot.device.get("Name") is None


@pytest.mark.parametrize("bad_value", ["---", "n/a", "", "1,5"])
def test_malformed_measurement_value_is_skipped(bad_value: str) -> None:
    """A placeholder or junk value must not become a number."""
    body = (
        "<?xml version='1.0' encoding='UTF-8'?><root><Device "
        f"Serial='{SERIAL}'><Measurements>"
        f"<Measurement Value='{bad_value}' Unit='W' Type='AC_Power'/>"
        "<Measurement Value='1.5' Unit='A' Type='AC_Current'/>"
        "</Measurements></Device></root>"
    )
    snapshot = parse_device_xml(body)

    assert set(snapshot.measurements) == {"AC_Current"}


class TestDefensiveParsing:
    """Branches that only fire on firmware variants or corrupted responses."""

    def test_unknown_version_lookups_return_none(self) -> None:
        """Unknown version lookups return none."""
        snapshot = parse_device_xml(fixture("all_active.xml"))

        assert snapshot.software_version("HMI", "NoSuchModule") is None
        assert snapshot.hardware_version("NoSuchDevice") is None

    def test_entity_expansion_bomb_is_refused(self) -> None:
        """ElementTree expands internal entities, so a doctype must be refused."""
        bomb = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE root ["
            ' <!ENTITY a "aaaaaaaaaa">'
            ' <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            ' <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            "]>"
            '<root><Device Serial="X">&c;</Device></root>'
        )

        with pytest.raises(InvalidResponseError, match="doctype"):
            parse_device_xml(bomb)

    def test_lowercase_doctype_is_also_refused(self) -> None:
        """The check must not be defeated by casing."""
        with pytest.raises(InvalidResponseError, match="doctype"):
            parse_device_xml(
                "<!doctype root []><root><Device Serial='X'></Device></root>"
            )

    def test_malformed_xml_is_rejected(self) -> None:
        """A complete-looking but unparseable body must not pass silently."""
        with pytest.raises(InvalidResponseError, match="malformed"):
            parse_device_xml("<root><Device attr=unquoted></Device></root>")

    def test_measurement_without_a_type_is_skipped(self) -> None:
        """Measurement without a type is skipped."""
        body = (
            "<?xml version='1.0'?><root><Device Serial='X'><Measurements>"
            "<Measurement Value='1.0' Unit='W'/>"
            "<Measurement Unit='W' Type='AC_Power'/>"
            "</Measurements></Device></root>"
        )

        assert parse_device_xml(body).measurements == {}
