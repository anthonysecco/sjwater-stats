"""San Jose Water: smart-meter use, billing periods and San Jose Water's leak flag from SJWaterHUB."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .binary_sensor import BINARY_SENSOR_KEYS
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIONS,
    LOGGER,
    MAX_SCAN_HOURS,
    MIN_SCAN_HOURS,
    OLD_DEFAULT_SCAN_HOURS,
)
from .coordinator import SJWaterConfigEntry, SJWaterCoordinator
from .sensor import SENSOR_KEYS

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: SJWaterConfigEntry) -> bool:
    """1.1 -> 1.2: drop the hour-based options; update interval now 2-24 h (default 6)."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options = {k: v for k, v in entry.options.items() if k in DEFAULT_OPTIONS}
        hours = float(options.get(CONF_SCAN_INTERVAL, DEFAULT_OPTIONS[CONF_SCAN_INTERVAL]))
        if hours == OLD_DEFAULT_SCAN_HOURS:
            hours = DEFAULT_OPTIONS[CONF_SCAN_INTERVAL]
        options[CONF_SCAN_INTERVAL] = max(MIN_SCAN_HOURS, min(MAX_SCAN_HOURS, hours))
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
        LOGGER.debug("Migrated %s to 1.2 with options %s", entry.title, options)
    return True


def _remove_retired_entities(hass: HomeAssistant, entry: SJWaterConfigEntry, account: str) -> None:
    """Remove entities that earlier versions created and this one no longer provides."""
    registry = er.async_get(hass)
    current = {f"{account}_{key}" for key in SENSOR_KEYS | BINARY_SENSOR_KEYS}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in current:
            LOGGER.info("Removing retired San Jose Water entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: SJWaterConfigEntry) -> bool:
    """Set up one San Jose Water account."""
    coordinator = SJWaterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _remove_retired_entities(hass, entry, coordinator.account)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SJWaterConfigEntry) -> bool:
    """Unload an account. Its imported statistics stay in the database."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
