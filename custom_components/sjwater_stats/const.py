"""Constants for San Jose Water."""

from __future__ import annotations

from datetime import timedelta
import logging
from zoneinfo import ZoneInfo

DOMAIN = "sjwater_stats"
LOGGER = logging.getLogger(__package__)

BASE_URL = "https://www.sjwaterhub.com"
ATTRIBUTION = "Data from SJWaterHUB (unofficial, not affiliated with San Jose Water)"
SJW_TZ = ZoneInfo("America/Los_Angeles")  # the portal labels hours in Pacific wall-clock time
REQUEST_TIMEOUT = 30  # seconds, per request

CONF_ACCOUNT = "account"

# Options
CONF_SCAN_INTERVAL = "scan_interval"  # hours
CONF_HIGH_DAY_PERCENT = "high_day_percent"  # % of the 30-day daily average
CONF_HIGH_HOUR_GALLONS = "high_hour_gallons"  # gallons in one hour
CONF_CONTINUOUS_HOURS = "continuous_hours"  # hours of uninterrupted use
CONF_OVERNIGHT_START = "overnight_start"  # hour of day, 0-23
CONF_OVERNIGHT_END = "overnight_end"  # hour of day, 0-23 (exclusive)
CONF_STALE_HOURS = "stale_hours"  # hours without new portal data

DEFAULT_OPTIONS = {
    CONF_SCAN_INTERVAL: 2,
    CONF_HIGH_DAY_PERCENT: 200,
    CONF_HIGH_HOUR_GALLONS: 400,
    CONF_CONTINUOUS_HOURS: 12,
    CONF_OVERNIGHT_START: 1,
    CONF_OVERNIGHT_END: 4,
    CONF_STALE_HOURS: 24,
}
MIN_SCAN_HOURS = 1
MAX_SCAN_HOURS = 6

# A "high" day must also be at least this many gallons, so tiny averages don't trigger it.
HIGH_DAY_MIN_GALLONS = 50
# Recent hours are re-imported on every update in case the portal revises them.
REIMPORT_HOURS = 48
# Possible-leak flags from the portal count as current for this long.
PORTAL_LEAK_WINDOW_HOURS = 48


def scan_interval(hours: float) -> timedelta:
    """Clamp the configured interval to the supported range."""
    return timedelta(hours=max(MIN_SCAN_HOURS, min(MAX_SCAN_HOURS, float(hours))))
