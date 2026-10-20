"""Sensors for San Jose Water.

Volume sensors have no state_class on purpose: the portal is hours behind, so recorder statistics
built from these states would land in the wrong hours. Use the imported `sjwater_stats:` statistic
for the Energy dashboard; these sensors are for cards, alerts and automations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime, UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SJW_TZ
from .coordinator import DayTotal, SJWaterConfigEntry, WaterData
from .entity import SJWaterEntity

PARALLEL_UPDATES = 0

StateType = float | int | datetime | None


@dataclass(frozen=True, kw_only=True)
class SJWaterSensorDescription(SensorEntityDescription):
    """Describes one San Jose Water sensor."""

    value_fn: Callable[[WaterData], StateType]
    attrs_fn: Callable[[WaterData], dict[str, Any]] | None = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _day_attrs(fn: Callable[[WaterData], DayTotal]) -> Callable[[WaterData], dict[str, Any]]:
    def attrs(d: WaterData) -> dict[str, Any]:
        day = fn(d)
        return {"date": day.day.isoformat(), "complete": day.complete, "data_through": _iso(_data_through(d))}

    return attrs


def _data_through(d: WaterData) -> datetime | None:
    return (d.last_hour.local_start + timedelta(hours=1)) if d.last_hour else None


def _gallons(key: str, value_fn: Callable[[WaterData], float | None], **kwargs: Any) -> SJWaterSensorDescription:
    return SJWaterSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        suggested_display_precision=0,
        value_fn=value_fn,
        **kwargs,
    )


def _hour_attrs(fn: Callable[[WaterData], Any]) -> Callable[[WaterData], dict[str, Any]]:
    def attrs(d: WaterData) -> dict[str, Any]:
        h = fn(d)
        return {"hour_start": _iso(h.local_start) if h else None}

    return attrs


def _flow(d: WaterData) -> float | None:
    return round(d.last_hour.gallons / 60, 2) if d.last_hour else None


def _overnight_attrs(d: WaterData) -> dict[str, Any]:
    w = d.overnight_window
    return {"window_start": _iso(w[0]) if w else None, "window_end": _iso(w[1]) if w else None}


SENSORS: tuple[SJWaterSensorDescription, ...] = (
    _gallons("usage_today", lambda d: d.today.gallons, attrs_fn=_day_attrs(lambda d: d.today)),
    _gallons("usage_yesterday", lambda d: d.yesterday.gallons, attrs_fn=_day_attrs(lambda d: d.yesterday)),
    _gallons("usage_last_7_days", lambda d: d.last_7_days),
    _gallons("usage_last_30_days", lambda d: d.last_30_days),
    _gallons(
        "average_daily_usage",
        lambda d: d.daily_average,
        attrs_fn=lambda d: {"days": d.days_in_average},
    ),
    SJWaterSensorDescription(
        key="yesterday_vs_average",
        translation_key="yesterday_vs_average",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.yesterday_vs_average,
    ),
    _gallons(
        "last_hour_usage",
        lambda d: d.last_hour.gallons if d.last_hour else None,
        attrs_fn=_hour_attrs(lambda d: d.last_hour),
    ),
    SJWaterSensorDescription(
        key="flow_rate",
        translation_key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_flow,
        attrs_fn=_hour_attrs(lambda d: d.last_hour),
    ),
    _gallons(
        "peak_hour_usage",
        lambda d: d.peak_hour.gallons if d.peak_hour else None,
        attrs_fn=_hour_attrs(lambda d: d.peak_hour),
    ),
    _gallons("overnight_minimum", lambda d: d.overnight_minimum, attrs_fn=_overnight_attrs),
    SJWaterSensorDescription(
        key="continuous_use",
        translation_key="continuous_use",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.longest_run_hours,
        attrs_fn=lambda d: {"run_start": _iso(d.longest_run_start.astimezone(SJW_TZ) if d.longest_run_start else None)},
    ),
    SJWaterSensorDescription(
        key="last_leak_alert",
        translation_key="last_leak_alert",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.last_portal_leak.start if d.last_portal_leak else None,
    ),
    SJWaterSensorDescription(
        key="data_updated",
        translation_key="data_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.usage.last_updated,
        attrs_fn=lambda d: {"data_through": _iso(_data_through(d)), "age_hours": d.data_age_hours},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SJWaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SJWaterSensor(coordinator, description) for description in SENSORS)


class SJWaterSensor(SJWaterEntity, SensorEntity):
    """A San Jose Water value."""

    entity_description: SJWaterSensorDescription

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Context for cards and automations."""
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
