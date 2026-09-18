"""Parser for the Solar Frontier Turbo XML API.

Pure functions only: no I/O and no Home Assistant imports, so every branch can be
exercised against recorded device responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Final
from xml.etree import ElementTree as ET

from .const import NON_PRODUCING_STATES
from .exceptions import IncompleteResponseError, InvalidResponseError

PLACEHOLDER: Final = "---"

_DOCTYPE_RE: Final = re.compile(r"<!DOCTYPE", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Measurement:
    """A single `<Measurement>` element."""

    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    """An entry from the 60-slot event ring buffer."""

    message: str | None
    severity: str | None
    memory: str | None
    start: str | None
    end: str | None


@dataclass(frozen=True, slots=True)
class SoftwareVersion:
    """A `<Software>` entry from the versions section."""

    device: str | None
    name: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class HardwareVersion:
    """A `<Hardware>` entry from the versions section."""

    device: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """One parsed XML document from the device."""

    device: dict[str, str] = field(default_factory=dict)
    measurements: dict[str, Measurement] = field(default_factory=dict)
    state: str | None = None
    events: tuple[DeviceEvent, ...] = ()
    software: tuple[SoftwareVersion, ...] = ()
    hardware: tuple[HardwareVersion, ...] = ()

    @property
    def serial(self) -> str | None:
        """Serial number, the only `<Device>` attribute that is always present."""
        return self.device.get("Serial") or None

    def software_version(self, device: str, name: str) -> str | None:
        """Return the version of one software entry, if reported."""
        for entry in self.software:
            if entry.device == device and entry.name == name:
                return entry.version
        return None

    def hardware_version(self, device: str) -> str | None:
        """Return the version of one hardware entry, if reported."""
        for entry in self.hardware:
            if entry.device == device:
                return entry.version
        return None


def parse_device_xml(body: str | bytes) -> DeviceSnapshot:
    """Parse any of the device's XML endpoints.

    Raises IncompleteResponseError when the body was truncated.
    """
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    text = text.strip()

    if not text.endswith("</root>"):
        raise IncompleteResponseError(
            "XML document does not end with </root>; the connection closed early"
        )

    # ElementTree expands internal entities, and the inverter never sends a
    # doctype, so refusing one closes off entity-expansion attacks.
    if _DOCTYPE_RE.search(text):
        raise InvalidResponseError("XML document declares a doctype; refusing to parse")

    try:
        root = ET.fromstring(text)  # noqa: S314 - guarded against entity expansion above
    except ET.ParseError as err:
        raise InvalidResponseError(f"malformed XML document: {err}") from err

    device_element = root.find("Device")
    if device_element is None:
        raise InvalidResponseError("no <Device> element in response")

    measurements: dict[str, Measurement] = {}
    for element in device_element.iterfind("./Measurements/Measurement"):
        measurement_type = element.get("Type")
        raw_value = element.get("Value")
        if not measurement_type or raw_value is None or raw_value == PLACEHOLDER:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        measurements[measurement_type] = Measurement(value, element.get("Unit", ""))

    state_element = device_element.find("State")
    state = state_element.get("Value") if state_element is not None else None

    events = tuple(
        DeviceEvent(
            message=element.get("Message"),
            # The XML attribute names are misleading: the HTML events table calls
            # Severity "Type" and Type "Memory".
            severity=element.get("Severity"),
            memory=element.get("Type"),
            start=element.get("Start"),
            end=element.get("End") or None,
        )
        for element in device_element.iterfind("./Events/Event")
    )

    software = tuple(
        SoftwareVersion(
            element.get("Device"), element.get("Name"), element.get("Version")
        )
        for element in device_element.iterfind("./Versions/Software")
    )
    hardware = tuple(
        HardwareVersion(element.get("Device"), element.get("Version"))
        for element in device_element.iterfind("./Versions/Hardware")
    )

    return DeviceSnapshot(
        device=dict(device_element.attrib),
        measurements=measurements,
        state=state or None,
        events=events,
        software=software,
        hardware=hardware,
    )


def resolve_power(
    measurements: dict[str, Measurement], state: str | None
) -> float | None:
    """AC power in W: 0.0 when legitimately idle, None when unexpectedly absent.

    Returning None rather than 0.0 makes the integrator skip the interval instead
    of recording fictitious zero production.
    """
    if (measurement := measurements.get("AC_Power")) is not None:
        return measurement.value
    if state in NON_PRODUCING_STATES:
        return 0.0
    return None
