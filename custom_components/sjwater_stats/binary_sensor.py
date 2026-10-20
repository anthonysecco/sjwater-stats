"""Leak and unusual-use binary sensors for San Jose Water."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CONTINUOUS_HOURS,
    CONF_HIGH_DAY_PERCENT,
    CONF_HIGH_HOUR_GALLONS,
    CONF_OVERNIGHT_END,
    CONF_OVERNIGHT_START,
    CONF_STALE_HOURS,
    DEFAULT_OPTIONS,
    SJW_TZ,
)
from .coordinator import SJWaterConfigEntry, WaterData
from .entity import SJWaterEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SJWaterBinaryDescription(BinarySensorEntityDescription):
    """Describes one signal."""

    is_on_fn: Callable[[WaterData], bool | None]
    attrs_fn: Callable[[WaterData, dict[str, Any]], dict[str, Any]]


def _hour(d: Any) -> str | None:
    return d.start.astimezone(SJW_TZ).isoformat() if d else None


BINARY_SENSORS: tuple[SJWaterBinaryDescription, ...] = (
    SJWaterBinaryDescription(
        key="possible_leak",
        translation_key="possible_leak",
        device_class=BinarySensorDeviceClass.MOISTURE,
        is_on_fn=lambda d: d.possible_leak,
        attrs_fn=lambda d, o: {
            "reasons": d.leak_reasons,
            "portal_leak_hour": _hour(d.last_portal_leak),
            "overnight_minimum_gallons": d.overnight_minimum,
            "overnight_window": f"{int(o[CONF_OVERNIGHT_START]):02d}:00-{int(o[CONF_OVERNIGHT_END]):02d}:00",
            "longest_continuous_hours": d.longest_run_hours,
            "continuous_hours_threshold": o[CONF_CONTINUOUS_HOURS],
        },
    ),
    SJWaterBinaryDescription(
        key="high_daily_usage",
        translation_key="high_daily_usage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: d.high_daily_usage,
        attrs_fn=lambda d, o: {
            "yesterday_gallons": d.yesterday.gallons,
            "daily_average_gallons": d.daily_average,
            "percent_of_average": d.yesterday_vs_average,
            "threshold_percent": o[CONF_HIGH_DAY_PERCENT],
        },
    ),
    SJWaterBinaryDescription(
        key="high_hourly_usage",
        translation_key="high_hourly_usage",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: d.high_hourly_usage,
        attrs_fn=lambda d, o: {
            "peak_hour": _hour(d.peak_hour),
            "peak_gallons": d.peak_hour.gallons if d.peak_hour else None,
            "threshold_gallons": o[CONF_HIGH_HOUR_GALLONS],
        },
    ),
    SJWaterBinaryDescription(
        key="data_delayed",
        translation_key="data_delayed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: d.data_stale,
        attrs_fn=lambda d, o: {"age_hours": d.data_age_hours, "threshold_hours": o[CONF_STALE_HOURS]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SJWaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SJWaterBinarySensor(coordinator, description) for description in BINARY_SENSORS)


class SJWaterBinarySensor(SJWaterEntity, BinarySensorEntity):
    """A leak or unusual-use signal."""

    entity_description: SJWaterBinaryDescription

    @property
    def is_on(self) -> bool | None:
        """Signal state."""
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Inputs and thresholds behind the signal."""
        options = {**DEFAULT_OPTIONS, **self.coordinator.config_entry.options}
        return self.entity_description.attrs_fn(self.coordinator.data, options)
