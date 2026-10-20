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
            "yesterday": data.yesterday.gallons,
            "daily_average": data.daily_average,
            "possible_leak": data.possible_leak,
            "leak_hours_7_days": data.leak_hours_7_days,
            "data_age_hours": data.data_age_hours,
        }
        billing = data.billing
        if billing is not None:
            summary["billing"] = {
                "periods": len(billing.periods),
                "billed_periods": sum(1 for p in billing.periods if p.bill),
                "first_read": billing.periods[0].end.isoformat() if billing.periods else None,
                "last_read": billing.periods[-1].end.isoformat() if billing.periods else None,
                "last_year_period_found": billing.last_year_period is not None,
                "vs_last_year": billing.vs_last_year,
                "period_to_date_gallons": billing.period_to_date,
            }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "summary": summary,
    }
