"""Setup, entities, Energy-dashboard statistics and failure handling."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sjwater_stats.const import CONF_ACCOUNT, DEFAULT_OPTIONS, DOMAIN, SJW_TZ
from custom_components.sjwater_stats.diagnostics import async_get_config_entry_diagnostics
from custom_components.sjwater_stats.statistics import statistic_id_for

from .portal import ACCOUNT, FakePortal, typical_hour

NOW = datetime(2026, 10, 19, 19, 30, tzinfo=SJW_TZ)
STAT_ID = statistic_id_for(ACCOUNT)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        title="San Jose Water (0030)",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret", CONF_ACCOUNT: ACCOUNT},
        options=dict(DEFAULT_OPTIONS),
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _hourly_rows(hass: HomeAssistant) -> list[dict]:
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=SJW_TZ),
        None,
        {STAT_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    return stats.get(STAT_ID, [])


async def test_setup_entities_and_statistics(recorder_mock, hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert float(hass.states.get("sensor.san_jose_water_usage_today").state) == 30
    yesterday = hass.states.get("sensor.san_jose_water_usage_yesterday")
    assert float(yesterday.state) == 531
    assert yesterday.attributes["unit_of_measurement"] == "gal"
    assert yesterday.attributes["device_class"] == "water"
    assert "state_class" not in yesterday.attributes  # statistics come from the import, not this sensor
    flow = hass.states.get("sensor.san_jose_water_flow_rate")
    assert flow.attributes["unit_of_measurement"] == "gal/min"
    assert flow.attributes["device_class"] == "volume_flow_rate"
    assert hass.states.get("binary_sensor.san_jose_water_high_daily_usage").state == "on"
    assert hass.states.get("binary_sensor.san_jose_water_high_hourly_usage").state == "on"
    assert hass.states.get("binary_sensor.san_jose_water_possible_leak").state == "off"
    assert hass.states.get("binary_sensor.san_jose_water_data_delayed").state == "off"

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

    # A second update re-imports the recent window; the history must come out identical.
    freezer.move_to(NOW + timedelta(hours=2))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    again = await _hourly_rows(hass)
    assert [(r["start"], r["sum"]) for r in again] == [(r["start"], r["sum"]) for r in rows]

    assert portal.calls.count("VXengage_Login") == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


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
