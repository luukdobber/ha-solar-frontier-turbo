"""Sensor platform for the Solar Frontier Turbo integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import ATTR_DATETIME, LOGGER, MAX_INTEGRATION_GAP
from .coordinator import SolarFrontierCoordinator, SolarFrontierData
from .entity import SolarFrontierEntity

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo

    from . import SolarFrontierConfigEntry

PARALLEL_UPDATES = 0


class MissingPolicy(StrEnum):
    """What an absent measurement means for a given quantity."""

    # The inverter genuinely is not producing, so zero is the truth.
    ZERO_WHEN_IDLE = "zero_when_idle"
    # There is no reading; reporting a number would invent data.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, kw_only=True)
class MeasurementDescription(SensorEntityDescription):
    """Describes a sensor built from a `<Measurement>` element."""

    missing_policy: MissingPolicy = MissingPolicy.UNAVAILABLE


MEASUREMENT_DESCRIPTIONS: dict[str, MeasurementDescription] = {
    "AC_Power": MeasurementDescription(
        key="AC_Power",
        translation_key="ac_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        missing_policy=MissingPolicy.ZERO_WHEN_IDLE,
    ),
    "AC_Voltage": MeasurementDescription(
        key="AC_Voltage",
        translation_key="ac_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
    ),
    "AC_Current": MeasurementDescription(
        key="AC_Current",
        translation_key="ac_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        missing_policy=MissingPolicy.ZERO_WHEN_IDLE,
    ),
    "AC_Frequency": MeasurementDescription(
        key="AC_Frequency",
        translation_key="ac_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=2,
    ),
    "DC_Power": MeasurementDescription(
        key="DC_Power",
        translation_key="dc_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        missing_policy=MissingPolicy.ZERO_WHEN_IDLE,
    ),
    "DC_Voltage": MeasurementDescription(
        key="DC_Voltage",
        translation_key="dc_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
    ),
    "DC_Current": MeasurementDescription(
        key="DC_Current",
        translation_key="dc_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        missing_policy=MissingPolicy.ZERO_WHEN_IDLE,
    ),
    "Temp": MeasurementDescription(
        key="Temp",
        # Unnamed: the temperature device class supplies the name.
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
}


@dataclass(frozen=True, kw_only=True)
class SolarFrontierSensorDescription(SensorEntityDescription):
    """Describes a sensor derived from the `/all.xml` poll."""

    value_fn: Callable[[SolarFrontierData], StateType]
    attributes_fn: Callable[[SolarFrontierData], dict[str, Any]] | None = None


def _last_event_attributes(data: SolarFrontierData) -> dict[str, Any]:
    events = data.snapshot.events
    if not events:
        return {}
    newest = events[0]
    return {
        "severity": newest.severity,
        "memory": newest.memory,
        "start": newest.start,
        "end": newest.end,
        "oldest_event_start": events[-1].start,
    }


SENSORS: tuple[SolarFrontierSensorDescription, ...] = (
    SolarFrontierSensorDescription(
        key="operating_state",
        translation_key="operating_state",
        # Not an enum device class: the full value set is unknown.
        value_fn=lambda data: data.state,
    ),
    SolarFrontierSensorDescription(
        key="last_event",
        translation_key="last_event",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.snapshot.events[0].message if data.snapshot.events else None
        ),
        attributes_fn=_last_event_attributes,
    ),
    SolarFrontierSensorDescription(
        key="inverter_clock",
        translation_key="inverter_clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # No timezone and no DST handling, so exposed as text.
        value_fn=lambda data: data.snapshot.device.get(ATTR_DATETIME),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarFrontierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data

    entities: list[SensorEntity] = [
        SolarFrontierSensor(
            data.coordinator, data.serial, data.device_info, description
        )
        for description in SENSORS
    ]
    entities.append(
        SolarFrontierEnergySensor(data.coordinator, data.serial, data.device_info)
    )

    entities += [
        SolarFrontierMeasurementSensor(
            data.coordinator, data.serial, data.device_info, description
        )
        for description in MEASUREMENT_DESCRIPTIONS.values()
    ]
    async_add_entities(entities)

    _log_unsupported_measurements(entry, data.coordinator)


@callback
def _log_unsupported_measurements(
    entry: SolarFrontierConfigEntry, coordinator: SolarFrontierCoordinator
) -> None:
    """Leave a trace when a model reports a type there is no sensor for."""
    logged: set[str] = set()

    @callback
    def _check() -> None:
        if (poll := coordinator.data) is None:
            return
        unsupported = (
            poll.measurements.keys() - MEASUREMENT_DESCRIPTIONS.keys() - logged
        )
        if not unsupported:
            return
        logged.update(unsupported)
        LOGGER.debug(
            "Inverter reports measurement type(s) with no sensor: %s",
            sorted(unsupported),
        )

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))


class SolarFrontierMeasurementSensor(SolarFrontierEntity, SensorEntity):
    """A sensor for one `<Measurement>` element."""

    entity_description: MeasurementDescription

    def __init__(
        self,
        coordinator: SolarFrontierCoordinator,
        serial: str,
        device_info: DeviceInfo,
        description: MeasurementDescription,
    ) -> None:
        """Initialise the measurement sensor."""
        super().__init__(coordinator, serial, device_info)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """The measured value, or the policy's answer when it is absent."""
        if (poll := self.poll) is None:
            return None
        if (
            measurement := poll.measurements.get(self.entity_description.key)
        ) is not None:
            return measurement.value
        if self.entity_description.missing_policy is MissingPolicy.ZERO_WHEN_IDLE:
            # An unrecognised state may be a read anomaly, not genuine zero output.
            return 0.0 if poll.is_producing_state is False else None
        return None

    @property
    def available(self) -> bool:
        """Unavailable when the measurement is absent and zero would be a lie."""
        return super().available and self.native_value is not None


