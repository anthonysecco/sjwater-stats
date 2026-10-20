"""Config, reauth and options flows for San Jose Water."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import AccountLocked, InvalidAuth, NoUsageData, SJWaterClient, SJWaterError, VerificationRequired
from .const import (
    CONF_ACCOUNT,
    CONF_CONTINUOUS_HOURS,
    CONF_HIGH_DAY_PERCENT,
    CONF_HIGH_HOUR_GALLONS,
    CONF_OVERNIGHT_END,
    CONF_OVERNIGHT_START,
    CONF_SCAN_INTERVAL,
    CONF_STALE_HOURS,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOGGER,
    MAX_SCAN_HOURS,
    MIN_SCAN_HOURS,
)
from .coordinator import SJWaterConfigEntry

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))
CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


OPTION_FIELDS = {
    CONF_SCAN_INTERVAL: _number(MIN_SCAN_HOURS, MAX_SCAN_HOURS, 1, "h"),
    CONF_HIGH_DAY_PERCENT: _number(110, 1000, 10, "%"),
    CONF_HIGH_HOUR_GALLONS: _number(10, 5000, 10, "gal"),
    CONF_CONTINUOUS_HOURS: _number(2, 24, 1, "h"),
    CONF_OVERNIGHT_START: _number(0, 23, 1, "h"),
    CONF_OVERNIGHT_END: _number(0, 23, 1, "h"),
    CONF_STALE_HOURS: _number(6, 168, 1, "h"),
}


def options_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Options form, pre-filled with the current values."""
    values = {**DEFAULT_OPTIONS, **current}
    return vol.Schema({vol.Required(key, default=values[key]): field for key, field in OPTION_FIELDS.items()})


def _error_key(err: Exception) -> str:
    if isinstance(err, InvalidAuth):
        return "invalid_auth"
    if isinstance(err, VerificationRequired):
        return "verification_required"
    if isinstance(err, AccountLocked):
        return "account_locked"
    if isinstance(err, NoUsageData):
        return "no_usage_data"
    return "cannot_connect"


def _title(account: str) -> str:
    return f"San Jose Water ({account[-4:]})"


class SJWaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a San Jose Water account (one entry per account)."""

    VERSION = 1

    def __init__(self) -> None:
        """Start a flow."""
        self._credentials: dict[str, str] = {}
        self._accounts: list[str] = []

    def _client(self, username: str, password: str) -> SJWaterClient:
        session = async_create_clientsession(self.hass, cookie_jar=aiohttp.CookieJar())
        return SJWaterClient(session, username, password)

    async def _check_account(self, account: str) -> str | None:
        """Error key, or None when hourly data is available for this account."""
        client = self._client(self._credentials[CONF_USERNAME], self._credentials[CONF_PASSWORD])
        try:
            await client.fetch_usage(account)
        except SJWaterError as err:
            LOGGER.warning("San Jose Water usage check failed: %s", err)
            return _error_key(err)
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the SJWaterHUB login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            client = self._client(**self._credentials)
            try:
                current, self._accounts = await client.accounts()
            except SJWaterError as err:
                LOGGER.warning("San Jose Water sign-in failed: %s", err)
                errors["base"] = _error_key(err)
            else:
                if len(self._accounts) > 1:
                    return await self.async_step_account()
                if not current:
                    errors["base"] = "no_usage_data"
                elif (result := await self._create(current, errors)) is not None:
                    return result
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of several accounts on the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._create(user_input[CONF_ACCOUNT], errors)
            if result is not None:
                return result
        schema = vol.Schema(
            {vol.Required(CONF_ACCOUNT): SelectSelector(SelectSelectorConfig(options=self._accounts))}
        )
        return self.async_show_form(step_id="account", data_schema=schema, errors=errors)

    async def _create(self, account: str, errors: dict[str, str]) -> ConfigFlowResult | None:
        await self.async_set_unique_id(account)
        self._abort_if_unique_id_configured()
        if error := await self._check_account(account):
            errors["base"] = error
            return None
        return self.async_create_entry(
            title=_title(account),
            data={**self._credentials, CONF_ACCOUNT: account},
            options=dict(DEFAULT_OPTIONS),
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the current password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            self._credentials = {CONF_USERNAME: entry.data[CONF_USERNAME], CONF_PASSWORD: password}
            if not (error := await self._check_account(entry.data[CONF_ACCOUNT])):
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: password})
            errors["base"] = error
        schema = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SJWaterConfigEntry) -> SJWaterOptionsFlow:
        """Thresholds and polling interval."""
        return SJWaterOptionsFlow()


class SJWaterOptionsFlow(OptionsFlowWithReload):
    """Edit the polling interval and alert thresholds; the entry reloads on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show or save the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=options_schema(self.config_entry.options))
