"""Exceptions raised by the Solar Frontier Turbo client and parsers."""

from __future__ import annotations


class SolarFrontierError(Exception):
    """Base class for all errors raised by this integration."""


class SolarFrontierConnectionError(SolarFrontierError):
    """The device could not be reached.

    Expected for hours every night: the inverter is powered from the PV array.
    """


class IncompleteResponseError(SolarFrontierError):
    """The response body was truncated.

    The device sends no Content-Length and ends the body by closing the
    connection, so truncation is only detectable from the payload itself.
    """


class InvalidResponseError(SolarFrontierError):
    """The response was received in full but could not be interpreted."""


class StaleSelectionError(SolarFrontierError):
    """A yield endpoint returned a period other than the one requested.

    Period selection is global server-side state with no session, so anyone
    browsing the web UI can change what the next read returns.
    """