class SolarFrontierSensor(SolarFrontierEntity, SensorEntity):
    """A sensor derived from the `/all.xml` poll."""

    entity_description: SolarFrontierSensorDescription

    def __init__(
        self,
        coordinator: SolarFrontierCoordinator,
        serial: str,
        device_info: DeviceInfo,
        description: SolarFrontierSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, serial, device_info)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """The described value."""
        if (poll := self.poll) is None:
            return None
        return self.entity_description.value_fn(poll)

    @property
    def available(self) -> bool:
        """Unavailable rather than unknown when there is no value to report."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra detail for the described value."""
        if (poll := self.poll) is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(poll)


class SolarFrontierEnergySensor(SolarFrontierEntity, RestoreSensor):
    """Energy produced, integrated locally from AC power."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_translation_key = "energy_total"

    def __init__(
        self,
        coordinator: SolarFrontierCoordinator,
        serial: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialise the energy sensor."""
        super().__init__(coordinator, serial, device_info)
        self._attr_unique_id = f"{serial}_energy_total"
        self._wh: float = 0.0
        self._last_sample: tuple[datetime, float] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the accumulator, but not the integration chain."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                restored = float(last.native_value)  # type: ignore[arg-type]
            except TypeError, ValueError:
                restored = 0.0
            if restored > 0:
                self._wh = restored * 1000.0
                LOGGER.debug("Restored energy accumulator at %.3f kWh", restored)
        # _last_sample is deliberately not restored, so the next sample starts a
        # new interval rather than integrating across the downtime.
        self._integrate()

    @property
    def available(self) -> bool:
        """Available once an accumulator value exists, even if the device is down."""
        return True

    @property
    def native_value(self) -> float:
        """Accumulated energy in kWh."""
        return self._wh / 1000.0

    @callback
    def _handle_coordinator_update(self) -> None:
        self._integrate()
        super()._handle_coordinator_update()

    @callback
    def _integrate(self) -> None:
        """Trapezoidally integrate AC power into the Wh accumulator."""
        poll = self.poll
        if poll is None or not self.coordinator.last_update_success:
            return

        power = poll.ac_power_w
        timestamp = poll.received

        if self._last_sample is not None and self._last_sample[0] == timestamp:
            # Same poll re-notified without new data.
            return

        if power is None:
            # Unknown power breaks the chain rather than contributing zero.
            self._last_sample = None
            return

        if self._last_sample is not None:
            previous_time, previous_power = self._last_sample
            # A negative gap means the clock stepped backwards; skip rather than
            # subtract energy.
            gap = (timestamp - previous_time).total_seconds()
            if 0 < gap <= MAX_INTEGRATION_GAP:
                self._wh += 0.5 * (previous_power + power) * (gap / 3600.0)
            elif gap > MAX_INTEGRATION_GAP:
                LOGGER.debug(
                    "Skipped a %.0f s gap in AC power (cap %.0f s); no energy "
                    "invented across it",
                    gap,
                    MAX_INTEGRATION_GAP,
                )

        self._last_sample = (timestamp, power)
