"""Polling coordinator and the conservation / leak signals derived from hourly use."""

from __future__ import annotations

from dataclasses import dataclass, field
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

from .api import AccountLocked, HourReading, InvalidAuth, SJWaterClient, SJWaterError, Usage, VerificationRequired
from .const import (
    CONF_ACCOUNT,
    CONF_CONTINUOUS_HOURS,
    CONF_HIGH_DAY_PERCENT,
    CONF_HIGH_HOUR_GALLONS,
    CONF_OVERNIGHT_END,
    CONF_OVERNIGHT_START,
    CONF_SCAN_INTERVAL,
    CONF_STALE_HOURS,
    DEFAULT_OPTIONS,
    DOMAIN,
    HIGH_DAY_MIN_GALLONS,
    LOGGER,
    PORTAL_LEAK_WINDOW_HOURS,
    SJW_TZ,
    scan_interval,
)
from .statistics import async_import_usage, statistic_id_for

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
    last_hour: HourReading | None
    today: DayTotal
    yesterday: DayTotal
    last_7_days: float | None
    last_30_days: float | None
    daily_average: float | None
    days_in_average: int
    yesterday_vs_average: float | None
    peak_hour: HourReading | None  # largest hour in the last 24 published hours
    overnight_minimum: float | None
    overnight_window: tuple[datetime, datetime] | None
    longest_run_hours: int
    longest_run_start: datetime | None
    last_portal_leak: HourReading | None
    data_age_hours: float | None
    leak_reasons: list[str] = field(default_factory=list)
    possible_leak: bool = False
    high_daily_usage: bool | None = None
    high_hourly_usage: bool | None = None
    data_stale: bool = False


def _window_sum(totals: dict[date, float], end: date, days: int, first: date) -> tuple[float | None, int]:
    """Sum of `days` complete days ending at `end`, counting only days since the first reading."""
    span = [end - timedelta(days=i) for i in range(days)]
    span = [d for d in span if d >= first]
    if not span:
        return None, 0
    return sum(totals.get(d, 0.0) for d in span), len(span)


def overnight_window(last_end: datetime, start_hour: int, end_hour: int) -> tuple[datetime, datetime] | None:
    """The most recent overnight window (Pacific) that is fully published by `last_end`."""
    last_end = last_end.astimezone(SJW_TZ)
    if start_hour == end_hour:
        return None
    for back in range(3):
        day = last_end.date() - timedelta(days=back)
        end = datetime.combine(day, datetime.min.time(), SJW_TZ).replace(hour=end_hour)
        start_day = day if start_hour < end_hour else day - timedelta(days=1)
        start = datetime.combine(start_day, datetime.min.time(), SJW_TZ).replace(hour=start_hour)
        if end <= last_end:
            return start, end
    return None


