"""A synthetic SJWaterHUB for tests: same response shapes as the real portal, made-up data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
import json
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
from yarl import URL

from custom_components.sjwater_stats.api import BROKER_URL, PERMISSIONS_URL
from custom_components.sjwater_stats.const import BASE_URL

ACCOUNT = "1000200030"
OTHER_ACCOUNT = "1000200099"
FIRST_DAY = date(2026, 8, 1)
METER_INSTALLED = date(2026, 8, 5)  # zeros before this, like an account that got a smart meter mid-history
LAST_DAY = date(2026, 10, 19)
LAST_UPDATED = "2026-10-19T18:00:00-06:00"  # the portal's odd fixed offset; 18:00 is Pacific clock time

# Bi-monthly meter reads (read date, days in period, CCF), oldest first. Each period starts on the
# previous read date. Made up, but chained like the real Usage History table.
READS = [
    (date(2025, 5, 28), 62, 7.0),
    (date(2025, 7, 25), 58, 12.0),
    (date(2025, 9, 23), 60, 10.0),
    (date(2025, 11, 21), 59, 8.0),
    (date(2026, 1, 28), 68, 6.0),
    (date(2026, 3, 30), 61, 7.0),
    (date(2026, 5, 28), 59, 9.0),
    (date(2026, 7, 27), 60, 14.0),
    (date(2026, 9, 24), 59, 13.0),
]
FIXED_PER_DAY = 1.30  # made-up tariff: a fixed charge per day plus a rate per CCF
RATE_PER_CCF = 11.00


def bill_amount(days: int, ccf: float) -> float:
    """What the made-up tariff charges for a period."""
    return round(FIXED_PER_DAY * days + RATE_PER_CCF * ccf, 2)


BILLS = [(read + timedelta(days=4), bill_amount(days, ccf)) for read, days, ccf in READS]


def typical_hour(day: date, hour: int) -> float:
    """Made-up household: showers, dishes, irrigation on Tue/Fri, a big hose day on Oct 18."""
    if day < METER_INSTALLED:
        return 0
    gallons = {6: 15, 7: 15, 18: 22, 19: 22, 21: 7}.get(hour, 0)
    if day.weekday() in (1, 4) and hour == 5:
        gallons += 300
    if day == date(2026, 10, 18) and hour == 20:
        gallons += 450  # hose left running Sunday evening
    return gallons


def hourly_graph(
    first: date = FIRST_DAY,
    last: date = LAST_DAY,
    value: Callable[[date, int], float] = typical_hour,
    leak_hours: set[tuple[date, int]] | None = None,
    unavailable: set[tuple[date, int]] | None = None,
    last_updated: str | None = LAST_UPDATED,
) -> dict[str, Any]:
    """`VXengage_GetHourlyGraph` result: 24 wall-clock labels per day (even on DST days)."""
    leak_hours, unavailable = leak_hours or set(), unavailable or set()
    labels, normal, leak, missing, daily_labels, daily = [], [], [], [], [], []
    day = first
    while day <= last:
        total = 0.0
        for hour in range(24):
            labels.append([day.isoformat(), f"{hour:02d}:00:00"])
            gallons = value(day, hour)
            is_leak = (day, hour) in leak_hours
            normal.append(str(int(0 if is_leak else gallons)))
            leak.append(str(int(gallons if is_leak else 0)))
            missing.append("1" if (day, hour) in unavailable else "0")
            total += gallons
        daily_labels.append(day.isoformat())
        daily.append(str(int(total)))
        day += timedelta(days=1)

    def sets(normal_data: list[str], leak_data: list[str], missing_data: list[str]) -> list[dict[str, Any]]:
        style = {"barPercentage": 0.1, "type": "bar"}
        return [
            {"backgroundColor": "#6ad4e0", "label": "Normal Use", "data": normal_data, **style},
            {"backgroundColor": "#f46b2a", "label": "Possible Leak", "data": leak_data, **style},
            {"backgroundColor": "#c4c4c4", "label": "Data Unavailable", "data": missing_data, **style},
        ]

    return {
        "ComplexLabels": labels,
        "LastUpdated": last_updated,
        "ComplexDragLabels": [d for d in daily_labels for _ in (0, 1)],
        "ComplexSlidingGraphDataSets": sets(normal, leak, missing),
        "ComplexSlidingGraphDragDataSet": {"data": [v for v in daily for _ in (0, 1)]},
        "SlidingGraphDataSets": sets(daily, ["0"] * len(daily), ["0"] * len(daily)),
        "DragLabels": daily_labels,
        "SlidingGraphDragDataSet": {"data": daily},
        "Labels": daily_labels,
        "DataSets": None,
        "SessionID": "",
        "Success": True,
        "StatusMessage": None,
        "HttpResponseCode": -1,
        "ErrorCode": 0,
        "ErrorName": None,
        "ErrorMessage": None,
    }


def page(token: str, accounts: list[str] | None = None, usage_account: str | None = None) -> str:
    """A portal page: hidden token fields, the account selector and the usage script."""
    options = "".join(f'<option value="{a}">{a}</option>' for a in (accounts or []))
    script = (
        f'const hourlyGraphRequest = {{ Actions: "VXengage_GetHourlyGraph", AccountNumber: "{usage_account}", '
        "Template: \"RealTimeChart\", StartDate: '', EndDate: '' };"
        if usage_account
        else ""
    )
    return (
        f'<html><body><input type="hidden" name="Token" id="Token" value="{token}" autocomplete="off"/>'
        '<input type="hidden" name="SitePrefix" id="SitePrefix" value=""/>'
        f'<select class="form-select" id="accountSelector">{options}</select>'
        f"<script>{script}</script></body></html>"
    )


def usage_history_page(token: str, reads: list[tuple[date, int, float]]) -> str:
    """The Usage History page: table#waterusageTbl, newest first, like the portal."""
    rows = "".join(
        f'<tr><td tabindex="0" data-th="Read Date">{d:%m/%d/%Y}</td>'
        f'<td tabindex="0" data-th="No. Of Days" class="col text-end text-tnum">{days}</td>'
        f'<td tabindex="0" data-th="Usage" class="text-end text-tnum">{ccf:.1f}</td></tr>'
        for d, days, ccf in reversed(reads)
    )
    table = (
        '<table class="table" id="waterusageTbl"><thead><tr><th>Read Date</th><th>No. Of Days</th>'
        f"<th>Usage (CCF)</th></tr></thead><tbody>{rows}</tbody></table>"
        '<table id="gasusageTbl"><tbody><tr><td>No entries found</td></tr></tbody></table>'
    )
    return page(token).replace("</body>", table + "</body>")


