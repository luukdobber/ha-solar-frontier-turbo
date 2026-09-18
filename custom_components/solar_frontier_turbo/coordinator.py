"""Update coordinator for the Solar Frontier Turbo integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import SolarFrontierClient
from .const import (
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    LOGGER,
    NON_PRODUCING_STATES,
    PRODUCING_STATES,
)
from .exceptions import SolarFrontierError
from .parser import DeviceSnapshot, Measurement, resolve_power


@dataclass(frozen=True, slots=True)
class SolarFrontierData:
    """One `/all.xml` poll."""

    snapshot: DeviceSnapshot
    ac_power_w: float | None
    # Wall clock: time.monotonic() stalls while the host is suspended.
    received: datetime
    reachable: bool = True

    @property
    def measurements(self) -> dict[str, Measurement]:
        """Measurements present in this poll."""
        return self.snapshot.measurements

    @property
    def state(self) -> str | None:
        """The reported operating state."""
        return self.snapshot.state

    @property
    def is_producing_state(self) -> bool | None:
        """Whether the state is known to mean 'producing'. None if unrecognised."""
        if self.state in PRODUCING_STATES:
            return True
        if self.state in NON_PRODUCING_STATES:
            return False
        return None


class SolarFrontierCoordinator(DataUpdateCoordinator[SolarFrontierData]):
    """Polls `/all.xml`: measurements, operating state, events and versions.

    Tolerates the nightly outage without log spam or flapping entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarFrontierClient,
        update_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self._known_measurements: frozenset[str] | None = None
        self._reported_measurement_sets: set[frozenset[str]] = set()
        self._reported_states: set[str] = set()
        self._failures = 0
        self._unreachable_logged = False

    @property
    def reachable(self) -> bool:
        """False once the device has missed enough consecutive polls."""
        return self._failures < FAILURES_BEFORE_UNAVAILABLE

    async def _async_update_data(self) -> SolarFrontierData:
        try:
            snapshot = await self.client.async_get_all()
        except SolarFrontierError as err:
            return self._handle_failure(err)

        self._note_success()
        self._log_state(snapshot)
        self._log_measurement_set(snapshot)

        power = resolve_power(snapshot.measurements, snapshot.state)
        if power is None and snapshot.state is not None:
            LOGGER.info(
                "AC_Power absent while state is %r, which is not a known idle "
                "state; skipping this interval rather than recording zero "
                "(measurements present: %s)",
                snapshot.state,
                sorted(snapshot.measurements) or "none",
            )

        return SolarFrontierData(
            snapshot=snapshot, ac_power_w=power, received=dt_util.utcnow()
        )

    def _handle_failure(self, err: Exception) -> SolarFrontierData:
        """Absorb a failed poll, keeping the last reading if there is one."""
        self._note_failure(err)
        if self.data is None:
            raise UpdateFailed(f"could not reach {self.client.host}: {err}") from err
        return replace(self.data, reachable=self.reachable)

    def _note_failure(self, err: Exception) -> None:
        self._failures += 1
        LOGGER.debug("Poll %s failed: %s", self._failures, err)

        if self._unreachable_logged or self._failures < FAILURES_BEFORE_UNAVAILABLE:
            return
        self._unreachable_logged = True
        LOGGER.info(
            "%s is not responding after %s consecutive polls (%s). This is expected "
            "overnight: the inverter is powered from the PV array and shuts down "
            "when array voltage collapses",
            self.client.host,
            self._failures,
            err,
        )

    def _note_success(self) -> None:
        if self._unreachable_logged:
            LOGGER.info(
                "%s is responding again after %s failed polls",
                self.client.host,
                self._failures,
            )
        self._failures = 0
        self._unreachable_logged = False

    def _log_state(self, snapshot: DeviceSnapshot) -> None:
        state = snapshot.state
        if state is None or state in self._reported_states:
            return
        self._reported_states.add(state)
        if state in PRODUCING_STATES or state in NON_PRODUCING_STATES:
            LOGGER.debug("Operating state %r observed", state)
            return
        LOGGER.info(
            "Unrecognised operating state %r (measurements present: %s). Energy "
            "integration is paused for unknown states; please report this state "
            "so it can be classified",
            state,
            sorted(snapshot.measurements) or "none",
        )

    def _log_measurement_set(self, snapshot: DeviceSnapshot) -> None:
        present = frozenset(snapshot.measurements)
        previous = self._known_measurements
        self._known_measurements = present

        if previous is None:
            LOGGER.debug(
                "Initial measurement set (%s): %s", snapshot.state, sorted(present)
            )
            self._reported_measurement_sets.add(present)
            return
        if present == previous:
            return

        message = (
            "Measurement set changed while state is %r: %s element(s), added %s, "
            "removed %s"
        )
        args = (
            snapshot.state,
            len(present),
            sorted(present - previous) or "none",
            sorted(previous - present) or "none",
        )
        # At dawn and dusk the set oscillates every poll, so each distinct set is
        # only worth one info line; the rest is detail for a debug log.
        if present in self._reported_measurement_sets:
            LOGGER.debug(message, *args)
            return
        self._reported_measurement_sets.add(present)
        LOGGER.info(message, *args)
