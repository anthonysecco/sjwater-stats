"""Client and parser for SJWaterHUB, San Jose Water's customer portal (VertexOne VXengage).

How the portal works (observed; it has no public API):
- Every page is a form POST carrying a one-time `Token`; each response hands out the next one.
  A plain GET of a signed-in page is treated as an expired session.
- Data calls go to POST /api/WebApi/RequestBroker with body {"Request": "<JSON string>"}. The inner
  object names the `Actions`, plus `Token`, `ViewName` (the current page) and `SitePrefix`.
- Sign-in is the `VXengage_Login` action, then a form POST of the returned token to `RedirectUrl`.
- Hourly smart-meter data comes from `VXengage_GetHourlyGraph` on the UnderstandUsage page: about
  two years of hours labelled [date, time] in Pacific wall-clock time (24 labels every day, even
  across daylight-saving changes), in gallons, split into Normal Use / Possible Leak / Data
  Unavailable series. Hours after `LastUpdated` are padded with zeros and are not real readings.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import urljoin

import aiohttp

from .const import BASE_URL, LOGGER, REQUEST_TIMEOUT, SJW_TZ

USER_AGENT = "HomeAssistant-SJWater-Stats (+https://github.com/anthonysecco/sjwater-stats)"
BROKER_URL = f"{BASE_URL}/api/WebApi/RequestBroker"
PERMISSIONS_URL = f"{BASE_URL}/api/WebApi/CreateExceptionPermissions"
USAGE_VIEW = "UnderstandUsage"

_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_ACCOUNT_RE = re.compile(r'VXengage_GetHourlyGraph"\s*,\s*AccountNumber\s*:\s*"([^"]+)"')
_SELECT_RE = re.compile(r'<select\b[^>]*id="accountSelector"[^>]*>(.*?)</select>', re.IGNORECASE | re.DOTALL)
_OPTION_RE = re.compile(r'<option\b[^>]*value="([^"]+)"', re.IGNORECASE)


class SJWaterError(Exception):
    """The portal could not be reached or understood."""


class InvalidAuth(SJWaterError):
    """Username or password rejected."""


class VerificationRequired(SJWaterError):
    """The portal wants a browser step first (MFA, email verification, password change)."""


class AccountLocked(SJWaterError):
    """Too many failed sign-ins, or the profile is disabled or blocked."""


class SessionExpired(SJWaterError):
    """The portal session ended; sign in again."""


class NoUsageData(SJWaterError):
    """The account has no smart-meter (hourly) data."""


class NoAccounts(SJWaterError):
    """The portal says the login has no qualifying accounts."""


# --- parsing ---------------------------------------------------------------------


def _hidden_inputs(page: str) -> dict[str, str]:
    """Values of <input> elements keyed by id (or name)."""
    found: dict[str, str] = {}
    for tag in _INPUT_RE.findall(page):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        key = attrs.get("id") or attrs.get("name")
        if key and "value" in attrs:
            found[key] = html_lib.unescape(attrs["value"])
    return found


def parse_page_token(page: str) -> tuple[str, str]:
    """The page's session token and site prefix."""
    inputs = _hidden_inputs(page)
    token = inputs.get("Token")
    if not token:
        raise SessionExpired("no session token on the page")
    return token, inputs.get("SitePrefix", "")


def parse_accounts(page: str) -> tuple[str | None, list[str]]:
    """The account the usage page is showing, and every account on the login."""
    current = _ACCOUNT_RE.search(page)
    select = _SELECT_RE.search(page)
    options = _OPTION_RE.findall(select.group(1)) if select else []
    accounts = list(dict.fromkeys(a.strip() for a in options if a.strip()))
    account = current.group(1).strip() if current else None
    if account and account not in accounts:
        accounts.insert(0, account)
    return account, accounts


@dataclass(slots=True, frozen=True)
class HourReading:
    """One published hour of water use."""

    start: datetime  # UTC
    gallons: float
    possible_leak: bool
    unavailable: bool

    @property
    def local_start(self) -> datetime:
        """Start in Pacific time."""
        return self.start.astimezone(SJW_TZ)


@dataclass(slots=True)
class Usage:
    """Parsed hourly usage for one account."""

    last_updated: datetime | None  # aware, Pacific
    hours: list[HourReading]  # published hours only, oldest first, from the first real reading

    def daily_totals(self) -> dict[date, float]:
        """Gallons per Pacific calendar day."""
        totals: dict[date, float] = {}
        for h in self.hours:
            if not h.unavailable:
                day = h.local_start.date()
                totals[day] = totals.get(day, 0.0) + h.gallons
        return totals


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def parse_last_updated(value: Any) -> datetime | None:
    """`LastUpdated` as Pacific time.

    The portal stamps it with a fixed -06:00 offset while the clock time shown to customers (and
    used for the hour labels) is Pacific, so the offset is ignored and the wall-clock time kept.
    """
    if not value:
        return None
    try:
        wall = datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None
    return wall.replace(tzinfo=SJW_TZ)


