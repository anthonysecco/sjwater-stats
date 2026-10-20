"""San Jose Water: hourly water use, conservation and leak signals from SJWaterHUB."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SJWaterConfigEntry, SJWaterCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SJWaterConfigEntry) -> bool:
    """Set up one San Jose Water account."""
    coordinator = SJWaterCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SJWaterConfigEntry) -> bool:
    """Unload an account. Its imported statistics stay in the database."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
