import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from .const import DOMAIN, GUARDSAAS_BASE_URL

_LOGGER = logging.getLogger(__name__)

DEFAULT_ACCOUNT_SCAN_INTERVAL = 60

LAST_EVENT_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="guardsaas_sensor",
    translation_key="guardsaas_sensor",
    name="GuardSaaS Sensor",
)

BALANCE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="balance",
    translation_key="balance",
    name="GuardSaaS Balance",
)

PAYMENT_DATE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="payment_date",
    translation_key="payment_date",
    name="GuardSaaS Payment Date",
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).lower()).strip("_")


def _login(session: requests.Session, username: str, password: str) -> None:
    """Login to GuardSaaS using the web form and CSRF token."""
    login_page = session.get(f"{GUARDSAAS_BASE_URL}/login", timeout=20)
    login_page.raise_for_status()

    soup = BeautifulSoup(login_page.text, "html.parser")
    token_input = soup.find("input", {"name": "_csrf_token"})
    if token_input is None or not token_input.get("value"):
        raise RuntimeError("CSRF token not found")

    auth_data = {
        "_username": username,
        "_password": password,
        "_remember_me": "on",
        "_csrf_token": token_input["value"],
    }

    login_response = session.post(
        f"{GUARDSAAS_BASE_URL}/login_check",
        data=auth_data,
        timeout=20,
    )
    login_response.raise_for_status()

    if "logout" not in login_response.text and "/login" in login_response.url:
        raise RuntimeError("invalid_auth")


def _logout(session: requests.Session) -> None:
    try:
        session.get(f"{GUARDSAAS_BASE_URL}/logout", timeout=20)
    except Exception:
        pass


def _extract_first_number(value: str):
    if not value:
        return None

    match = re.search(r"-?\d+[\.,]\d+", value)
    if match:
        return float(match.group().replace(",", "."))

    match = re.search(r"-?\d+", value)
    if match:
        return float(match.group())

    return None




def _format_payment_date(value) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _day_word(n: int) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def _time_to_pay(name: str, due_date: str, service_name: str = "GuardSaaS") -> tuple[str | None, int | None]:
    if not due_date:
        return None, None
    try:
        due = datetime.strptime(due_date, "%d.%m.%Y").date()
    except ValueError:
        return None, None
    days_left = (due - datetime.now().date()).days
    dword = _day_word(days_left)
    if days_left == 0:
        msg = f"Сегодня срок оплаты {service_name} - {name}!"
    elif 0 < days_left <= 5:
        msg = f"Через {days_left} {dword} нужно оплатить {service_name} - {name}!"
    elif days_left < 0:
        msg = f"Просрочена оплата {service_name} - {name}!!!"
    else:
        msg = f"Все в порядке! Оплачивать {service_name} - {name} нужно через {days_left} {dword}."
    return msg, days_left


def _parse_payment_date_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")

    # Original selector from the working script.
    selected = soup.select("div:nth-child(9) > div.item-content")
    for elem in selected:
        value = elem.get_text(strip=True)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value

    # Fallback: first ISO date on the page.
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", soup.get_text(" ", strip=True))
    if match:
        return match.group(0)

    return None


