import logging
import re
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

_LOGGER = logging.getLogger(__name__)

def fetch_guardsaas_data(config):
    try:
        CONFIG = {
            'base_url': 'https://app.guardsaas.ru',
            'credentials': {
                '_username': config["_username"],
                '_password': config["_password"],
                '_remember_me': 'on'
            },
            'target_object': config["target_object"],
            'target_eventid': 4,
            'employee_url': 'https://app.guardsaas.ru/employee/list/export',
            'limit': int(config.get("limit", 25)),
        }

        session = requests.Session()
        login_page = session.get(f"{CONFIG['base_url']}/login", timeout=20)
        soup = BeautifulSoup(login_page.text, 'html.parser')
        csrf_token = soup.find('input', {'name': '_csrf_token'})['value']

        auth_data = {**CONFIG['credentials'], '_csrf_token': csrf_token}
        session.post(f"{CONFIG['base_url']}/login_check", data=auth_data, timeout=20)

        params = {"limit": CONFIG['limit']}
        response = session.get(f"{CONFIG['base_url']}/reports/events/export", params=params, timeout=20)
        data = response.json()
        current_time = datetime.now()
        items = data.get('items', [])

        # Фильтр по объекту
        by_object = [i for i in items if i.get('object') == CONFIG['target_object']]

        # Фильтр по eventid
        by_eventid = [i for i in by_object if i.get('eventid') == CONFIG['target_eventid']]

        # Фильтр по времени
        valid_events = []
        for item in by_eventid:
            try:
                event_time = datetime.strptime(item['time'], '%Y-%m-%d %H:%M:%S')
                if event_time <= current_time:
                    valid_events.append(item)
            except (KeyError, ValueError):
                _LOGGER.warning(f"Ошибка разбора времени в событии: {item}")
                continue

        last_event = None
        if valid_events:
            valid_events.sort(key=lambda x: datetime.strptime(x['time'], '%Y-%m-%d %H:%M:%S'), reverse=True)
            last_event = valid_events[0]
        else:
            _LOGGER.warning("Событий не найдено после всех фильтров.")

        if last_event:
            employeeid_from_event = last_event.get('employeeid')
            emp_response = session.get(CONFIG['employee_url'], timeout=20)
            try:
                emp_data = emp_response.json()
                if isinstance(emp_data, list):
                    items = emp_data
                elif isinstance(emp_data, dict) and 'items' in emp_data:
                    items = emp_data['items']
                else:
                    items = [emp_data]
                emp = next((e for e in items if str(e.get('id') or e.get('employeeid')) == str(employeeid_from_event)), None)
                if emp:
                    raw_name = emp.get("name") or last_event.get("employee") or ""
                    # Шаг 1. Убрать ведущие цифры или ***
                    raw_name = re.sub(r'^(?:\d+|\*{3})\s*', '', raw_name)
                    # Шаг 2. Убрать последние 12 символов (телефон, пробелы и пр.)
                    clean_name = raw_name[:-12].rstrip() if len(raw_name) > 12 else raw_name
                    # match = re.search(r'([А-ЯЁA-Z][а-яёa-zA-ZЁ\-]+(?:\s*\([А-ЯЁA-Z][а-яёa-zA-ZЁ\-]+\))?\s+[А-ЯЁA-Z][а-яёa-zA-ZЁ\-]+\s+[А-ЯЁA-Z][а-яёa-zA-ZЁ\-]+)', raw_name)
                    # clean_name = match.group(0) if match else raw_name
                    state = clean_name
                    attrs = {
                        "time": last_event.get("time"),
                        "number": emp.get("number"),
                        "department": emp.get("department"),
                        "position": emp.get("position"),
                        "comment": emp.get("comment")
                    }
                    _LOGGER.debug(f"Найден пользователь: {clean_name}")
                    return {"state": state, "attrs": attrs}
                else:
                    _LOGGER.warning(f"Пользователь не найден для employeeid: {employeeid_from_event}")
                    return {"state": "Пользователь не найден", "attrs": {}}
            except Exception as e:
                _LOGGER.error(f"Ошибка разбора пользователя: {e}")
                return {"state": "Ошибка пользователя", "attrs": {"error": str(e)}}
        else:
            return {"state": "Событий не найдено", "attrs": {}}
    except Exception as e:
        _LOGGER.error(f"Общая ошибка: {e}")
        return {"state": "Ошибка", "attrs": {"error": str(e)}}
    finally:
        try:
            session.get(f"{CONFIG['base_url']}/logout", timeout=20)
        except Exception:
            pass

async def async_setup_entry(hass, entry, async_add_entities):
    config = {**entry.data, **(entry.options or {})}
    scan_interval = int(config.get("scan_interval", 1))
    update_interval = timedelta(minutes=scan_interval)

    async def async_update_data():
        return await hass.async_add_executor_job(fetch_guardsaas_data, config)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"GuardSaaS Sensor ({config.get('target_object', 'Sensor')})",
        update_method=async_update_data,
        update_interval=update_interval,
    )

    await coordinator.async_config_entry_first_refresh()

    entity = GuardSaaSSensor(coordinator, config)
    async_add_entities([entity])

class GuardSaaSSensor(CoordinatorEntity):
    """Representation of a GuardSaaS sensor."""

    def __init__(self, coordinator, config):
        super().__init__(coordinator)
        self._config = config
        self._name = f"GuardSaaS - {self._config.get('target_object', 'Sensor')}"
        self._unique_id = f"guardsaas_{self._config.get('target_object','sensor').lower().replace(' ','_')}"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        return self.coordinator.data.get("state") if self.coordinator.data else "Ошибка"

    @property
    def extra_state_attributes(self):
        return self.coordinator.data.get("attrs", {}) if self.coordinator.data else {}

    @property
    def icon(self):
        return "mdi:account-key"

    @property
    def should_poll(self):
        return False

    @property
    def device_info(self):
        return {
            "identifiers": {("guardsaas_events", self._unique_id)},
            "name": self._name,
            "manufacturer": "GuardSaaS",
            "model": "Event Sensor",
        }
