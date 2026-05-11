from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BillingApiError,
    JinoCredentials,
    JinoDomainsClient,
    NightscoutEasyClient,
    parse_nightscout_accounts,
)
from .const import (
    CONF_JINO_LOGIN,
    CONF_JINO_PASSWORD,
    CONF_NIGHTSCOUT_ACCOUNTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


class ServiceBillingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = entry.options.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)

        super().__init__(
            hass=hass,
            logger=hass.data[DOMAIN]["logger"],
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=int(interval)),
            config_entry=entry,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.hass.async_add_executor_job(self._fetch_data)
        except BillingApiError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def _fetch_data(self) -> dict[str, Any]:
        started = time.time()

        jino_login = self.entry.data[CONF_JINO_LOGIN]
        jino_password = self.entry.data[CONF_JINO_PASSWORD]
        nightscout_raw = self.entry.options.get(
            CONF_NIGHTSCOUT_ACCOUNTS,
            self.entry.data.get(CONF_NIGHTSCOUT_ACCOUNTS, []),
        )
        accounts = parse_nightscout_accounts(nightscout_raw)

        jino = JinoDomainsClient(JinoCredentials(jino_login, jino_password))
        jino.authenticate()
        jino_data = jino.get_all()

        nightscout_data: list[dict[str, Any]] = []
        for account in accounts:
            client = NightscoutEasyClient(account)
            client.authenticate()
            nightscout_data.append(client.get_info())

        return {
            "execution_seconds": round(time.time() - started, 2),
            "jino": jino_data,
            "nightscout_easy": nightscout_data,
        }