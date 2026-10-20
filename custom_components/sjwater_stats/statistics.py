"""Hourly water use and cost as external statistics for the Energy dashboard.

Why external statistics and not a sensor's own statistics: the portal publishes hours 4-6 h late
and bills weeks after the fact, so a sensor's state changes would be booked to the wrong hours. And
writing rows into a statistic the recorder also compiles from a sensor collides with its own rows.
Separate `sjwater_stats:` statistics avoid both.

Water: only published, complete hours are imported (never the zero-padded future hours); the last
REIMPORT_HOURS are rewritten on every update in case the portal revised them.
Cost: each bill spread over its period's hours (see billing.allocate_costs); hours after the last
bill have no cost yet. The last COST_REIMPORT_PERIODS billed periods are rewritten on every update.
Both: the running sum continues from the stored row before the rewritten window, so re-imports are
idempotent.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import BillingPeriod, HourReading
from .billing import allocate_costs, period_bounds
from .const import COST_REIMPORT_PERIODS, DOMAIN, LOGGER, REIMPORT_HOURS


def _slug(account: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in account.lower()).strip("_") or "account"


def statistic_id_for(account: str) -> str:
    """`sjwater_stats:<account>_water` (lowercase letters, digits and underscores only)."""
    return f"{DOMAIN}:{_slug(account)}_water"


def cost_statistic_id_for(account: str) -> str:
    """`sjwater_stats:<account>_water_cost`."""
    return f"{DOMAIN}:{_slug(account)}_water_cost"


def statistic_name(account: str) -> str:
    """Shown in the Energy dashboard's water picker."""
    return f"San Jose Water usage ({account[-4:]})"


def cost_statistic_name(account: str) -> str:
    """Shown in the Energy dashboard's cost picker."""
    return f"San Jose Water cost ({account[-4:]})"


async def _async_import(
    hass: HomeAssistant,
    metadata: StatisticMetaData,
    values: list[tuple[datetime, float]],
    *,
    rewind: timedelta | None = None,
    rewrite_from: datetime | None = None,
) -> int:
    """Write hourly (start, value) rows with a running sum; returns how many rows were written.

    With history stored, rows are written from just after the last stored hour, or earlier: `rewind`
    before the last stored hour, or from `rewrite_from`.
    """
    if not values:
        return 0
    statistic_id = metadata["statistic_id"]
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(get_last_statistics, hass, 1, statistic_id, True, {"sum"})

    base_sum = 0.0
    window_start = values[0][0]
    if last.get(statistic_id):
        last_start = dt_util.utc_from_timestamp(last[statistic_id][0]["start"])
        candidate = last_start + timedelta(hours=1)
        if rewind is not None:
            candidate = min(last_start, values[-1][0]) - rewind
        if rewrite_from is not None:
            candidate = min(candidate, rewrite_from)
        window_start = max(values[0][0], candidate)
        if window_start > values[0][0]:
            prior = await recorder.async_add_executor_job(
                statistics_during_period,
                hass,
                window_start - timedelta(days=90),
                window_start,
                {statistic_id},
                "hour",
                None,
                {"sum"},
            )
            rows = prior.get(statistic_id) or []
            if rows and rows[-1].get("sum") is not None:
                base_sum = float(rows[-1]["sum"])
            else:
                window_start = values[0][0]  # nothing stored before the window: rebuild from the start

    total = base_sum
    stats: list[StatisticData] = []
    for start, value in values:
        if start < window_start:
            continue
        total += value
        stats.append(StatisticData(start=start, state=value, sum=round(total, 3)))
    if stats:
        async_add_external_statistics(hass, metadata, stats)
    LOGGER.debug("Imported %s hourly rows into %s from %s", len(stats), statistic_id, window_start)
    return len(stats)


async def async_import_usage(hass: HomeAssistant, statistic_id: str, account: str, hours: list[HourReading]) -> int:
    """Import published hours; returns how many hourly rows were written."""
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=statistic_name(account),
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class="volume",
        unit_of_measurement=UnitOfVolume.GALLONS,
    )
    values = [(h.start, h.gallons) for h in hours if not h.unavailable]
    return await _async_import(hass, metadata, values, rewind=timedelta(hours=REIMPORT_HOURS))


async def async_import_cost(
    hass: HomeAssistant, statistic_id: str, account: str, hours: list[HourReading], periods: list[BillingPeriod]
) -> int:
    """Import billed cost per hour; returns how many hourly rows were written."""
    # Like Opower's cost statistics: no unit, so the Energy dashboard shows it in the configured currency.
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=cost_statistic_name(account),
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=None,
        unit_of_measurement=None,
    )
    values = allocate_costs(hours, periods)
    recent = [p for p in periods if p.bill][-COST_REIMPORT_PERIODS:]
    rewrite_from = period_bounds(recent[0].start, recent[0].end)[0] if recent else None
    return await _async_import(hass, metadata, values, rewrite_from=rewrite_from)
