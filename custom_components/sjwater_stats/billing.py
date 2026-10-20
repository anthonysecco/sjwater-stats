"""Billing-period comparisons, and bills spread over hours for the Energy dashboard's cost.

San Jose Water bills every two months. Each bill is paired with the meter read a few days before it
(see api.billing_periods); a period runs from the previous read date to its read date (Pacific
midnights, end exclusive).

Cost per hour: a bill is a fixed part (service charge, roughly the same per day) plus a usage part.
The fixed part per day is estimated from recent bills with a least-squares fit of
amount = fixed_per_day * days + rate * CCF, so it needs no rate tables. Each period's fixed part is
spread evenly over its hours and the usage part in proportion to each hour's gallons. When the smart
meter covers the whole period, the hours add up to the bill exactly; for a period it only partly
covers (the first one), the covered hours get their share and the rest is left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from .api import Bill, BillingPeriod, HourReading
from .const import FIXED_FIT_PERIODS, SAME_PERIOD_TOLERANCE_DAYS, SJW_TZ


def period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Pacific midnights as UTC datetimes (UTC so subtraction counts real hours across DST)."""
    return (
        datetime.combine(start, time.min, SJW_TZ).astimezone(UTC),
        datetime.combine(end, time.min, SJW_TZ).astimezone(UTC),
    )


def _year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # Feb 29
        return day - timedelta(days=365)


def same_period_last_year(periods: list[BillingPeriod], period: BillingPeriod) -> BillingPeriod | None:
    """The period whose read date is closest to a year before `period`'s."""
    target = _year_earlier(period.end)
    candidates = [
        p for p in periods if p is not period and abs((p.end - target).days) <= SAME_PERIOD_TOLERANCE_DAYS
    ]
    return min(candidates, key=lambda p: abs((p.end - target).days), default=None)


def percent_change_per_day(current: BillingPeriod, previous: BillingPeriod) -> float | None:
    """Change in average daily use, in percent (periods differ in length by a few days)."""
    if current.read.days <= 0 or previous.read.days <= 0 or previous.read.ccf <= 0:
        return None
    now_rate = current.read.ccf / current.read.days
    then_rate = previous.read.ccf / previous.read.days
    return round((now_rate / then_rate - 1) * 100, 1)


def fixed_charge_per_day(periods: list[BillingPeriod]) -> float:
    """Estimated fixed part of a bill per day; 0 when recent bills don't support a sensible fit."""
    billed = [p for p in periods if p.bill and p.bill.amount > 0][-FIXED_FIT_PERIODS:]
    if len(billed) < 3:
        return 0.0
    days = [float(p.read.days) for p in billed]
    ccf = [p.read.ccf for p in billed]
    amount = [p.bill.amount for p in billed if p.bill]
    s_dd = sum(d * d for d in days)
    s_dc = sum(d * c for d, c in zip(days, ccf, strict=True))
    s_cc = sum(c * c for c in ccf)
    s_da = sum(d * a for d, a in zip(days, amount, strict=True))
    s_ca = sum(c * a for c, a in zip(ccf, amount, strict=True))
    det = s_dd * s_cc - s_dc * s_dc
    if abs(det) < 1e-9:
        return 0.0
    fixed = (s_da * s_cc - s_dc * s_ca) / det
    rate = (s_dd * s_ca - s_dc * s_da) / det
    if fixed < 0 or rate <= 0:
        return 0.0
    return min(fixed, min(a / d for a, d in zip(amount, days, strict=True)))


def allocate_costs(hours: list[HourReading], periods: list[BillingPeriod]) -> list[tuple[datetime, float]]:
    """(hour start in UTC, dollars) for every published hour inside a billed period, oldest first."""
    fixed_per_day = fixed_charge_per_day(periods)
    readings = [h for h in hours if not h.unavailable]
    costs: list[tuple[datetime, float]] = []
    for period in periods:
        if not period.bill or period.bill.amount <= 0:
            continue
        start, end = period_bounds(period.start, period.end)
        inside = [h for h in readings if start <= h.start < end]
        if not inside:
            continue
        period_hours = round((end - start) / timedelta(hours=1))
        amount = period.bill.amount
        fixed = min(fixed_per_day * period.read.days, amount)
        variable = amount - fixed
        metered = sum(h.gallons for h in inside)
        # The portal drops the repeated hour when clocks fall back, so allow one hour short.
        complete = len(inside) >= period_hours - 1
        per_hour = fixed / len(inside) if complete else fixed / period_hours
        if complete and metered <= 0:
            per_hour += variable / len(inside)
            per_gallon = 0.0
        elif complete:
            per_gallon = variable / metered
        else:
            per_gallon = variable / period.read.gallons if period.read.gallons > 0 else 0.0
        costs.extend((h.start, round(per_hour + h.gallons * per_gallon, 6)) for h in inside)
    costs.sort(key=lambda c: c[0])
    return costs


@dataclass(slots=True)
class BillingData:
    """What the billing sensors read."""

    periods: list[BillingPeriod]
    last_period: BillingPeriod | None
    last_year_period: BillingPeriod | None
    vs_last_year: float | None
    last_bill: Bill | None
    period_start: date | None
    period_to_date: float | None
    period_days: float | None


def derive_billing(periods: list[BillingPeriod], hours: list[HourReading], data_end: datetime | None) -> BillingData:
    """Billing sensors from the periods and the smart meter's published hours (up to `data_end`)."""
    last = periods[-1] if periods else None
    last_year = same_period_last_year(periods, last) if last else None
    billed = [p.bill for p in periods if p.bill]
    to_date = days = None
    if last and data_end:
        start, _ = period_bounds(last.end, last.end)
        if data_end >= start:
            to_date = round(sum(h.gallons for h in hours if not h.unavailable and h.start >= start), 1)
            days = round((data_end - start) / timedelta(days=1), 1)
    return BillingData(
        periods=periods,
        last_period=last,
        last_year_period=last_year,
        vs_last_year=percent_change_per_day(last, last_year) if last and last_year else None,
        last_bill=billed[-1] if billed else None,
        period_start=last.end if last else None,
        period_to_date=to_date,
        period_days=days,
    )