def derive(usage: Usage, options: dict[str, Any], now: datetime) -> WaterData:
    """Compute every sensor value from the published hours."""
    opts = {**DEFAULT_OPTIONS, **options}
    hours = [h for h in usage.hours if not h.unavailable]
    totals = usage.daily_totals()
    now_local = now.astimezone(SJW_TZ)
    today, yesterday = now_local.date(), now_local.date() - timedelta(days=1)
    first_day = usage.hours[0].local_start.date() if usage.hours else today
    last_hour = hours[-1] if hours else None
    last_end = (last_hour.start + timedelta(hours=1)) if last_hour else None
    last_end_local = last_end.astimezone(SJW_TZ) if last_end else None

    def day_total(day: date) -> DayTotal:
        if day < first_day:
            return DayTotal(day, None, False)
        next_midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), SJW_TZ)
        complete = bool(last_end_local and last_end_local >= next_midnight)
        return DayTotal(day, round(totals.get(day, 0.0), 1), complete)

    last_7, _ = _window_sum(totals, yesterday, 7, first_day)
    last_30, n_days = _window_sum(totals, yesterday, 30, first_day)
    average = round(last_30 / n_days, 1) if last_30 is not None and n_days else None
    y = day_total(yesterday)
    vs_avg = round(y.gallons / average * 100, 1) if average and y.gallons is not None else None

    recent = [h for h in hours if last_end and h.start >= last_end - timedelta(hours=24)]
    peak = max(recent, key=lambda h: h.gallons) if recent else None

    # Longest run of consecutive hours with any use, within the last 24 published hours.
    run = best = 0
    run_start = best_start = None
    previous: HourReading | None = None
    for h in recent:
        contiguous = previous is not None and h.start - previous.start == timedelta(hours=1)
        if h.gallons > 0:
            run = run + 1 if contiguous and run else 1
            run_start = run_start if contiguous and run > 1 else h.start
            if run > best:
                best, best_start = run, run_start
        else:
            run, run_start = 0, None
        previous = h

    start_hour, end_hour = int(opts[CONF_OVERNIGHT_START]), int(opts[CONF_OVERNIGHT_END])
    window = overnight_window(last_end, start_hour, end_hour) if last_end else None
    overnight_min = None
    if window:
        night = [h for h in usage.hours if window[0] <= h.start < window[1]]
        expected = round((window[1] - window[0]) / timedelta(hours=1))
        if night and len(night) >= expected and not any(h.unavailable for h in night):
            overnight_min = min(h.gallons for h in night)

    leaks = [h for h in usage.hours if h.possible_leak]
    last_leak = leaks[-1] if leaks else None
    age = round((now - usage.last_updated).total_seconds() / 3600, 1) if usage.last_updated else None

    data = WaterData(
        usage=usage,
        last_hour=last_hour,
        today=day_total(today),
        yesterday=y,
        last_7_days=round(last_7, 1) if last_7 is not None else None,
        last_30_days=round(last_30, 1) if last_30 is not None else None,
        daily_average=average,
        days_in_average=n_days,
        yesterday_vs_average=vs_avg,
        peak_hour=peak,
        overnight_minimum=overnight_min,
        overnight_window=window,
        longest_run_hours=best,
        longest_run_start=best_start,
        last_portal_leak=last_leak,
        data_age_hours=age,
    )

    reasons = []
    if last_leak and last_end and last_leak.start >= last_end - timedelta(hours=PORTAL_LEAK_WINDOW_HOURS):
        reasons.append("portal_leak_flag")
    if overnight_min is not None and overnight_min > 0:
        reasons.append("overnight_flow")
    if best >= int(opts[CONF_CONTINUOUS_HOURS]):
        reasons.append("continuous_use")
    data.leak_reasons = reasons
    data.possible_leak = bool(reasons)
    if y.complete and average and y.gallons is not None:
        data.high_daily_usage = (
            y.gallons >= average * float(opts[CONF_HIGH_DAY_PERCENT]) / 100 and y.gallons >= HIGH_DAY_MIN_GALLONS
        )
    elif y.complete and y.gallons is not None:
        data.high_daily_usage = False
    if peak is not None:
        data.high_hourly_usage = peak.gallons >= float(opts[CONF_HIGH_HOUR_GALLONS])
    data.data_stale = age is not None and age >= float(opts[CONF_STALE_HOURS])
    return data


class SJWaterCoordinator(DataUpdateCoordinator[WaterData]):
    """Signs in, fetches hourly use, imports statistics and derives the signals."""

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

    async def _async_update_data(self) -> WaterData:
        try:
            _, usage = await self.client.fetch_usage(self.account)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (VerificationRequired, AccountLocked) as err:
            raise UpdateFailed(f"{err}; sign in at sjwaterhub.com in a browser") from err
        except SJWaterError as err:
            raise UpdateFailed(str(err)) from err

        await async_import_usage(self.hass, self.statistic_id, self.account, usage.hours)
        return derive(usage, dict(self.config_entry.options), dt_util.now())
