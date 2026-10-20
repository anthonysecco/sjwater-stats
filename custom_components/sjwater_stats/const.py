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
CONF_STALE_HOURS = "stale_hours"  # hours without new portal data

DEFAULT_OPTIONS = {
    CONF_SCAN_INTERVAL: 6,
    CONF_STALE_HOURS: 24,
}
MIN_SCAN_HOURS = 2
MAX_SCAN_HOURS = 24
OLD_DEFAULT_SCAN_HOURS = 2  # 0.1.x default; entries still on it move to the new default

# Recent hours are re-imported on every update in case the portal revises them.
REIMPORT_HOURS = 48
# Possible-leak flags from the portal count as current for this long.
PORTAL_LEAK_WINDOW_HOURS = 48

# Billing
GALLONS_PER_CCF = 748.052  # 1 CCF (HCF) = 100 cubic feet
BILL_MATCH_DAYS = 14  # a bill is dated within this many days after its meter read
FIXED_FIT_PERIODS = 6  # recent bills used to estimate the fixed (non-usage) part of a bill
COST_REIMPORT_PERIODS = 3  # billed periods rewritten in the cost statistic on every update
SAME_PERIOD_TOLERANCE_DAYS = 20  # "same period last year" must end within this many days of a year earlier


def scan_interval(hours: float) -> timedelta:
    """Clamp the configured interval to the supported range."""
    return timedelta(hours=max(MIN_SCAN_HOURS, min(MAX_SCAN_HOURS, float(hours))))
