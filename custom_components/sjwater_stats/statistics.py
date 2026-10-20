"""Hourly water use as an external statistic for the Energy dashboard.

Why an external statistic and not a sensor's own statistics: the portal publishes hours 4-6 h
late, so a sensor's state changes would be booked to the wrong hours. And writing rows into a
statistic the recorder also compiles from a sensor collides with its own rows. A separate
`sjwater_stats:` statistic avoids both.

Rules: only published, complete hours are imported (never the zero-padded future hours); the
last REIMPORT_HOURS are rewritten on every update in case the portal revised them; the running
sum continues from the stored row before that window, so re-imports are idempotent.
"""

from __future__ import annotations

from datetime import timedelta

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

from .api import HourReading
from .const import DOMAIN, LOGGER, REIMPORT_HOURS


def statistic_id_for(account: str) -> str:
    """`sjwater_stats:<account>_water` (lowercase letters, digits and underscores only)."""
    slug = "".join(c if c.isalnum() else "_" for c in account.lower()).strip("_") or "account"
    return f"{DOMAIN}:{slug}_water"


def statistic_name(account: str) -> str:
    """Shown in the Energy dashboard's water picker."""
    return f"San Jose Water usage ({account[-4:]})"


async def async_import_usage(hass: HomeAssistant, statistic_id: str, account: str, hours: list[HourReading]) -> int:
    """Import published hours; returns how many hourly rows were written."""
    readings = [h for h in hours if not h.unavailable]
    if not readings:
        return 0
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(get_last_statistics, hass, 1, statistic_id, True, {"sum"})

    base_sum = 0.0
    window_start = readings[0].start
    if last.get(statistic_id):
        last_start = dt_util.utc_from_timestamp(last[statistic_id][0]["start"])
        window_start = max(readings[0].start, min(last_start, readings[-1].start) - timedelta(hours=REIMPORT_HOURS))
        if window_start > readings[0].start:
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
                window_start = readings[0].start  # nothing stored before the window: rebuild from the start

    total = base_sum
    stats: list[StatisticData] = []
    for h in readings:
        if h.start < window_start:
            continue
        total += h.gallons
        stats.append(StatisticData(start=h.start, state=h.gallons, sum=round(total, 3)))

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=statistic_name(account),
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class="volume",
        unit_of_measurement=UnitOfVolume.GALLONS,
    )
    async_add_external_statistics(hass, metadata, stats)
    LOGGER.debug("Imported %s hourly rows into %s from %s", len(stats), statistic_id, window_start)
    return len(stats)
