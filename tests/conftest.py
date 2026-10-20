"""Shared fixtures for San Jose Water tests."""

from __future__ import annotations

import pytest

from .portal import FakePortal


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture
def portal(aioclient_mock) -> FakePortal:
    """A synthetic SJWaterHUB with one account and ~80 days of made-up hourly data."""
    fake = FakePortal()
    fake.register(aioclient_mock)
    return fake
