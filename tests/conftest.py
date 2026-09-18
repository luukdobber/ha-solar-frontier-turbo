"""Shared fixtures for the Solar Frontier Turbo tests."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import aiohttp
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_frontier_turbo.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"

# Synthetic values. No real inverter's identifiers appear in this repository.
SERIAL = "123456AB000000000001"
HOST = "192.0.2.10"

# Endpoint path -> fixture file.
DEFAULT_RESPONSES: dict[str, str] = {
    "all.xml": "all_active.xml",
    "measurements.xml": "measurements.xml",
}


def fixture(name: str) -> str:
    """Read one fixture file."""
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """Stands in for an aiohttp response."""

    def __init__(self, body: bytes) -> None:
        """Hold the body the device would have sent."""
        self._body = body

    def raise_for_status(self) -> None:
        """Accept the status; the device answers 200 for everything it serves."""

    async def read(self) -> bytes:
        """Return the body."""
        return self._body


class FakeDevice:
    """Serves fixture responses in place of a real inverter.

    Acts as an aiohttp session so the client's own locking, retrying and header
    handling are exercised rather than stubbed out.
    """

    def __init__(self) -> None:
        """Start out as a healthy, generating inverter."""
        self.responses: dict[str, str] = {
            path: fixture(name) for path, name in DEFAULT_RESPONSES.items()
        }
        self.offline = False
        self.requests: list[str] = []
        self.delay = 0.0
        self.in_flight = 0
        self.max_in_flight = 0

    def set(self, path: str, body: str) -> None:
        """Replace one endpoint's response."""
        self.responses[path] = body

    def remove(self, path: str) -> None:
        """Make one endpoint behave as if it does not exist."""
        self.responses.pop(path, None)

    async def get(
        self, url: str, timeout: Any = None, headers: Any = None
    ) -> FakeResponse:
        """Answer a request the way the device would."""
        parts = urlsplit(url)
        path = parts.path.lstrip("/")
        if parts.query:
            path = f"{path}?{parts.query}"
        self.requests.append(path)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.offline:
                raise TimeoutError
            endpoint = path.split("?", 1)[0]
            if endpoint.startswith("page."):
                return FakeResponse(b"")
            if endpoint not in self.responses:
                # Absent gen.*/page.* paths drop the connection on the real device.
                raise aiohttp.ClientConnectionError(f"no such endpoint {endpoint}")
            return FakeResponse(self.responses[endpoint].encode("utf-8"))
        finally:
            self.in_flight -= 1


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom integrations in all tests."""
    return


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured inverter."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        data={CONF_HOST: HOST},
        unique_id=SERIAL,
    )


@pytest.fixture
def fake_device() -> Generator[FakeDevice]:
    """Serve fixture responses instead of touching the network."""
    device = FakeDevice()

    with (
        patch(
            "custom_components.solar_frontier_turbo.async_get_clientsession",
            return_value=device,
        ),
        patch(
            "custom_components.solar_frontier_turbo.config_flow.async_get_clientsession",
            return_value=device,
        ),
    ):
        yield device


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent the integration from being set up during config flow tests."""
    with patch(
        "custom_components.solar_frontier_turbo.async_setup_entry",
        return_value=True,
    ) as mocked:
        yield mocked


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_device: FakeDevice
) -> MockConfigEntry:
    """Set up the integration against the fake device."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
