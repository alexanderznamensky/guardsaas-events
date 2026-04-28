import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Setting up GuardSaaS entry: %s", entry.title)
    _LOGGER.debug("Entry DATA: %s", entry.data)
    _LOGGER.debug("Entry OPTIONS: %s", entry.options)
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Unloading GuardSaaS entry: %s", entry.title)
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.debug("Reloading GuardSaaS entry: %s", entry.title)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
