"""Config, account choice, reauth and options flows."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sjwater_stats.const import CONF_ACCOUNT, DEFAULT_OPTIONS, DOMAIN, SJW_TZ

from .portal import ACCOUNT, OTHER_ACCOUNT, FakePortal, hourly_graph

NOW = datetime(2026, 10, 19, 19, 30, tzinfo=SJW_TZ)
LOGIN = {CONF_USERNAME: " user@example.com ", CONF_PASSWORD: "secret"}


@pytest.fixture(autouse=True)
def no_setup():
    """Flows only; entry setup is covered in test_init."""
    with patch("custom_components.sjwater_stats.async_setup_entry", return_value=True):
        yield


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_single_account(hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "San Jose Water (0030)"
    assert result["data"] == {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret", CONF_ACCOUNT: ACCOUNT}
    assert result["options"] == DEFAULT_OPTIONS
    assert result["result"].unique_id == ACCOUNT


async def test_choose_between_accounts(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    fake = FakePortal(accounts=[ACCOUNT, OTHER_ACCOUNT])
    fake.register(aioclient_mock)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ACCOUNT: OTHER_ACCOUNT})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT] == OTHER_ACCOUNT
    assert "VXengage_UpdateSelectedAccount" in fake.calls


@pytest.mark.parametrize(
    ("login_result", "error"),
    [
        ({"Success": False, "ErrorCode": 0}, "invalid_auth"),
        ({"Success": False, "ErrorCode": 38, "RedirectUrl": "/Verify"}, "verification_required"),
        ({"Success": False, "Restricted": True, "ErrorCode": 105}, "account_locked"),
    ],
)
async def test_sign_in_errors(hass: HomeAssistant, aioclient_mock, freezer, login_result, error) -> None:
    freezer.move_to(NOW)
    FakePortal(login_result=login_result).register(aioclient_mock)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_no_smart_meter(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to(NOW)
    FakePortal(graph=hourly_graph(value=lambda d, h: 0)).register(aioclient_mock)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["errors"] == {"base": "no_usage_data"}


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get("https://www.sjwaterhub.com/", status=503)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    MockConfigEntry(domain=DOMAIN, unique_id=ACCOUNT, data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, portal: FakePortal, freezer) -> None:
    freezer.move_to(NOW)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "old", CONF_ACCOUNT: ACCOUNT},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ACCOUNT, data={}, options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    new = {**DEFAULT_OPTIONS, "high_hour_gallons": 600, "overnight_start": 0, "overnight_end": 5}
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["high_hour_gallons"] == 600
