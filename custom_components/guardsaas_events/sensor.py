import logging
import re
from datetime import datetime
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

# sensor.py
async def async_setup_entry(hass, entry, async_add_entities):
    entity = GuardSaaSSensor(hass, entry)
    async_add_entities([entity])
    # -- Добавляем entity в список, если ещё нет --
    domain = "guardsaas_events"
    if domain not in hass.data:
        hass.data[domain] = {}
    if "entities" not in hass.data[domain]:
        hass.data[domain]["entities"] = []
    if entity not in hass.data[domain]["entities"]:
        hass.data[domain]["entities"].append(entity)
    # -- Сразу обновляем --
    await entity.async_update_ha_state(force_refresh=True)



class GuardSaaSSensor(Entity):
    """Representation of a GuardSaaS sensor."""

    def __init__(self, hass, entry):
        self._hass = hass
        # Сначала берем из options (если были изменены через UI), иначе из data
        data = {**entry.data, **(entry.options or {})}
        self._config = data
        self._state = None
        self._attrs = {}
        self._name = f"GuardSaaS - {self._config.get('target_object', 'Sensor')}"
        self._unique_id = f"guardsaas_{self._config.get('target_object','sensor').lower().replace(' ','_')}"
        self._available = True

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attrs

    @property
    def available(self):
        return self._available

    @property
    def icon(self):
        return "mdi:account-key"

    @property
    def should_poll(self):
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {("guardsaas_events", self._unique_id)},
            "name": self._name,
            "manufacturer": "GuardSaaS",
            "model": "Event Sensor",
        }

    async def async_update(self):
        """Update the sensor from GuardSaaS."""
        # Все сетевые операции — через executor!
        result = await self._hass.async_add_executor_job(self._fetch_data)
        if result:
            self._state = result.get("state", "Ошибка")
            self._attrs = result.get("attrs", {})
            self._available = True
        else:
            self._state = "Ошибка"
            self._attrs = {}
            self._available = False

    def _fetch_data(self):
        try:
            import requests
            from bs4 import BeautifulSoup

            CONFIG = {
                'base_url': 'https://app.guardsaas.ru',
                'credentials': {
                    '_username': self._config["_username"],
                    '_password': self._config["_password"],
                    '_remember_me': 'on'
                },
                'target_object': self._config["target_object"],
                'target_eventid': 4,
                'employee_url': 'https://app.guardsaas.ru/employee/list/export'
            }

            session = requests.Session()
            login_page = session.get(f"{CONFIG['base_url']}/login", timeout=20)
            soup = BeautifulSoup(login_page.text, 'html.parser')
            csrf_token = soup.find('input', {'name': '_csrf_token'})['value']

            auth_data = {**CONFIG['credentials'], '_csrf_token': csrf_token}
            session.post(f"{CONFIG['base_url']}/login_check", data=auth_data, timeout=20)
            response = session.get(f"{CONFIG['base_url']}/reports/events/export", timeout=20)
            data = response.json()
            current_time = datetime.now()
            valid_events = []
            for item in data.get('items', []):
                try:
                    if (item.get('object') == CONFIG['target_object'] and
                        item.get('eventid') == CONFIG['target_eventid']):
                        event_time = datetime.strptime(item['time'], '%Y-%m-%d %H:%M:%S')
                        if event_time <= current_time:
                            valid_events.append(item)
                except (KeyError, ValueError):
                    continue
            last_event = None
            if valid_events:
                valid_events.sort(key=lambda x: datetime.strptime(x['time'], '%Y-%m-%d %H:%M:%S'), reverse=True)
                last_event = valid_events[0]
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
                        match = re.search(r'[А-ЯA-Z][а-яa-zёЁ]+\s[А-ЯA-Z][а-яa-zёЁ]+\s[А-ЯA-Z][а-яa-zёЁ]+', raw_name)
                        clean_name = match.group(0) if match else raw_name
                        state = clean_name
                        attrs = {
                            "time": last_event.get("time"),
                            "number": emp.get("number"),
                            "department": emp.get("department"),
                            "position": emp.get("position")
                        }
                        return {"state": state, "attrs": attrs}
                    else:
                        return {"state": "Сотрудник не найден", "attrs": {}}
                except Exception as e:
                    return {"state": "Ошибка сотрудников", "attrs": {"error": str(e)}}
            else:
                return {"state": "Событий не найдено", "attrs": {}}
        except Exception as e:
            return {"state": "Ошибка", "attrs": {"error": str(e)}}
        finally:
            try:
                session.get(f"{CONFIG['base_url']}/logout", timeout=20)
            except Exception:
                pass