def parse_usage(graph: dict[str, Any], now: datetime | None = None) -> Usage:
    """Parse the `VXengage_GetHourlyGraph` result into published hourly readings."""
    if not isinstance(graph, dict) or not graph.get("Success"):
        raise SJWaterError(f"usage request failed: {graph.get('ErrorMessage') if isinstance(graph, dict) else graph}")
    labels = graph.get("ComplexLabels") or []
    datasets = graph.get("ComplexSlidingGraphDataSets") or []
    if not labels or not datasets:
        raise NoUsageData("the portal returned no hourly data for this account")

    def series(name: str, index: int) -> list[Any]:
        for ds in datasets:
            if str(ds.get("label", "")).strip().lower() == name:
                return ds.get("data") or []
        return (datasets[index].get("data") or []) if index < len(datasets) else []

    normal, leak, unavailable = series("normal use", 0), series("possible leak", 1), series("data unavailable", 2)
    last_updated = parse_last_updated(graph.get("LastUpdated"))
    cutoff = min(last_updated or (now or datetime.now(UTC)), now or datetime.now(UTC))

    merged: dict[datetime, list[float]] = {}  # UTC start -> [gallons, leak, unavailable]
    for i, label in enumerate(labels):
        try:
            day, clock = (label[0], label[1]) if isinstance(label, list) else str(label).split(" ", 1)
            wall = datetime.fromisoformat(f"{day}T{clock[:8]}")
        except (ValueError, IndexError, TypeError):
            continue
        # Pacific wall clock -> UTC. On the spring-forward day the nonexistent 02:00 label lands on
        # the same UTC hour as 03:00 and is merged into it; the repeated fall-back hour is absent.
        start = wall.replace(tzinfo=SJW_TZ).astimezone(UTC)
        if start + timedelta(hours=1) > cutoff:
            continue  # not yet published (the portal pads these with zeros)
        n = _number(normal[i]) if i < len(normal) else 0.0
        lk = _number(leak[i]) if i < len(leak) else 0.0
        un = _number(unavailable[i]) if i < len(unavailable) else 0.0
        slot = merged.setdefault(start, [0.0, 0.0, 0.0])
        slot[0] += n + lk
        slot[1] += lk
        slot[2] += un

    hours = [
        HourReading(start=s, gallons=v[0], possible_leak=v[1] > 0, unavailable=v[2] > 0 and v[0] == 0)
        for s, v in sorted(merged.items())
    ]
    first = next((i for i, h in enumerate(hours) if h.gallons > 0 or h.possible_leak), None)
    if first is None:
        raise NoUsageData("no smart-meter readings yet for this account")
    return Usage(last_updated=last_updated, hours=hours[first:])


def login_error(result: dict[str, Any]) -> SJWaterError:
    """The error for a refused `VXengage_Login`, checked in the same order as the portal's login page."""
    try:
        code = int(result.get("ErrorCode") or 0)
    except (TypeError, ValueError):
        code = 0
    detail = f"ErrorCode {code}" + (f", {name}" if (name := result.get("ErrorName")) else "")
    LOGGER.debug(
        "Sign-in refused: %s; flags %s; message %r",
        detail,
        sorted(k for k, v in result.items() if v is True),
        result.get("ErrorMessage") or result.get("StatusMessage"),
    )
    if result.get("IPAddressBlocked"):
        return AccountLocked(f"the portal has blocked sign-in from this IP address ({detail})")
    if result.get("PasswordChangeRequired") or result.get("EmailVerificationRequired"):
        return VerificationRequired(f"the portal wants a password change or email verification ({detail})")
    if result.get("ProfileDisabled"):
        return AccountLocked(f"the SJWaterHUB profile is disabled ({detail})")
    if code == 1:
        return SJWaterError(f"the portal reported a general sign-in error ({detail})")
    if result.get("Restricted") or code == 105:
        wait = result.get("RestrictedTimeRemainingSeconds")
        return AccountLocked(f"too many failed sign-ins; try again in {wait or 'a few'} seconds ({detail})")
    if code == 10:
        return VerificationRequired(f"the password has expired; change it at sjwaterhub.com ({detail})")
    if code in (38, 39):
        return VerificationRequired(f"the portal wants a verification step in a browser ({detail})")
    if code > 0:
        return NoAccounts(f"the portal says this login has no qualifying accounts ({detail})")
    return InvalidAuth(f"username or password not accepted ({detail})")


# --- HTTP ------------------------------------------------------------------------


