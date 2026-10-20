"""Base entity for San Jose Water."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, BASE_URL, DOMAIN
from .coordinator import SJWaterCoordinator


class SJWaterEntity(CoordinatorEntity[SJWaterCoordinator]):
    """One entity on the account's "San Jose Water" service device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: SJWaterCoordinator, description: EntityDescription) -> None:
        """Bind the description and device."""
        super().__init__(coordinator)
        self.entity_description = description
        account = coordinator.account
        self._attr_unique_id = f"{account}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account)},
            name="San Jose Water",
            manufacturer="San Jose Water",
            model=f"Account ending {account[-4:]}",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=BASE_URL,
        )
