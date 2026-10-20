"""Parser and derived-signal tests (no Home Assistant instance needed)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from custom_components.sjwater_stats.api import (
    AccountLocked,
    Bill,
    BillingPeriod,
    HourReading,
    InvalidAuth,
    MeterRead,
    NoAccounts,
    NoUsageData,
    SessionExpired,
    SJWaterError,
    VerificationRequired,
    billing_periods,
    login_error,
    parse_accounts,
    parse_billing_history,
    parse_last_updated,
    parse_page_token,
    parse_usage,
    parse_usage_history,
)
from custom_components.sjwater_stats.billing import (
    allocate_costs,
    fixed_charge_per_day,
    percent_change_per_day,
    period_bounds,
    same_period_last_year,
)
from custom_components.sjwater_stats.const import SJW_TZ
from custom_components.sjwater_stats.coordinator import derive

from .portal import (
    ACCOUNT,
    BILLS,
    FIXED_PER_DAY,
    METER_INSTALLED,
    OTHER_ACCOUNT,
    READS,
    bill_amount,
    billing_history_page,
    hourly_graph,
    page,
    typical_hour,
    usage_history_page,
)

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
    assert data.data_through == datetime(2026, 10, 19, 18, tzinfo=SJW_TZ)
    assert data.yesterday.gallons == 81 + 450
    assert data.yesterday.complete is True
    days = [date(2026, 10, 18) - timedelta(days=i) for i in range(30)]
    expected_30 = sum(typical_hour(d, h) for d in days for h in range(24))
    assert data.last_30_days == expected_30
    assert data.daily_average == round(expected_30 / 30, 1)
    assert data.possible_leak is False
    assert data.leak_hours_7_days == 0
    assert data.data_stale is False
    assert data.billing is None  # no billing periods passed


def test_derive_portal_leak_flag() -> None:
    flagged = hourly_graph(
        leak_hours={(date(2026, 10, 19), 3)},
        value=lambda d, h: typical_hour(d, h) + (30 if h == 3 else 0),
    )
    data = derive(parse_usage(flagged, NOW), {}, NOW)
    assert data.possible_leak is True
    assert data.leak_hours_7_days == 1
    assert data.last_portal_leak.local_start == datetime(2026, 10, 19, 3, tzinfo=SJW_TZ)

    # Water at night without San Jose Water's flag is not a leak any more.
    dripping = hourly_graph(value=lambda d, h: typical_hour(d, h) + 7)
    assert derive(parse_usage(dripping, NOW), {}, NOW).possible_leak is False

    # A flag older than the window stays in last_portal_leak but turns the sensor off.
    old = hourly_graph(leak_hours={(date(2026, 10, 10), 3)}, value=lambda d, h: 30 if h == 3 else 0)
    data = derive(parse_usage(old, NOW), {}, NOW)
    assert data.possible_leak is False
    assert data.last_portal_leak is not None


def test_derive_staleness() -> None:
    usage = parse_usage(hourly_graph(), NOW)
    assert derive(usage, {}, NOW + timedelta(hours=30)).data_stale is True
    assert derive(usage, {"stale_hours": 48}, NOW + timedelta(hours=30)).data_stale is False


def _periods() -> list[BillingPeriod]:
    reads = parse_usage_history(usage_history_page("t", READS))
    return billing_periods(reads, parse_billing_history(billing_history_page("t", BILLS)))


def test_parse_usage_and_billing_history() -> None:
    reads = parse_usage_history(usage_history_page("t", READS))
    assert [(r.read_date, r.days, r.ccf) for r in reads] == READS  # oldest first
    assert reads[-1].start == date(2026, 7, 27)
    assert reads[-1].gallons == round(13 * 748.052, 1)
    for earlier, later in zip(reads, reads[1:], strict=False):
        assert later.start == earlier.read_date  # periods chain

    bills = parse_billing_history(billing_history_page("t", BILLS))
    assert [(b.bill_date, b.amount) for b in bills] == BILLS
    big = parse_billing_history(billing_history_page("t", [(date(2026, 9, 28), 1234.5)]))
    assert big[0].amount == 1234.5  # "$1,234.50"

    with pytest.raises(SJWaterError):
        parse_usage_history(page("t"))


def test_billing_periods_pairing() -> None:
    reads = parse_usage_history(usage_history_page("t", READS))
    # Latest read not billed yet; a stray bill far from any read is ignored.
    bills = [Bill(d, a) for d, a in BILLS[:-1]] + [Bill(date(2026, 9, 20), 99.0)]
    periods = billing_periods(reads, bills)
    assert len(periods) == len(READS)
    assert periods[-1].bill is None
    assert periods[-2].bill == Bill(date(2026, 7, 31), bill_amount(60, 14.0))
    assert all(p.bill and p.bill.bill_date > p.end for p in periods[:-1])


def test_year_over_year() -> None:
    periods = _periods()
    last = periods[-1]
    before = same_period_last_year(periods, last)
    assert before is not None
    assert before.end == date(2025, 9, 23)
    assert percent_change_per_day(last, before) == round(((13 / 59) / (10 / 60) - 1) * 100, 1)  # 32.2
    assert same_period_last_year(periods, periods[0]) is None


def test_fixed_charge_estimate() -> None:
    assert fixed_charge_per_day(_periods()) == pytest.approx(FIXED_PER_DAY, abs=0.02)
    assert fixed_charge_per_day(_periods()[:2]) == 0  # too few bills to fit


def _flat_hours(start: date, end: date, gallons: float) -> list[HourReading]:
    first, last = period_bounds(start, end)
    hours, t = [], first
    while t < last:
        hours.append(HourReading(start=t, gallons=gallons, possible_leak=False, unavailable=False))
        t += timedelta(hours=1)
    return hours


def test_allocate_costs_complete_period_matches_bill() -> None:
    periods = _periods()
    period = periods[-2]  # 2026-05-28 to 2026-07-27, fully metered here
    hours = _flat_hours(period.start, period.end, 10.0)
    hours[5] = HourReading(start=hours[5].start, gallons=500.0, possible_leak=False, unavailable=False)
    costs = allocate_costs(hours, periods)
    assert len(costs) == len(hours)
    assert sum(c for _, c in costs) == pytest.approx(period.bill.amount, abs=0.01)
    assert costs[5][1] > costs[4][1]  # the big hour carries more of the usage charge
    assert costs[0][0] == period_bounds(period.start, period.end)[0]


def test_allocate_costs_partial_and_dst() -> None:
    periods = _periods()
    period = periods[-2]
    hours = _flat_hours(period.start, period.end, 10.0)
    half = hours[len(hours) // 2 :]
    partial = sum(c for _, c in allocate_costs(half, periods))
    fixed = fixed_charge_per_day(periods) * period.read.days
    expected = fixed * len(half) / len(hours) + (period.bill.amount - fixed) * (10.0 * len(half)) / period.read.gallons
    assert partial == pytest.approx(expected, abs=0.01)

    # A period across the November clock change: the portal has one hour fewer; still the whole bill.
    nov = [MeterRead(date(2026, 10, 1), 30, 5.0), MeterRead(date(2026, 11, 30), 60, 10.0)]
    dst_periods = billing_periods(nov, [Bill(date(2026, 12, 3), 150.0)])
    dst_hours = _flat_hours(date(2026, 10, 1), date(2026, 11, 30), 5.0)
    dst_hours.pop(800)
    assert sum(c for _, c in allocate_costs(dst_hours, dst_periods)) == pytest.approx(150.0, abs=0.01)


def test_derive_billing() -> None:
    usage = parse_usage(hourly_graph(), NOW)
    data = derive(usage, {}, NOW, _periods())
    billing = data.billing
    assert billing.last_period.end == date(2026, 9, 24)
    assert billing.last_year_period.end == date(2025, 9, 23)
    assert billing.vs_last_year == 32.2
    assert billing.last_bill.amount == bill_amount(59, 13.0)
    assert billing.period_start == date(2026, 9, 24)
    start = datetime(2026, 9, 24, tzinfo=SJW_TZ)
    expected = 0.0
    day = start.date()
    while day <= date(2026, 10, 19):
        expected += sum(typical_hour(day, h) for h in range(18 if day == date(2026, 10, 19) else 24))
        day += timedelta(days=1)
    assert billing.period_to_date == expected
    assert billing.period_days == round((datetime(2026, 10, 19, 18, tzinfo=SJW_TZ) - start).total_seconds() / 86400, 1)