class SJWaterClient:
    """Signs in and fetches hourly usage. Each fetch starts a fresh portal session."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self._token = ""
        self._prefix = ""
        self._view = "Login"

    async def _text(self, method: str, url: str, **kwargs: Any) -> tuple[str, str]:
        headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
        try:
            async with self._session.request(method, url, timeout=self._timeout, headers=headers, **kwargs) as resp:
                resp.raise_for_status()
                return await resp.text(), str(resp.url)
        except TimeoutError as err:
            raise SJWaterError(f"timed out: {method} {url}") from err
        except aiohttp.ClientError as err:
            raise SJWaterError(f"{method} {url}: {err}") from err

    async def _page(self, path: str, form: dict[str, str]) -> str:
        """POST to a portal page (how the site navigates) and pick up its token."""
        body, final_url = await self._text("POST", urljoin(BASE_URL + "/", path.lstrip("/")), data=form)
        if "/login" in final_url.lower() and "login" not in path.lower():
            raise SessionExpired("the portal returned the sign-in page")
        self._token, self._prefix = parse_page_token(body)
        self._view = path.strip("/").split("?")[0] or "Login"
        await self._permissions()
        return body

    async def _permissions(self) -> None:
        """What each page does on load; failures here are not fatal."""
        with contextlib.suppress(SJWaterError):
            await self._text("POST", PERMISSIONS_URL, data={"token": self._token, "sitePrefix": self._prefix})

    async def _broker(self, request: dict[str, Any]) -> dict[str, Any]:
        """One RequestBroker call; carries the token forward."""
        payload = {"ViewName": self._view, **request, "Token": self._token, "SitePrefix": self._prefix}
        body, _ = await self._text(
            "POST",
            BROKER_URL,
            data=json.dumps({"Request": json.dumps(payload)}),
            headers={
                "Content-Type": "application/json;charset=utf-8",
                "Accept": "application/json",
                # What the site's own jQuery calls send.
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/" if self._view == "Login" else f"{BASE_URL}/{self._view}",
            },
        )
        try:
            response = json.loads(body)
        except ValueError as err:
            raise SJWaterError("the portal returned a non-JSON response") from err
        if not isinstance(response, dict):
            raise SJWaterError("unexpected portal response")
        if response.get("SessionExpired"):
            raise SessionExpired("portal session expired")
        if response.get("Token"):
            self._token = response["Token"]
        return response

    async def login(self) -> None:
        """Sign in. Raises InvalidAuth, VerificationRequired, AccountLocked or NoAccounts on refusal."""
        self._session.cookie_jar.clear()
        body, _ = await self._text("GET", BASE_URL + "/")
        self._token, self._prefix = parse_page_token(body)
        self._view = "Login"
        await self._permissions()  # the login page does this on load, before signing in
        response = await self._broker(
            {
                "Actions": "VXengage_Login",
                "UserName": self._username,
                "Password": self._password,
                "ViewName": "Login",
                "Language": "",
                "ExistingMFAToken": None,
                "RedirectUrl": "",
                "AdditionalParameters": {"TargetElementId": "Login_Password"},
            }
        )
        if response.get("Validated") is False:
            raise InvalidAuth(f"the portal rejected the sign-in form: {response.get('ValidationErrors')}")
        result = response.get("VXengage_Login") or {}
        if result.get("Success"):
            await self._page(result.get("RedirectUrl") or "/AccountSummary", {"token": self._token})
            return
        raise login_error(result)

    async def _usage_page(self) -> str:
        return await self._page(f"/{USAGE_VIEW}", {"Token": self._token, "SitePrefix": self._prefix})

    async def accounts(self) -> tuple[str | None, list[str]]:
        """Sign in and list the accounts on this login (the first is the one shown by default)."""
        await self.login()
        return parse_accounts(await self._usage_page())

    async def fetch_usage(self, account: str | None = None, now: datetime | None = None) -> tuple[str, Usage]:
        """Sign in and fetch hourly usage for `account` (default: the portal's selected account)."""
        await self.login()
        page = await self._usage_page()
        current, accounts = parse_accounts(page)
        if account and current != account:
            if account not in accounts:
                raise SJWaterError("that account is no longer on this login")
            await self._broker({"Actions": "VXengage_UpdateSelectedAccount", "SelectedAccountNumber": account})
            page = await self._usage_page()
            current, _ = parse_accounts(page)
        if not current or (account and current != account):
            raise NoUsageData("the usage page did not show an account with smart-meter data")
        response = await self._broker(
            {
                "Actions": "VXengage_GetHourlyGraph",
                "AccountNumber": current,
                "Template": "RealTimeChart",
                "StartDate": "",
                "EndDate": "",
                "GroupData": False,
            }
        )
        return current, parse_usage(response.get("VXengage_GetHourlyGraph") or {}, now)