def billing_history_page(token: str, bills: list[tuple[date, float]]) -> str:
    """The Billing History page: one row per bill, newest first, like the portal."""
    rows = "".join(
        f'<tr><td tabindex="0" class="text-nowrap" data-th="Bill Date"><a data-viewbilldate="{d.isoformat()}" '
        f'data-invoicenumber="" class="viewBillHistory" href="javascript:void(0);">{d:%m/%d/%Y}</a></td>'
        f'<td tabindex="0" data-th="Amount" class="text-end text-tnum">${amount:,.2f}</td></tr>'
        for d, amount in reversed(bills)
    )
    table = f"<table><thead><tr><th>Bill Date</th><th>Amount</th></tr></thead><tbody>{rows}</tbody></table>"
    return page(token).replace("</body>", table + "</body>")


class FakePortal:
    """Answers the client's requests the way SJWaterHUB does."""

    def __init__(
        self,
        accounts: list[str] | None = None,
        graph: dict[str, Any] | None = None,
        login_result: dict[str, Any] | None = None,
        reads: list[tuple[date, int, float]] | None = None,
        bills: list[tuple[date, float]] | None = None,
        billing_down: bool = False,
    ) -> None:
        self.accounts = accounts or [ACCOUNT]
        self.selected = self.accounts[0]
        self.graph = graph if graph is not None else hourly_graph()
        self.login_result = login_result or {"Success": True, "ValidToken": False, "RedirectUrl": "/AccountSummary"}
        self.reads = READS if reads is None else reads
        self.bills = BILLS if bills is None else bills
        self.billing_down = billing_down
        self.calls: list[str] = []
        self.tokens = 0
        self.permitted: set[str] = set()  # tokens the page-load permissions call has registered

    def _token(self) -> str:
        self.tokens += 1
        return f"tok{self.tokens}"

    def register(self, aioclient_mock) -> None:
        aioclient_mock.get(BASE_URL + "/", text=page("tok-login"))
        aioclient_mock.post(f"{BASE_URL}/AccountSummary", side_effect=self._summary)
        aioclient_mock.post(f"{BASE_URL}/UnderstandUsage", side_effect=self._usage_page)
        aioclient_mock.post(f"{BASE_URL}/UsageHistory", side_effect=self._usage_history)
        aioclient_mock.post(f"{BASE_URL}/BillingHistory", side_effect=self._billing_history)
        aioclient_mock.post(PERMISSIONS_URL, side_effect=self._permissions)
        aioclient_mock.post(BROKER_URL, side_effect=self._broker)

    async def _summary(self, method, url, data):
        return AiohttpClientMockResponse(method, url, text=page(self._token(), self.accounts))

    async def _usage_page(self, method, url, data):
        return AiohttpClientMockResponse(method, url, text=page(self._token(), self.accounts, self.selected))

    async def _usage_history(self, method, url, data):
        if self.billing_down:
            return AiohttpClientMockResponse(method, url, status=500)
        return AiohttpClientMockResponse(method, url, text=usage_history_page(self._token(), self.reads))

    async def _billing_history(self, method, url, data):
        return AiohttpClientMockResponse(method, url, text=billing_history_page(self._token(), self.bills))

    async def _permissions(self, method, url, data):
        self.calls.append("CreateExceptionPermissions")
        self.permitted.add(data["token"])
        return AiohttpClientMockResponse(method, url, text="")

    async def _broker(self, method, url, data):
        request = json.loads(json.loads(data)["Request"])
        action = request["Actions"]
        self.calls.append(action)
        body: dict[str, Any] = {"Authorized": True, "Validated": True, "Token": self._token()}
        if action == "VXengage_Login":
            # What the real portal appears to do: a token that skipped the permissions call looks like a bad password.
            permitted = request["Token"] in self.permitted
            body[action] = self.login_result if permitted else {"Success": False, "ErrorCode": 0}
        elif action == "VXengage_UpdateSelectedAccount":
            self.selected = request["SelectedAccountNumber"]
            body[action] = {"Success": True}
        elif action == "VXengage_GetHourlyGraph":
            assert request["AccountNumber"] == self.selected
            assert request["ViewName"] == "UnderstandUsage"
            assert request["Token"].startswith("tok")
            body[action] = self.graph
        else:
            body[action] = {"Success": True}
        return AiohttpClientMockResponse(method, URL(str(url)), json=body)
