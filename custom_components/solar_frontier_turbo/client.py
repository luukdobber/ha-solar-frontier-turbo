"""HTTP client for the Solar Frontier Turbo inverter.

The device is GET-only, sends no Content-Length and serialises requests
internally, so every request goes through one lock.
"""

from __future__ import annotations

import asyncio

import aiohttp

from .const import DEFAULT_TIMEOUT, ENDPOINT_ALL, ENDPOINT_MEASUREMENTS
from .exceptions import InvalidResponseError, SolarFrontierConnectionError
from .parser import DeviceSnapshot, parse_device_xml


class SolarFrontierClient:
    """Read-only client for one inverter."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client for a host, without contacting it."""
        self._session = session
        self._host = host
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._request_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """The configured host."""
        return self._host

    @property
    def base_url(self) -> str:
        """Base URL of the device's web interface."""
        return f"http://{self._host}/"

    async def _get_bytes(self, path: str) -> bytes:
        async with self._request_lock:
            try:
                response = await self._session.get(
                    f"http://{self._host}/{path}", timeout=self._timeout
                )
                response.raise_for_status()
                return await response.read()
            except TimeoutError as err:
                raise SolarFrontierConnectionError(f"timeout fetching {path}") from err
            except aiohttp.ClientError as err:
                raise SolarFrontierConnectionError(
                    f"error fetching {path}: {err}"
                ) from err

    async def _get_xml(self, path: str) -> DeviceSnapshot:
        """Fetch and parse an XML endpoint.

        An incomplete body raises, and the caller treats that like any other
        failed poll.
        """
        body = (await self._get_bytes(path)).decode("utf-8", errors="replace")
        return parse_device_xml(body)

    async def async_get_all(self) -> DeviceSnapshot:
        """Fetch `/all.xml`: measurements, state, events and versions in one request."""
        return await self._get_xml(ENDPOINT_ALL)

    async def async_get_measurements(self) -> DeviceSnapshot:
        """Fetch `/measurements.xml`, used to validate the config flow."""
        return await self._get_xml(ENDPOINT_MEASUREMENTS)

    async def async_verify(self) -> DeviceSnapshot:
        """Confirm the host is this device and report a usable serial number."""
        snapshot = await self.async_get_measurements()
        if not snapshot.serial:
            raise InvalidResponseError("device reported no serial number")
        return snapshot
