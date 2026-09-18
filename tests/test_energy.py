"""Tests for the self-integrated energy sensor.

Phantom energy is worse than a crash: it looks plausible on the Energy
Dashboard. These are the regressions that matter most.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util
import pytest

from custom_components.solar_frontier_turbo.const import MAX_INTEGRATION_GAP
from custom_components.solar_frontier_turbo.coordinator import SolarFrontierData
from custom_components.solar_frontier_turbo.parser import (
    DeviceSnapshot,
    Measurement,
    resolve_power,
)
from custom_components.solar_frontier_turbo.sensor import SolarFrontierEnergySensor


def _snapshot(state: str, measurements: dict[str, float]) -> DeviceSnapshot:
    return DeviceSnapshot(
        device={"Serial": "TEST"},
        measurements={
            key: Measurement(value, "W") for key, value in measurements.items()
        },
        state=state,
    )


def _poll(
    state: str, measurements: dict[str, float], received: datetime
) -> SolarFrontierData:
    snapshot = _snapshot(state, measurements)
    return SolarFrontierData(
        snapshot=snapshot,
        ac_power_w=resolve_power(snapshot.measurements, state),
        received=received,
    )


class Harness:
    """Drives the energy sensor without a running Home Assistant."""

    def __init__(self) -> None:
        """Create the sensor wired to a stub coordinator."""
        self.coordinator = MagicMock()
        self.coordinator.data = None
        self.coordinator.last_update_success = True
        self.sensor = SolarFrontierEnergySensor(self.coordinator, "TEST", MagicMock())
        self.clock = datetime(2024, 5, 5, 12, 0, tzinfo=dt_util.UTC)

    def sample(
        self,
        state: str = "Active",
        power: float | None = 0.0,
        advance: float = 30.0,
    ) -> None:
        """Feed one poll, `advance` seconds after the previous one."""
        self.clock += timedelta(seconds=advance)
        measurements = {} if power is None else {"AC_Power": power}
        self.coordinator.data = _poll(state, measurements, self.clock)
        self.sensor._integrate()

    def fail(self, advance: float = 30.0) -> None:
        """Simulate a poll the coordinator could not complete."""
        self.clock += timedelta(seconds=advance)
        self.coordinator.last_update_success = False
        self.sensor._integrate()
        self.coordinator.last_update_success = True

    @property
    def kwh(self) -> float:
        """The sensor's current value."""
        return self.sensor.native_value

    @property
    def wh(self) -> float:
        """The raw accumulator."""
        return self.sensor._wh


def test_constant_power_integrates_to_expected_energy() -> None:
    """1000 W held for an hour is 1 kWh."""
    harness = Harness()
    harness.sample(power=1000.0, advance=0.0)
    for _ in range(120):
        harness.sample(power=1000.0, advance=30.0)

    assert harness.kwh == pytest.approx(1.0, rel=1e-9)


def test_trapezoidal_rule_is_used() -> None:
    """A ramp integrates as the average of the two endpoints."""
    harness = Harness()
    harness.sample(power=0.0, advance=0.0)
    harness.sample(power=3600.0, advance=120.0)

    # mean 1800 W over 120 s
    assert harness.wh == pytest.approx(60.0)


def test_actual_elapsed_time_is_used_not_the_nominal_interval() -> None:
    """Measured latency varies from 0.34 s to 9.5 s, so intervals really drift."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(power=3600.0, advance=45.0)

    assert harness.wh == pytest.approx(45.0)


def test_standby_period_contributes_zero_energy() -> None:
    """AC_Power vanishes at night rather than reporting zero."""
    harness = Harness()
    harness.sample(state="Active", power=500.0, advance=0.0)
    before = harness.wh

    # Ten hours of night at the real poll interval, AC_Power absent throughout.
    for _ in range(1200):
        harness.sample(state="Standby", power=None, advance=30.0)

    assert harness.wh == pytest.approx(before, abs=5.0)
    assert harness.wh < before + 5.0


def test_standby_does_not_hold_the_last_daylight_value() -> None:
    """Holding 500 W across a night would inject about 4 kWh."""
    harness = Harness()
    harness.sample(state="Active", power=500.0, advance=0.0)
    for _ in range(1200):
        harness.sample(state="Standby", power=None, advance=30.0)

    assert harness.kwh < 0.01


def test_long_gap_does_not_invent_energy() -> None:
    """A restart after daylight hours must not trapezoid across the gap."""
    harness = Harness()
    harness.sample(power=3000.0, advance=0.0)
    harness.sample(power=3000.0, advance=6 * 3600.0)

    assert harness.wh == 0.0


def test_gap_exactly_at_the_cap_is_still_integrated() -> None:
    """Gap exactly at the cap is still integrated."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(power=3600.0, advance=MAX_INTEGRATION_GAP)

    assert harness.wh == pytest.approx(3600.0 * MAX_INTEGRATION_GAP / 3600.0)


def test_gap_just_over_the_cap_is_skipped() -> None:
    """Gap just over the cap is skipped."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(power=3600.0, advance=MAX_INTEGRATION_GAP + 1)

    assert harness.wh == 0.0


def test_integration_resumes_after_a_skipped_gap() -> None:
    """The gap is dropped, not the whole sensor."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(power=3600.0, advance=10 * 3600.0)
    harness.sample(power=3600.0, advance=60.0)

    assert harness.wh == pytest.approx(60.0)


def test_unknown_state_without_power_breaks_the_chain() -> None:
    """An unrecognised state must never contribute energy."""
    harness = Harness()
    harness.sample(power=1000.0, advance=0.0)
    harness.sample(state="GridMonitoring", power=None, advance=30.0)
    harness.sample(state="GridMonitoring", power=None, advance=30.0)

    assert harness.wh == 0.0


