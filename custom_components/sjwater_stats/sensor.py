"""Sensors for San Jose Water.

Volume sensors have no state_class on purpose: the portal is hours behind and bills weeks after the
fact, so recorder statistics built from these states would land in the wrong hours. Use the imported
`sjwater_stats:` statistics for the Energy dashboard; these sensors are for cards and automations.
Only day-or-longer measures: the portal is too far behind for hour-level ones.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BillingPeriod
from .coordinator import SJWaterConfigEntry, SJWaterCoordinator, WaterData
from .entity import SJWaterEntity

PARALLEL_UPDATES = 0

StateType = float | int | str | datetime | None


@dataclass(frozen=True, kw_only=True)
class SJWaterSensorDescription(SensorEntityDescription):
    """Describes one San Jose Water sensor."""

    value_fn: Callable[[WaterData], StateType]
    attrs_fn: Callable[[WaterData], dict[str, Any]] | None = None


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


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


def _period_attrs(period: BillingPeriod | None) -> dict[str, Any]:
    if period is None:
        return {}
    return {
        "period_start": _iso(period.start),
        "period_end": _iso(period.end),
        "days": period.read.days,
        "ccf": period.read.ccf,
        "bill_date": _iso(period.bill.bill_date) if period.bill else None,
        "bill_amount": period.bill.amount if period.bill else None,
    }


def _last(d: WaterData) -> BillingPeriod | None:
    return d.billing.last_period if d.billing else None


def _last_year(d: WaterData) -> BillingPeriod | None:
    return d.billing.last_year_period if d.billing else None


def _per_day(period: BillingPeriod | None) -> float | None:
    return round(period.read.gallons / period.read.days, 1) if period and period.read.days else None


def _vs_year_ago_attrs(d: WaterData) -> dict[str, Any]:
    last, before = _last(d), _last_year(d)
    return {
        "period_start": _iso(last.start) if last else None,
        "period_end": _iso(last.end) if last else None,
        "gallons_per_day": _per_day(last),
        "year_ago_period_start": _iso(before.start) if before else None,
        "year_ago_period_end": _iso(before.end) if before else None,
        "year_ago_gallons_per_day": _per_day(before),
    }


def _cost(key: str, period_fn: Callable[[WaterData], BillingPeriod | None]) -> SJWaterSensorDescription:
    def value(d: WaterData) -> float | None:
        period = period_fn(d)
        return period.bill.amount if period and period.bill else None

    return SJWaterSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        suggested_display_precision=2,
        value_fn=value,
        attrs_fn=lambda d: _period_attrs(period_fn(d)),
    )


# Names follow "group, detail" so related sensors sort together: Account, Data, Last billing period, Leak, Usage.
SENSORS: tuple[SJWaterSensorDescription, ...] = (
    _gallons(
        "usage_yesterday",
        lambda d: d.yesterday.gallons,
        attrs_fn=lambda d: {
            "date": d.yesterday.day.isoformat(),
            "complete": d.yesterday.complete,
            "data_through": _iso(d.data_through),
        },
    ),
    _gallons("usage_last_7_days", lambda d: d.last_7_days),
    _gallons("usage_last_30_days", lambda d: d.last_30_days),
    _gallons("usage_average_daily", lambda d: d.daily_average, attrs_fn=lambda d: {"days": d.days_in_average}),
    _gallons(
        "usage_this_billing_period",
        lambda d: d.billing.period_to_date if d.billing else None,
        attrs_fn=lambda d: {
            "period_start": _iso(d.billing.period_start) if d.billing else None,
            "days": d.billing.period_days if d.billing else None,
            "data_through": _iso(d.data_through),
        },
    ),
    _gallons(
        "last_billing_period_usage",
        lambda d: p.read.gallons if (p := _last(d)) else None,
        attrs_fn=lambda d: _period_attrs(_last(d)),
    ),
    _gallons(
        "last_billing_period_usage_year_ago",
        lambda d: p.read.gallons if (p := _last_year(d)) else None,
        attrs_fn=lambda d: _period_attrs(_last_year(d)),
    ),
    SJWaterSensorDescription(
        key="last_billing_period_vs_year_ago",
        translation_key="last_billing_period_vs_year_ago",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: d.billing.vs_last_year if d.billing else None,
        attrs_fn=_vs_year_ago_attrs,
    ),
    _cost("last_billing_period_cost", _last),
    _cost("last_billing_period_cost_year_ago", _last_year),
    SJWaterSensorDescription(
        key="leak_alert_last",
        translation_key="leak_alert_last",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.last_portal_leak.start if d.last_portal_leak else None,
    ),
    SJWaterSensorDescription(
        key="data_updated",
        translation_key="data_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.usage.last_updated,
        attrs_fn=lambda d: {"data_through": _iso(d.data_through), "age_hours": d.data_age_hours},
    ),
)

ACCOUNT_NUMBER = SensorEntityDescription(
    key="account_number",
    translation_key="account_number",
    entity_category=EntityCategory.DIAGNOSTIC,
)

SENSOR_KEYS = frozenset({d.key for d in SENSORS} | {ACCOUNT_NUMBER.key})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SJWaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(SJWaterSensor(coordinator, description) for description in SENSORS),
            SJWaterAccountSensor(coordinator, ACCOUNT_NUMBER),
        ]
    )


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


class SJWaterAccountSensor(SJWaterEntity, SensorEntity):
    """The San Jose Water account number (diagnostic)."""

    def __init__(self, coordinator: SJWaterCoordinator, description: SensorEntityDescription) -> None:
        """Show the configured account."""
        super().__init__(coordinator, description)
        self._attr_native_value = coordinator.account