def fetch_guardsaas_data(config):
    session = None
    try:
        CONFIG = {
            "credentials": {
                "_username": config["_username"],
                "_password": config["_password"],
                "_remember_me": "on",
            },
            "target_object": config["target_object"],
            "target_eventid": 4,
            "limit": int(config.get("limit", 25)),
        }

        session = requests.Session()
        _login(session, CONFIG["credentials"]["_username"], CONFIG["credentials"]["_password"])

        params = {"limit": CONFIG["limit"]}
        response = session.get(
            f"{GUARDSAAS_BASE_URL}/reports/events/export",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        current_time = datetime.now()
        items = data.get("items", [])

        by_object = [i for i in items if i.get("object") == CONFIG["target_object"]]
        by_eventid = [i for i in by_object if i.get("eventid") == CONFIG["target_eventid"]]

        valid_events = []
        for item in by_eventid:
            try:
                event_time = datetime.strptime(item["time"], "%Y-%m-%d %H:%M:%S")
                if event_time <= current_time:
                    valid_events.append(item)
            except (KeyError, ValueError):
                _LOGGER.warning("Ошибка разбора времени в событии: %s", item)
                continue

        last_event = None
        if valid_events:
            valid_events.sort(
                key=lambda x: datetime.strptime(x["time"], "%Y-%m-%d %H:%M:%S"),
                reverse=True,
            )
            last_event = valid_events[0]
        else:
            _LOGGER.warning(
                "Событий не найдено после всех фильтров для объекта %s.",
                CONFIG["target_object"],
            )

        if last_event:
            employeeid_from_event = last_event.get("employeeid")
            emp_response = session.get(
                f"{GUARDSAAS_BASE_URL}/employee/list/export",
                timeout=20,
            )
            emp_response.raise_for_status()
            try:
                emp_data = emp_response.json()
                if isinstance(emp_data, list):
                    items = emp_data
                elif isinstance(emp_data, dict) and "items" in emp_data:
                    items = emp_data["items"]
                else:
                    items = [emp_data]
                emp = next(
                    (
                        e
                        for e in items
                        if str(e.get("id") or e.get("employeeid")) == str(employeeid_from_event)
                    ),
                    None,
                )
                if emp:
                    raw_name = emp.get("name") or last_event.get("employee") or ""
                    raw_name = re.sub(r"^(?:\d+|\*{3})\s*", "", raw_name)
                    clean_name = raw_name[:-12].rstrip() if len(raw_name) > 12 else raw_name
                    state = clean_name
                    attrs = {
                        "time": last_event.get("time"),
                        "number": emp.get("number"),
                        "department": emp.get("department"),
                        "position": emp.get("position"),
                        "comment": emp.get("comment"),
                    }
                    _LOGGER.debug("Найден пользователь: %s", clean_name)
                    return {"state": state, "attrs": attrs}
                else:
                    _LOGGER.warning(
                        "Пользователь не найден для employeeid: %s",
                        employeeid_from_event,
                    )
                    return {"state": "Пользователь не найден", "attrs": {}}
            except Exception as e:
                _LOGGER.error("Ошибка разбора пользователя: %s", e)
                return {"state": "Ошибка пользователя", "attrs": {"error": str(e)}}
        else:
            return {"state": "Событий не найдено", "attrs": {}}
    except Exception as e:
        _LOGGER.error("Общая ошибка: %s", e)
        if str(e) == "invalid_auth":
            return {"state": "Ошибка авторизации", "attrs": {"error": "Неверные учётные данные"}}
        return {"state": "Ошибка", "attrs": {"error": str(e)}}
    finally:
        if session is not None:
            _logout(session)


def fetch_guardsaas_account_data(config):
    """Fetch account balance and payment date."""
    session = None
    try:
        session = requests.Session()
        _login(session, config["_username"], config["_password"])

        account_response = session.get(f"{GUARDSAAS_BASE_URL}/account", timeout=20)
        account_response.raise_for_status()
        account_soup = BeautifulSoup(account_response.text, "html.parser")

        balance_spans = account_soup.select("td.user-balance > span")
        all_balance_texts = [span.get_text(strip=True) for span in balance_spans]
        raw_balance = all_balance_texts[0] if all_balance_texts else ""
        parsed_balance = _extract_first_number(raw_balance or " ".join(all_balance_texts))
        converted_balance = "{:.2f}".format(round(parsed_balance * 60, 2)) if parsed_balance is not None else None

        index_response = session.get(GUARDSAAS_BASE_URL, timeout=20)
        index_response.raise_for_status()
        payment_date = _parse_payment_date_from_html(index_response.text)

        formatted_payment_date = _format_payment_date(payment_date)
        payment_message, days_left = _time_to_pay(
            "аккаунт",
            formatted_payment_date,
            "GuardSaaS",
        )

        return {
            "balance": {
                "state": converted_balance,
                "attrs": {
                    "raw_balance": raw_balance,
                    "all_balances": all_balance_texts,
                    "conversion": "first_found_balance * 60",
                },
            },
            "payment_date": {
                "state": payment_date,
                "attrs": {
                    "raw_date": payment_date,
                    "days_left": days_left,
                    "message": payment_message,
                },
            },
        }
    except Exception as e:
        _LOGGER.error("Ошибка получения баланса/даты GuardSaaS: %s", e)
        error_text = "Неверные учётные данные" if str(e) == "invalid_auth" else str(e)
        return {
            "balance": {"state": None, "attrs": {"error": error_text}},
            "payment_date": {"state": None, "attrs": {"error": error_text, "message": None}},
        }
    finally:
        if session is not None:
            _logout(session)


async def async_setup_entry(hass, entry, async_add_entities):
    config = {**entry.data, **(entry.options or {})}

    target_objects = config.get("target_objects")
    object_ids = config.get("object_ids")
    object_options = config.get("object_options") or {}

    # Backward compatibility with old entries where only one object was saved.
    if not target_objects:
        target_objects = [config.get("target_object")]
    if not object_ids:
        object_ids = [config.get("object_id") or config.get("target_object")]

    entities = []
    scan_intervals = []

    for target_object, object_id in zip(target_objects, object_ids):
        if not target_object:
            continue

        object_id = str(object_id)
        per_object_options = object_options.get(object_id, {})

        # Backward compatibility: if this entry was created before per-object
        # options existed, reuse the old shared options.
        limit = int(per_object_options.get("limit", config.get("limit", 25)))
        scan_interval = int(per_object_options.get("scan_interval", config.get("scan_interval", 1)))
        enabled = bool(per_object_options.get("enabled", config.get("enabled", True)))
        scan_intervals.append(scan_interval)
        update_interval = timedelta(minutes=scan_interval)

        object_config = {
            **config,
            "target_object": target_object,
            "object_id": object_id,
            "limit": limit,
            "scan_interval": scan_interval,
            "enabled": enabled,
        }

        async def async_update_data(object_config=object_config):
            if not object_config.get("enabled", True):
                _LOGGER.debug(
                    "Sensor update skipped because it is disabled: %s",
                    object_config.get("target_object"),
                )
                return {"state": "Отключено", "attrs": {}}
            return await hass.async_add_executor_job(fetch_guardsaas_data, object_config)

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"GuardSaaS ({target_object})",
            update_method=async_update_data,
            update_interval=update_interval,
            config_entry=entry,
        )

        await coordinator.async_config_entry_first_refresh()
        entities.append(GuardSaaSLastEventSensor(coordinator, object_config))

    account_scan_interval = max(
        DEFAULT_ACCOUNT_SCAN_INTERVAL,
        min(scan_intervals) if scan_intervals else DEFAULT_ACCOUNT_SCAN_INTERVAL,
    )

    async def async_update_account_data():
        return await hass.async_add_executor_job(fetch_guardsaas_account_data, config)

    account_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="GuardSaaS account",
        update_method=async_update_account_data,
        update_interval=timedelta(minutes=account_scan_interval),
        config_entry=entry,
    )

    await account_coordinator.async_config_entry_first_refresh()
    entities.extend([
        GuardSaaSBalanceSensor(account_coordinator, config),
        GuardSaaSPaymentDateSensor(account_coordinator, config),
    ])

    async_add_entities(entities)


