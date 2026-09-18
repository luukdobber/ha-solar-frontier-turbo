"""Constants for the Solar Frontier Turbo integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "solar_frontier_turbo"

LOGGER: Final = logging.getLogger(__package__)

MANUFACTURER: Final = "Solar Frontier"

# Used only if a firmware revision omits the <Device> @Name attribute.
DEFAULT_DEVICE_NAME: Final = "Solar Frontier Turbo"

CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

DEFAULT_TIMEOUT: Final = 30

FAILURES_BEFORE_UNAVAILABLE: Final = 3

# Integrating across a longer gap would invent energy.
MAX_INTEGRATION_GAP: Final = 300.0

# The full value set is unknown, so anything unrecognised must not integrate.
NON_PRODUCING_STATES: Final = frozenset({"Standby"})
PRODUCING_STATES: Final = frozenset({"Active"})

ENDPOINT_ALL: Final = "all.xml"
ENDPOINT_MEASUREMENTS: Final = "measurements.xml"

ATTR_SERIAL: Final = "Serial"
ATTR_NAME: Final = "Name"
ATTR_DATETIME: Final = "DateTime"
ATTR_IP_ADDRESS: Final = "IpAddress"
ATTR_NETBIOS_NAME: Final = "NetBiosName"
