"""Parser and derived-signal tests (no Home Assistant instance needed)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.sjwater_stats.api import (
    AccountLocked,
    InvalidAuth,
    NoAccounts,
    NoUsageData,
    SessionExpired,
    SJWaterError,
    VerificationRequired,
    login_error,
    parse_accounts,
    parse_last_updated,
    parse_page_token,
    parse_usage,
)
from custom_components.sjwater_stats.const import SJW_TZ
from custom_components.sjwater_stats.coordinator import derive, overnight_window

from .portal import ACCOUNT, METER_INSTALLED, OTHER_ACCOUNT, hourly_graph, page, typical_hour

NOW = datetime(2026, 10, 19, 19, 30, tzinfo=SJW_TZ)


def test_page_token_and_accounts() -> None:
    html = page("abc&amp;123", [ACCOUNT, OTHER_ACCOUNT], OTHER_ACCOUNT)
    assert parse_page_token(html) == ("abc&123", "")
    assert parse_accounts(html) == (OTHER_ACCOUNT, [ACCOUNT, OTHER_ACCOUNT])
    assert parse_accounts(page("t")) == (None, [])
    with pytest.raises(SessionExpired):
        parse_page_token("<html>Login</html>")


def test_last_updated_is_pacific_clock_time() -> None:
    assert parse_last_updated("2026-10-19T18:00:00-06:00") == datetime(2026, 10, 19, 18, 0, tzinfo=SJW_TZ)
    assert parse_last_updated(None) is None
    assert parse_last_updated("garbage") is None


def test_parse_usage_publishes_only_complete_hours() -> None:
    usage = parse_usage(hourly_graph(), NOW)
    assert usage.last_updated == datetime(2026, 10, 19, 18, 0, tzinfo=SJW_TZ)
    # The portal pads 18:00-23:00 with zeros; only hours ending by 18:00 are real.
    assert usage.hours[-1].local_start == datetime(2026, 10, 19, 17, 0, tzinfo=SJW_TZ)
    assert all(h.start + timedelta(hours=1) <= usage.last_updated for h in usage.hours)
    # Leading zeros from before the smart meter are dropped.
    assert usage.hours[0].local_start.date() == METER_INSTALLED
    assert usage.hours[0].gallons > 0
    assert usage.daily_totals()[date(2026, 10, 17)] == sum(typical_hour(date(2026, 10, 17), h) for h in range(24))


def test_parse_usage_never_past_now() -> None:
    """Even if LastUpdated were in the future, nothing past the current hour is imported."""
    early = datetime(2026, 10, 19, 9, 10, tzinfo=SJW_TZ)
    usage = parse_usage(hourly_graph(), early)
    assert usage.hours[-1].local_start == datetime(2026, 10, 19, 8, 0, tzinfo=SJW_TZ)


def test_parse_usage_dst_days() -> None:
    graph = hourly_graph(
        date(2026, 3, 7), date(2026, 3, 9), value=lambda d, h: 10, last_updated="2026-03-10T00:00:00"
    )
    usage = parse_usage(graph, datetime(2026, 3, 10, 6, tzinfo=SJW_TZ))
    spring = [h for h in usage.hours if h.local_start.date() == date(2026, 3, 8)]
    assert len(spring) == 23  # 02:00 does not exist; its reading is merged into 03:00 PDT
    assert sum(h.gallons for h in spring) == 240
    assert len({h.start for h in usage.hours}) == len(usage.hours)

    graph = hourly_graph(
        date(2025, 11, 1), date(2025, 11, 3), value=lambda d, h: 10, last_updated="2025-11-04T00:00:00"
    )
    usage = parse_usage(graph, datetime(2025, 11, 4, 6, tzinfo=SJW_TZ))
    fall = [h for h in usage.hours if h.local_start.date() == date(2025, 11, 2)]
    assert sum(h.gallons for h in fall) == 240  # 24 labels; the repeated 01:00 hour just isn't reported
    assert len({h.start for h in usage.hours}) == len(usage.hours)


def test_parse_usage_errors() -> None:
    with pytest.raises(SJWaterError):
        parse_usage({"Success": False, "ErrorMessage": "nope"}, NOW)
    with pytest.raises(NoUsageData):
        parse_usage(hourly_graph(value=lambda d, h: 0), NOW)
    with pytest.raises(NoUsageData):
        parse_usage({**hourly_graph(), "ComplexLabels": []}, NOW)


def test_login_error_names_the_portal_code() -> None:
    err = login_error({"Success": False, "ErrorCode": 0})
    assert type(err) is InvalidAuth
    assert "ErrorCode 0" in str(err)
    assert type(login_error({"ErrorCode": "9", "ErrorName": "NoQualifyingAccounts"})) is NoAccounts
    assert "NoQualifyingAccounts" in str(login_error({"ErrorCode": 9, "ErrorName": "NoQualifyingAccounts"}))
    assert type(login_error({"ErrorCode": 1})) is SJWaterError
    assert type(login_error({"ErrorCode": 105, "RestrictedTimeRemainingSeconds": 840})) is AccountLocked
    assert "840 seconds" in str(login_error({"Restricted": True, "RestrictedTimeRemainingSeconds": 840}))
    assert type(login_error({"ErrorCode": 39})) is VerificationRequired
    assert type(login_error({"ErrorCode": None})) is InvalidAuth
    assert type(login_error({"ErrorCode": "x"})) is InvalidAuth


def test_derive_typical_day() -> None:
    usage = parse_usage(hourly_graph(), NOW)
    data = derive(usage, {}, NOW)
    assert data.last_hour.local_start.hour == 17
    assert data.today.gallons == 15 + 15  # 06:00 and 07:00 published so far; Monday has no irrigation
    assert data.today.complete is False
    assert data.yesterday.gallons == 81 + 450
    assert data.yesterday.complete is True
    days = [date(2026, 10, 18) - timedelta(days=i) for i in range(30)]
    expected_30 = sum(typical_hour(d, h) for d in days for h in range(24))
    assert data.last_30_days == expected_30
    assert data.daily_average == round(expected_30 / 30, 1)
    assert data.high_daily_usage is True  # 531 gal vs a ~170 gal average
    assert data.high_hourly_usage is True  # 450 gal at 20:00 yesterday is within the last 24 h
    assert data.peak_hour.gallons == 450
    assert data.overnight_minimum == 0
    assert data.possible_leak is False
    assert data.longest_run_hours == 4  # 18:00-21:00 yesterday
    assert data.data_stale is False
    assert round(data.last_hour.gallons / 60, 2) == 0


def test_derive_leak_signals() -> None:
    def dripping(day: date, hour: int) -> float:
        return typical_hour(day, hour) + (7 if day >= date(2026, 10, 18) else 0)

    data = derive(parse_usage(hourly_graph(value=dripping), NOW), {}, NOW)
    assert data.overnight_minimum == 7
    assert "overnight_flow" in data.leak_reasons
    assert "continuous_use" in data.leak_reasons
    assert data.longest_run_hours == 24
    assert data.possible_leak is True

    flagged = hourly_graph(
        leak_hours={(date(2026, 10, 19), 3)},
        value=lambda d, h: typical_hour(d, h) + (30 if h == 3 else 0),
    )
    data = derive(parse_usage(flagged, NOW), {}, NOW)
    assert data.leak_reasons[0] == "portal_leak_flag"
    assert data.last_portal_leak.local_start == datetime(2026, 10, 19, 3, tzinfo=SJW_TZ)


def test_derive_options_and_staleness() -> None:
    usage = parse_usage(hourly_graph(), NOW)
    data = derive(usage, {"high_day_percent": 400, "high_hour_gallons": 500}, NOW)
    assert data.high_daily_usage is False
    assert data.high_hourly_usage is False
    late = NOW + timedelta(hours=30)
    assert derive(usage, {}, late).data_stale is True


def test_overnight_window() -> None:
    end = datetime(2026, 10, 19, 18, tzinfo=SJW_TZ)
    assert overnight_window(end, 1, 4) == (
        datetime(2026, 10, 19, 1, tzinfo=SJW_TZ),
        datetime(2026, 10, 19, 4, tzinfo=SJW_TZ),
    )
    assert overnight_window(datetime(2026, 10, 19, 2, tzinfo=SJW_TZ), 1, 4)[0].date() == date(2026, 10, 18)
    assert overnight_window(end, 23, 4)[0] == datetime(2026, 10, 18, 23, tzinfo=SJW_TZ)
    assert overnight_window(end.astimezone(UTC), 3, 3) is None