def test_chain_break_does_not_bridge_across_the_unknown_period() -> None:
    """After a break, the next interval starts fresh instead of spanning it."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(state="GridMonitoring", power=None, advance=3600.0)
    harness.sample(power=3600.0, advance=30.0)
    harness.sample(power=3600.0, advance=30.0)

    assert harness.wh == pytest.approx(30.0)


def test_failed_poll_does_not_integrate() -> None:
    """A coordinator failure is not a power reading."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.fail(advance=3600.0)

    assert harness.wh == 0.0


def test_repeated_notification_for_the_same_poll_is_ignored() -> None:
    """Listeners are notified again while the device is unreachable."""
    harness = Harness()
    harness.sample(power=3600.0, advance=0.0)
    harness.sample(power=3600.0, advance=3600.0)
    expected = harness.wh

    for _ in range(5):
        harness.sensor._integrate()

    assert harness.wh == pytest.approx(expected)


def test_value_is_monotonically_increasing() -> None:
    """The sensor is TOTAL_INCREASING, so it must never step backwards."""
    harness = Harness()
    harness.sample(power=0.0, advance=0.0)
    previous = harness.kwh

    for power in (100.0, 0.0, 2500.0, None, 0.0, 900.0):
        harness.sample(
            state="Standby" if power is None else "Active", power=power, advance=30.0
        )
        assert harness.kwh >= previous
        previous = harness.kwh


class RestoringHarness(Harness):
    """Harness that can replay a Home Assistant restart."""

    async def restart(self, restored_kwh: float | None) -> Harness:
        """Return a fresh sensor that restored the given value."""
        fresh = Harness()
        fresh.clock = self.clock

        class _Restored:
            native_value: Any = restored_kwh

        async def _get_last_sensor_data() -> Any:
            return None if restored_kwh is None else _Restored()

        fresh.sensor.async_get_last_sensor_data = _get_last_sensor_data  # type: ignore[method-assign]
        return fresh


async def test_accumulator_is_restored_but_the_chain_is_not() -> None:
    """Restoring the last (time, power) pair would integrate over the downtime."""
    harness = RestoringHarness()
    harness.sample(power=3000.0, advance=0.0)
    fresh = await harness.restart(restored_kwh=12.5)

    last = await fresh.sensor.async_get_last_sensor_data()
    fresh.sensor._wh = float(last.native_value) * 1000.0
    assert fresh.sensor._last_sample is None

    # Home Assistant was down for six hours; the first sample after the restart
    # must start a new interval rather than spanning the downtime.
    fresh.sample(power=3000.0, advance=6 * 3600.0)
    fresh.sample(power=3000.0, advance=30.0)

    assert fresh.kwh == pytest.approx(12.5 + 3000.0 * 30.0 / 3600.0 / 1000.0)


def test_restored_value_of_zero_is_harmless() -> None:
    """Restored value of zero is harmless."""
    harness = Harness()
    harness.sensor._wh = 0.0
    harness.sample(power=0.0, advance=0.0)

    assert harness.kwh == 0.0


def test_night_then_dawn_produces_only_daylight_energy() -> None:
    """End-to-end shape of a real day boundary."""
    harness = Harness()

    # Dusk: a final generating sample, then the measurement set shrinks.
    harness.sample(state="Active", power=120.0, advance=0.0)
    for _ in range(20):
        harness.sample(state="Standby", power=None, advance=30.0)

    # The inverter goes fully offline for eight hours.
    for _ in range(4):
        harness.fail(advance=2 * 3600.0)

    dusk_total = harness.wh

    # Dawn: the set grows back and generation resumes at 600 W for one hour.
    harness.sample(state="Active", power=600.0, advance=30.0)
    for _ in range(120):
        harness.sample(state="Active", power=600.0, advance=30.0)

    assert harness.wh - dusk_total == pytest.approx(600.0, rel=1e-6)
    assert dusk_total == pytest.approx(0.5, abs=0.6)


def test_timedelta_sanity() -> None:
    """Guard against the cap being changed to an implausible value."""
    assert timedelta(seconds=MAX_INTEGRATION_GAP) <= timedelta(minutes=10)


def test_elapsed_time_is_wall_clock_not_monotonic() -> None:
    """A stall must be integrated over its real duration, not the poll interval."""
    harness = Harness()
    harness.sample(power=270.0, advance=0.0)
    harness.sample(power=270.0, advance=123.0)

    assert harness.wh == pytest.approx(270.0 * 123.0 / 3600.0)
    assert harness.wh == pytest.approx(9.225, abs=0.001)


def test_a_stall_shorter_than_the_cap_is_integrated_in_full() -> None:
    """Real stalls of tens of seconds are normal and must be accounted for."""
    harness = Harness()
    harness.sample(power=300.0, advance=0.0)
    for stall in (26.0, 46.0, 65.0, 123.0, 70.0):
        harness.sample(power=300.0, advance=stall)

    assert harness.wh == pytest.approx(300.0 * 330.0 / 3600.0)


def test_clock_stepping_backwards_does_not_subtract_energy() -> None:
    """Wall clock can be corrected backwards by NTP; that is not negative energy."""
    harness = Harness()
    harness.sample(power=1000.0, advance=0.0)
    harness.sample(power=1000.0, advance=60.0)
    accumulated = harness.wh

    harness.sample(power=1000.0, advance=-30.0)

    assert harness.wh == pytest.approx(accumulated)


def test_clock_jumping_forward_beyond_the_cap_is_skipped() -> None:
    """A large NTP correction must not be mistaken for hours of production."""
    harness = Harness()
    harness.sample(power=3000.0, advance=0.0)
    harness.sample(power=3000.0, advance=7200.0)

    assert harness.wh == 0.0
