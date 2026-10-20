"""Leak and data-freshness binary sensors for San Jose Water."""

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

from .const import CONF_STALE_HOURS, DEFAULT_OPTIONS, PORTAL_LEAK_WINDOW_HOURS, SJW_TZ
from .coordinator import SJWaterConfigEntry, WaterData
from .entity import SJWaterEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SJWaterBinaryDescription(BinarySensorEntityDescription):
    """Describes one signal."""

    is_on_fn: Callable[[WaterData], bool | None]
    attrs_fn: Callable[[WaterData, dict[str, Any]], dict[str, Any]]


BINARY_SENSORS: tuple[SJWaterBinaryDescription, ...] = (
    SJWaterBinaryDescription(
        key="leak_alert",
        translation_key="leak_alert",
        device_class=BinarySensorDeviceClass.MOISTURE,
        # San Jose Water's own leak flag (the "Possible Leak" series in the hourly data).
        is_on_fn=lambda d: d.possible_leak,
        attrs_fn=lambda d, o: {
            "portal_leak_hour": d.last_portal_leak.start.astimezone(SJW_TZ).isoformat() if d.last_portal_leak else None,
            "leak_hours_last_7_days": d.leak_hours_7_days,
            "window_hours": PORTAL_LEAK_WINDOW_HOURS,
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

BINARY_SENSOR_KEYS = frozenset(d.key for d in BINARY_SENSORS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SJWaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SJWaterBinarySensor(coordinator, description) for description in BINARY_SENSORS)


class SJWaterBinarySensor(SJWaterEntity, BinarySensorEntity):
    """A leak or data-freshness signal."""

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
