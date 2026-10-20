"""Diagnostics for San Jose Water (credentials and account number redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNT
from .coordinator import SJWaterConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_ACCOUNT}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SJWaterConfigEntry) -> dict[str, Any]:
    """Options, derived values and a summary of the last fetch (no raw hourly history)."""
    coordinator = entry.runtime_data
    data = coordinator.data
    summary: dict[str, Any] | None = None
    if data is not None:
        hours = data.usage.hours
        summary = {
            "last_updated": data.usage.last_updated.isoformat() if data.usage.last_updated else None,
            "hours": len(hours),
            "first_hour": hours[0].start.isoformat() if hours else None,
            "last_hour": hours[-1].start.isoformat() if hours else None,
            "unavailable_hours": sum(1 for h in hours if h.unavailable),
            "leak_flag_hours": sum(1 for h in hours if h.possible_leak),
            "today": data.today.gallons,
            "yesterday": data.yesterday.gallons,
            "daily_average": data.daily_average,
            "overnight_minimum": data.overnight_minimum,
            "longest_run_hours": data.longest_run_hours,
            "leak_reasons": data.leak_reasons,
            "high_daily_usage": data.high_daily_usage,
            "high_hourly_usage": data.high_hourly_usage,
            "data_age_hours": data.data_age_hours,
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "summary": summary,
    }
