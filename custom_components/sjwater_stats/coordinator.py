"""Polling coordinator: hourly use, San Jose Water's leak flag and billing periods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AccountLocked,
    BillingPeriod,
    HourReading,
    InvalidAuth,
    SJWaterClient,
    SJWaterError,
    Usage,
    VerificationRequired,
)
from .billing import BillingData, derive_billing
from .const import (
    CONF_ACCOUNT,
    CONF_SCAN_INTERVAL,
    CONF_STALE_HOURS,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOGGER,
    PORTAL_LEAK_WINDOW_HOURS,
    SJW_TZ,
    scan_interval,
)
from .statistics import async_import_cost, async_import_usage, cost_statistic_id_for, statistic_id_for

type SJWaterConfigEntry = ConfigEntry[SJWaterCoordinator]


@dataclass(slots=True)
class DayTotal:
    """Gallons used on one Pacific calendar day."""

    day: date
    gallons: float | None
    complete: bool


@dataclass(slots=True)
class WaterData:
    """Everything the entities read, refreshed together."""

    usage: Usage
    data_through: datetime | None  # end of the last published hour, Pacific
    yesterday: DayTotal
    last_7_days: float | None
    last_30_days: float | None
    daily_average: float | None
    days_in_average: int
    last_portal_leak: HourReading | None
    leak_hours_7_days: int
    data_age_hours: float | None
    billing: BillingData | None
    possible_leak: bool = False
    data_stale: bool = False


def _window_sum(totals: dict[date, float], end: date, days: int, first: date) -> tuple[float | None, int]:
    """Sum of `days` complete days ending at `end`, counting only days since the first reading."""
    span = [end - timedelta(days=i) for i in range(days)]
    span = [d for d in span if d >= first]
    if not span:
        return None, 0
    return sum(totals.get(d, 0.0) for d in span), len(span)


def derive(
    usage: Usage, options: dict[str, Any], now: datetime, periods: list[BillingPeriod] | None = None
) -> WaterData:
    """Compute every sensor value from the published hours and billing periods."""
    opts = {**DEFAULT_OPTIONS, **options}
    hours = [h for h in usage.hours if not h.unavailable]
    totals = usage.daily_totals()
    yesterday = now.astimezone(SJW_TZ).date() - timedelta(days=1)
    first_day = usage.hours[0].local_start.date() if usage.hours else yesterday + timedelta(days=1)
    last_end = (hours[-1].start + timedelta(hours=1)) if hours else None
    last_end_local = last_end.astimezone(SJW_TZ) if last_end else None

    next_midnight = datetime.combine(yesterday + timedelta(days=1), datetime.min.time(), SJW_TZ)
    y = DayTotal(
        yesterday,
        round(totals.get(yesterday, 0.0), 1) if yesterday >= first_day else None,
        bool(last_end_local and last_end_local >= next_midnight),
    )
    last_7, _ = _window_sum(totals, yesterday, 7, first_day)
    last_30, n_days = _window_sum(totals, yesterday, 30, first_day)
    average = round(last_30 / n_days, 1) if last_30 is not None and n_days else None

    leaks = [h for h in usage.hours if h.possible_leak]
    last_leak = leaks[-1] if leaks else None
    recent_leaks = sum(1 for h in leaks if last_end and h.start >= last_end - timedelta(days=7))
    age = round((now - usage.last_updated).total_seconds() / 3600, 1) if usage.last_updated else None

    data = WaterData(
        usage=usage,
        data_through=last_end_local,
        yesterday=y,
        last_7_days=round(last_7, 1) if last_7 is not None else None,
        last_30_days=round(last_30, 1) if last_30 is not None else None,
        daily_average=average,
        days_in_average=n_days,
        last_portal_leak=last_leak,
        leak_hours_7_days=recent_leaks,
        data_age_hours=age,
        billing=derive_billing(periods, usage.hours, last_end) if periods else None,
    )
    data.possible_leak = bool(
        last_leak and last_end and last_leak.start >= last_end - timedelta(hours=PORTAL_LEAK_WINDOW_HOURS)
    )
    data.data_stale = age is not None and age >= float(opts[CONF_STALE_HOURS])
    return data


class SJWaterCoordinator(DataUpdateCoordinator[WaterData]):
    """Signs in, fetches hourly use and billing, imports statistics and derives the sensors."""

    config_entry: SJWaterConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SJWaterConfigEntry) -> None:
        """Set up from the entry."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_OPTIONS[CONF_SCAN_INTERVAL])),
        )
        # Own cookie jar: the portal session must not mix with other integrations' cookies.
        session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self.client = SJWaterClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
        self.account: str = entry.data[CONF_ACCOUNT]
        self.statistic_id = statistic_id_for(self.account)
        self.cost_statistic_id = cost_statistic_id_for(self.account)

    async def _async_update_data(self) -> WaterData:
        try:
            _, usage = await self.client.fetch_usage(self.account)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (VerificationRequired, AccountLocked) as err:
            raise UpdateFailed(f"{err}; sign in at sjwaterhub.com in a browser") from err
        except SJWaterError as err:
            raise UpdateFailed(str(err)) from err

        # Billing changes every two months; a failure here keeps the last periods and the hourly data.
        periods: list[BillingPeriod] | None
        try:
            periods = await self.client.fetch_billing()
        except SJWaterError as err:
            LOGGER.warning("San Jose Water billing history unavailable, keeping the last one: %s", err)
            periods = self.data.billing.periods if self.data and self.data.billing else None

        await async_import_usage(self.hass, self.statistic_id, self.account, usage.hours)
        if periods:
            await async_import_cost(self.hass, self.cost_statistic_id, self.account, usage.hours, periods)
        return derive(usage, dict(self.config_entry.options), dt_util.now(), periods)
