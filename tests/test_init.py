"""Setup, entities, Energy-dashboard statistics and failure handling."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sjwater_stats.const import CONF_ACCOUNT, DEFAULT_OPTIONS, DOMAIN, SJW_TZ
from custom_components.sjwater_stats.diagnostics import async_get_config_entry_diagnostics
from custom_components.sjwater_stats.statistics import cost_statistic_id_for, statistic_id_for

from .portal import ACCOUNT, FIXED_PER_DAY, FakePortal, bill_amount, typical_hour

NOW = datetime(2026, 10, 19, 19, 30, tzinfo=SJW_TZ)
STAT_ID = statistic_id_for(ACCOUNT)
COST_ID = cost_statistic_id_for(ACCOUNT)


async def _setup(hass: HomeAssistant, units=US_CUSTOMARY_SYSTEM) -> MockConfigEntry:
    hass.config.units = units  # metric installs see liters; see test_metric_install_sees_liters
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        title="San Jose Water (0030)",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret", CONF_ACCOUNT: ACCOUNT},
        options=dict(DEFAULT_OPTIONS),
        minor_version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _hourly_rows(hass: HomeAssistant, statistic_id: str = STAT_ID) -> list[dict]:
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=SJW_TZ),
        None,
        {statistic_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return stats.get(statistic_id, [])


def _pacific_hours(start: datetime, end: datetime) -> list[tuple[date, int]]:
    """(Pacific date, hour) for each hour in [start, end); no clock change in the test ranges."""
    hours, t = [], start
    while t < end:
        hours.append((t.date(), t.hour))
        t += timedelta(hours=1)
    return hours


async def test_setup_entities_and_statistics(recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    yesterday = hass.states.get("sensor.san_jose_water_usage_yesterday")
    assert float(yesterday.state) == 531
    assert yesterday.attributes["unit_of_measurement"] == "gal"
    assert yesterday.attributes["device_class"] == "water"
    assert "state_class" not in yesterday.attributes  # statistics come from the import, not this sensor
    assert hass.states.get("binary_sensor.san_jose_water_leak_alert").state == "off"
    assert hass.states.get("binary_sensor.san_jose_water_data_delayed").state == "off"
    for retired in (
        "sensor.san_jose_water_usage_today",
        "sensor.san_jose_water_flow_rate",
        "binary_sensor.san_jose_water_high_hourly_usage",
        "binary_sensor.san_jose_water_high_daily_usage",
    ):
        assert hass.states.get(retired) is None

    # Billing sensors (made-up tariff in tests/portal.py).
    last_period = hass.states.get("sensor.san_jose_water_last_billing_period_usage")
    assert float(last_period.state) == round(13 * 748.052, 1)
    last_year = hass.states.get("sensor.san_jose_water_last_billing_period_usage_year_ago")
    assert float(last_year.state) == round(10 * 748.052, 1)
    assert last_year.attributes["period_end"] == "2025-09-23"
    vs = hass.states.get("sensor.san_jose_water_last_billing_period_vs_year_ago")
    assert float(vs.state) == 32.2
    assert vs.attributes["period_start"] == "2026-07-27"
    assert vs.attributes["year_ago_period_start"] == "2025-07-25"
    assert vs.attributes["year_ago_period_end"] == "2025-09-23"
    bill = hass.states.get("sensor.san_jose_water_last_billing_period_cost")
    assert float(bill.state) == bill_amount(59, 13.0)
    assert bill.attributes["unit_of_measurement"] == "USD"
    assert bill.attributes["bill_date"] == "2026-09-28"
    cost_year_ago = hass.states.get("sensor.san_jose_water_last_billing_period_cost_year_ago")
    assert float(cost_year_ago.state) == bill_amount(60, 10.0)
    assert hass.states.get("sensor.san_jose_water_usage_average_daily") is not None
    assert hass.states.get("sensor.san_jose_water_leak_alert_last") is not None
    this_period = hass.states.get("sensor.san_jose_water_usage_this_billing_period")
    assert this_period.attributes["period_start"] == "2026-09-24"
    registry = er.async_get(hass)
    account = registry.async_get(registry.async_get_entity_id("sensor", DOMAIN, f"{ACCOUNT}_account_number"))
    assert account.entity_category is EntityCategory.DIAGNOSTIC
    assert hass.states.get(account.entity_id).state == ACCOUNT

    rows = await _hourly_rows(hass)
    starts = [datetime.fromtimestamp(r["start"], SJW_TZ) for r in rows]
    assert starts[0] == datetime(2026, 8, 5, 6, tzinfo=SJW_TZ)  # first real reading
    assert starts[-1] == datetime(2026, 10, 19, 17, tzinfo=SJW_TZ)  # last published hour, nothing later
    assert len(set(starts)) == len(starts)
    day = datetime(2026, 8, 5).date()
    expected_total = 0.0
    while day <= datetime(2026, 10, 19).date():
        last_hour = 17 if day == datetime(2026, 10, 19).date() else 23
        expected_total += sum(typical_hour(day, h) for h in range(last_hour + 1))
        day += timedelta(days=1)
    assert rows[-1]["sum"] == expected_total

    # Cost: the bill for the 2026-07-27..09-24 period (59 days, 13 CCF) over the hours the meter covers
    # (from Aug 5 06:00); hours from Sep 24 on wait for their bill.
    cost = await _hourly_rows(hass, COST_ID)
    cost_starts = [datetime.fromtimestamp(r["start"], SJW_TZ) for r in cost]
    assert cost_starts[0] == datetime(2026, 8, 5, 6, tzinfo=SJW_TZ)
    assert cost_starts[-1] == datetime(2026, 9, 23, 23, tzinfo=SJW_TZ)
    covered = _pacific_hours(datetime(2026, 8, 5, 6, tzinfo=SJW_TZ), datetime(2026, 9, 24, tzinfo=SJW_TZ))
    gallons = sum(typical_hour(d, h) for d, h in covered)
    amount = bill_amount(59, 13.0)
    fixed = FIXED_PER_DAY * 59
    expected_cost = fixed * len(covered) / (59 * 24) + (amount - fixed) * gallons / (13 * 748.052)
    assert cost[-1]["sum"] == pytest.approx(expected_cost, abs=0.5)
    assert cost[-1]["sum"] < amount

    # A second update re-imports the recent windows; the history must come out identical.
    freezer.move_to(NOW + timedelta(hours=2))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    again = await _hourly_rows(hass)
    assert [(r["start"], r["sum"]) for r in again] == [(r["start"], r["sum"]) for r in rows]
    cost_again = await _hourly_rows(hass, COST_ID)
    assert [(r["start"], r["sum"]) for r in cost_again] == [(r["start"], r["sum"]) for r in cost]

    assert portal.calls.count("VXengage_Login") == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_metric_install_sees_liters(recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    await _setup(hass, METRIC_SYSTEM)
    yesterday = hass.states.get("sensor.san_jose_water_usage_yesterday")
    assert yesterday.attributes["unit_of_measurement"] == "L"
    assert round(float(yesterday.state), 1) == round(531 * 3.785411784, 1)


async def test_billing_failure_keeps_hourly(recorder_mock, hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    FakePortal(billing_down=True).register(aioclient_mock)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert float(hass.states.get("sensor.san_jose_water_usage_yesterday").state) == 531
    assert hass.states.get("sensor.san_jose_water_last_billing_period_cost").state == "unknown"
    assert await _hourly_rows(hass)
    assert not await _hourly_rows(hass, COST_ID)


async def test_retired_entities_removed(recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret", CONF_ACCOUNT: ACCOUNT},
        options=dict(DEFAULT_OPTIONS),
        minor_version=2,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("sensor", DOMAIN, f"{ACCOUNT}_flow_rate", config_entry=entry)
    renamed = registry.async_get_or_create("binary_sensor", DOMAIN, f"{ACCOUNT}_possible_leak", config_entry=entry)
    kept = registry.async_get_or_create("sensor", DOMAIN, f"{ACCOUNT}_usage_yesterday", config_entry=entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(renamed.entity_id) is None
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{ACCOUNT}_leak_alert") is not None
    assert registry.async_get(kept.entity_id) is not None


@pytest.mark.parametrize(
    ("old_interval", "new_interval"),
    [(2, 6), (4, 4), (1, 2), (6, 6)],
)
async def test_migrate_options(
    recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer, old_interval, new_interval
) -> None:
    freezer.move_to(NOW)
    old_options = {
        "scan_interval": old_interval,
        "high_day_percent": 200,
        "high_hour_gallons": 400,
        "continuous_hours": 12,
        "overnight_start": 1,
        "overnight_end": 4,
        "stale_hours": 24,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret", CONF_ACCOUNT: ACCOUNT},
        options=old_options,
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.options == {"scan_interval": new_interval, "stale_hours": 24}


async def test_wrong_password_starts_reauth(recorder_mock, hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    FakePortal(login_result={"Success": False, "ErrorCode": 0}).register(aioclient_mock)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_portal_down_retries(recorder_mock, hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    aioclient_mock.get("https://www.sjwaterhub.com/", status=503)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_redacted(recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "secret" not in text
    assert ACCOUNT not in text
    assert "user@example.com" not in text
    assert diag["summary"]["yesterday"] == 531