class GuardSaaSLastEventSensor(CoordinatorEntity, SensorEntity):
    """Representation of a GuardSaaS last event sensor."""

    def __init__(self, coordinator, config):
        super().__init__(coordinator)
        self._config = config

        self.entity_description = LAST_EVENT_SENSOR_DESCRIPTION
        self._attr_translation_key = "guardsaas_sensor"

        target_object = self._config.get("target_object", "Sensor")
        object_id = self._config.get("object_id") or target_object

        self._name = f"GuardSaaS - {target_object}"
        self._unique_id = f"guardsaas_{_slug(object_id)}"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        if not self._config.get("enabled", True):
            return "Отключено"
        if not self.coordinator.data:
            return "Нет данных"
        return self.coordinator.data.get("state", "Ошибка")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data or not isinstance(data, dict):
            return {}

        attrs = data.get("attrs", {})
        return {
            "Время": attrs.get("time"),
            "Квартира/Помещение": attrs.get("number"),
            "Статус": attrs.get("department"),
            "Телефон": attrs.get("position"),
            "Автомобиль": attrs.get("comment"),
            "limit": self._config.get("limit"),
            "scan_interval": self._config.get("scan_interval"),
            "enabled": self._config.get("enabled"),
        }

    @property
    def icon(self):
        return "mdi:account-key"

    @property
    def should_poll(self):
        return False

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": self._name,
            "manufacturer": "GuardSaaS",
            "model": "Event Sensor",
        }


class GuardSaaSAccountSensor(CoordinatorEntity, SensorEntity):
    """Base account-level GuardSaaS sensor."""

    sensor_key: str

    def __init__(self, coordinator, config):
        super().__init__(coordinator)
        self._config = config

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        data = self.coordinator.data or {}
        sensor_data = data.get(self.sensor_key) or {}
        return sensor_data.get("state") is not None

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        sensor_data = data.get(self.sensor_key) or {}
        return sensor_data.get("state")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        sensor_data = data.get(self.sensor_key) or {}
        return sensor_data.get("attrs") or {}

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"guardsaas_account_{self._config.get('_username')}")},
            "name": "GuardSaaS Account",
            "manufacturer": "GuardSaaS",
            "model": "Account",
        }


class GuardSaaSBalanceSensor(GuardSaaSAccountSensor):
    sensor_key = "balance"

    def __init__(self, coordinator, config):
        super().__init__(coordinator, config)
        self.entity_description = BALANCE_SENSOR_DESCRIPTION
        self._attr_translation_key = "balance"
        entry_part = _slug(config.get("_username", "account"))
        self._unique_id = f"guardsaas_{entry_part}_balance"
        self._name = "GuardSaaS - Баланс"
        self._attr_native_unit_of_measurement = "RUB"
        self._attr_suggested_display_precision = 2
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        sensor_data = data.get(self.sensor_key) or {}
        value = sensor_data.get("state")
        if value is None:
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    @property
    def icon(self):
        return "mdi:cash"


class GuardSaaSPaymentDateSensor(GuardSaaSAccountSensor):
    sensor_key = "payment_date"

    def __init__(self, coordinator, config):
        super().__init__(coordinator, config)
        self.entity_description = PAYMENT_DATE_SENSOR_DESCRIPTION
        self._attr_translation_key = "payment_date"
        entry_part = _slug(config.get("_username", "account"))
        self._unique_id = f"guardsaas_{entry_part}_payment_date"
        self._name = "GuardSaaS - Дата оплаты"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        sensor_data = data.get(self.sensor_key) or {}
        return _format_payment_date(sensor_data.get("state"))

    @property
    def icon(self):
        return "mdi:calendar-clock"
