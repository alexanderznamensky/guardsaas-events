from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import slugify
from .const import (
    ATTR_AUTOINVOICE_ENABLED,
    ATTR_AUTORENEWAL,
    ATTR_BONUS_FUNDS,
    ATTR_CAN_BE_RENEWED_FROM_BALANCE,
    ATTR_DAYS_LEFT,
    ATTR_DUE_DATE,
    ATTR_EXPIRATION_DAYS,
    ATTR_EXPIRATION_LABEL,
    ATTR_EXPIRING,
    ATTR_IS_EXPIRED,
    ATTR_MAX_PAYMENT,
    ATTR_MESSAGE,
    ATTR_MIN_ORG_PAYMENT,
    ATTR_MIN_PAYMENT,
    ATTR_MIN_PERSON_PAYMENT,
    ATTR_PAYMENTS_COUNT,
    ATTR_REAL_FUNDS,
    ATTR_RENEWAL_AVAILABLE,
    ATTR_RENEWAL_COST,
    DATA_COORDINATOR,
    DOMAIN,
    INTEGRATION_NAME,
    MANUFACTURER,
    ATTR_BLOCKED,
    ATTR_YEAR_COST,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    data = coordinator.data

    entities: list[SensorEntity] = [JinoBalanceSensor(coordinator, entry)]
    entities.extend(
        JinoDomainSensor(coordinator, entry, domain_info)
        for domain_info in data.get("jino", {}).get("domains", [])
    )
    entities.extend(
        NightscoutSensor(coordinator, entry, account_info, index)
        for index, account_info in enumerate(data.get("nightscout_easy", []))
    )

    async_add_entities(entities)


class BaseBillingEntity(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry


class JinoBalanceSensor(BaseBillingEntity):
    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = "RUB"
    _attr_name = "Balance"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_jino_balance"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_jino")},
            name="Jino",
            manufacturer=MANUFACTURER,
            model=INTEGRATION_NAME,
        )

    @property
    def native_value(self):
        value = self.coordinator.data.get("jino", {}).get("balance", {}).get("funds")
        if value is None:
            return None
        return round(float(value), 2)

    @property
    def extra_state_attributes(self):
        balance = self.coordinator.data.get("jino", {}).get("balance", {})
        return {
            ATTR_REAL_FUNDS: balance.get("real_funds"),
            ATTR_BONUS_FUNDS: balance.get("bonus_funds"),
            ATTR_PAYMENTS_COUNT: balance.get("payments_count"),
            ATTR_EXPIRATION_DAYS: balance.get("expiration_days"),
            ATTR_DUE_DATE: balance.get("expiration_date"),
            ATTR_EXPIRATION_LABEL: balance.get("expiration_label"),
            ATTR_MIN_PAYMENT: balance.get("min_payment"),
            ATTR_MIN_PERSON_PAYMENT: balance.get("min_person_payment"),
            ATTR_MIN_ORG_PAYMENT: balance.get("min_org_payment"),
            ATTR_MAX_PAYMENT: balance.get("max_payment"),
            ATTR_AUTOINVOICE_ENABLED: balance.get("autoinvoice_enabled"),
            ATTR_DAYS_LEFT: balance.get("days_left"),
            ATTR_MESSAGE: balance.get("message"),
            "execution_seconds": self.coordinator.data.get("execution_seconds"),
        }


class JinoDomainSensor(BaseBillingEntity):
    _attr_icon = "mdi:web"

    def __init__(self, coordinator, entry: ConfigEntry, domain_info: dict) -> None:
        super().__init__(coordinator, entry)
        self.domain_name = domain_info["domain"]
        self._slug = slugify(self.domain_name)
        self._attr_name = self.domain_name
        self._attr_unique_id = f"{entry.entry_id}_jino_domain_{self._slug}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_jino")},
            name="Jino",
            manufacturer=MANUFACTURER,
            model=INTEGRATION_NAME,
        )

    def _domain_data(self) -> dict:
        for item in self.coordinator.data.get("jino", {}).get("domains", []):
            if item.get("domain") == self.domain_name:
                return item
        return {}

    @property
    def available(self) -> bool:
        return bool(self._domain_data()) and super().available

    @property
    def native_value(self):
        return self._domain_data().get("expire_date")

    @property
    def extra_state_attributes(self):
        data = self._domain_data()
        return {
            ATTR_DUE_DATE: data.get("expire_date"),
            ATTR_AUTORENEWAL: data.get("autorenewal_enabled"),
            ATTR_IS_EXPIRED: data.get("is_expired"),
            ATTR_EXPIRING: data.get("expiring"),
            ATTR_RENEWAL_COST: data.get("renewal_cost"),
            ATTR_RENEWAL_AVAILABLE: data.get("renewal_available"),
            ATTR_CAN_BE_RENEWED_FROM_BALANCE: data.get("can_be_renewed_from_balance"),
            ATTR_DAYS_LEFT: data.get("days_left"),
            ATTR_MESSAGE: data.get("message"),
            "execution_seconds": self.coordinator.data.get("execution_seconds"),
        }


class NightscoutSensor(BaseBillingEntity):
    _attr_icon = "mdi:medical-bag"

    def __init__(self, coordinator, entry: ConfigEntry, account_info: dict, index: int) -> None:
        super().__init__(coordinator, entry)
        self._index = index
        self._name_source = account_info.get("name") or f"Nightscout {index + 1}"
        self._slug = slugify(self._name_source)
        self._attr_name = "Access"
        self._attr_unique_id = f"{entry.entry_id}_nightscout_{index + 1}_{self._slug}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_nightscout_{self._index + 1}")},
            name=self._name_source,
            manufacturer=MANUFACTURER,
            model="Nightscout Easy",
        )

    def _account_data(self) -> dict:
        items = self.coordinator.data.get("nightscout_easy", [])
        if 0 <= self._index < len(items):
            return items[self._index]
        return {}

    @property
    def available(self) -> bool:
        return bool(self._account_data()) and super().available

    @property
    def native_value(self):
        return self._account_data().get("expire_date")

    @property
    def extra_state_attributes(self):
        data = self._account_data()
        return {
            ATTR_DUE_DATE: data.get("expire_date"),
            ATTR_DAYS_LEFT: data.get("days_left"),
            ATTR_MESSAGE: data.get("message"),
            ATTR_BLOCKED: data.get("blocked"),
            ATTR_YEAR_COST: data.get("year_cost"),
            "name": data.get("name"),
            "execution_seconds": self.coordinator.data.get("execution_seconds"),
        }