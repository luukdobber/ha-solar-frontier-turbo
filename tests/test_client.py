"""Tests for the HTTP client's handling of the device's quirks."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.solar_frontier_turbo.client import SolarFrontierClient
from custom_components.solar_frontier_turbo.exceptions import (
    IncompleteResponseError,
    InvalidResponseError,
    SolarFrontierConnectionError,
)

from .conftest import HOST, SERIAL, FakeDevice, fixture


def _client(device: FakeDevice) -> SolarFrontierClient:
    """Build a client whose session is the fake device."""
    return SolarFrontierClient(device, HOST)  # type: ignore[arg-type]


async def test_base_url() -> None:
    """The device is HTTP-only."""
    assert _client(FakeDevice()).base_url == f"http://{HOST}/"


async def test_get_all_parses_the_fixture() -> None:
    """Get all parses the fixture."""
    snapshot = await _client(FakeDevice()).async_get_all()

    assert snapshot.serial == SERIAL
    assert snapshot.state == "Active"


async def test_all_requests_are_sequential() -> None:
    """The server serialises internally; parallelism only adds latency."""
    device = FakeDevice()
    device.delay = 0.01
    client = _client(device)

    await asyncio.gather(
        client.async_get_all(), client.async_get_all(), client.async_get_all()
    )

    assert device.max_in_flight == 1


async def test_truncated_xml_is_rejected() -> None:
    """A short read must raise rather than yield a partial measurement set."""
    device = FakeDevice()
    device.set("all.xml", fixture("all_active.xml").rsplit("</root>", 1)[0])

    with pytest.raises(IncompleteResponseError):
        await _client(device).async_get_all()

    assert device.requests == ["all.xml"]


async def test_verify_requires_a_serial() -> None:
    """Verify requires a serial."""
    device = FakeDevice()
    device.set("measurements.xml", "<?xml version='1.0'?><root><Device/></root>")

    with pytest.raises(InvalidResponseError, match="serial"):
        await _client(device).async_verify()


async def test_timeout_is_wrapped() -> None:
    """Timeout is wrapped."""
    device = FakeDevice()
    device.offline = True

    with pytest.raises(SolarFrontierConnectionError, match="timeout"):
        await _client(device).async_get_all()


async def test_dropped_connection_is_wrapped() -> None:
    """Absent gen.*/page.* paths drop the connection instead of returning 404."""
    device = FakeDevice()
    device.remove("all.xml")

    with pytest.raises(SolarFrontierConnectionError):
        await _client(device).async_get_all()
