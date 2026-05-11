from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, INTEGRATION_NAME, MANUFACTURER


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([JinoRefreshButton(coordinator, entry)])


class JinoRefreshButton(CoordinatorEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"
    _attr_name = "Refresh"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_jino_refresh"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_jino")},
            name="Jino",
            manufacturer=MANUFACTURER,
            model=INTEGRATION_NAME,
        )

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()