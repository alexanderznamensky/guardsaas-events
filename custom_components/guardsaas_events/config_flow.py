import logging
import re
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession, TCPConnector
from aiohttp.resolver import ThreadedResolver
from bs4 import BeautifulSoup

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import DOMAIN, GUARDSAAS_BASE_URL

_LOGGER = logging.getLogger(__name__)

CONTROLLER_LIST_ENDPOINTS = (
    "/equipment/controller/list/export",
    "/equipment/controller/api/list/",
)


def _items_from_response(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "controllers", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if data.get("id") is not None:
            return [data]
    return []


def _field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item.get(name) not in (None, ""):
            return item.get(name)
    return None


def _controller_id(controller: dict[str, Any]) -> str | None:
    value = _field(controller, "id", "controller_id", "controllerid", "controllerId")
    return str(value) if value not in (None, "") else None


def _controller_token(controller: dict[str, Any]) -> str | None:
    value = _field(controller, "token1", "token_1", "open_token", "opendoor_token", "token")
    return str(value) if value not in (None, "") else None


def _controller_serial(controller: dict[str, Any]) -> str | None:
    value = _field(controller, "serial", "serial_number", "serialNumber", "sn")
    return str(value) if value not in (None, "") else None


def _controller_label(controller: dict[str, Any]) -> str:
    controller_id = _controller_id(controller)
    name = _field(controller, "name", "description", "title", "serial", "number")
    serial = _controller_serial(controller)

    parts = []
    if controller_id:
        parts.append(f"id={controller_id}")
    if name:
        parts.append(str(name))
    if serial and str(serial) != str(name):
        parts.append(f"serial={serial}")
    return ", ".join(parts) or str(controller)


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    elif value not in (None, ""):
        yield str(value)


def _phone_candidates(value: Any) -> set[str]:
    """Return normalized phone-like candidates from any nested structure."""
    result: set[str] = set()
    for text in _walk_values(value):
        for raw in re.findall(r"(?:\+?7|8)?[\s\-()]*(?:\d[\s\-()]*){10,11}", text):
            digits = re.sub(r"\D+", "", raw)
            if len(digits) == 11 and digits.startswith("8"):
                digits = "7" + digits[1:]
            if len(digits) >= 10:
                result.add(digits)
                result.add(digits[-10:])
    return result


def _find_auto_controller_for_object(
    obj: dict[str, Any], controllers: list[dict[str, Any]]
) -> dict[str, Any] | None:
    object_id = str(obj.get("id") or "")
    object_name = str(obj.get("name") or "")
    object_phones = _phone_candidates(obj)

    phone_matches = []
    if object_phones:
        for controller in controllers:
            if object_phones.intersection(_phone_candidates(controller)):
                phone_matches.append(controller)
        if len(phone_matches) == 1:
            return phone_matches[0]
        if len(phone_matches) > 1:
            _LOGGER.warning(
                "GuardSaaS: several controller phone matches for %s (%s): %s",
                object_name,
                object_id,
                "; ".join(_controller_label(item) for item in phone_matches),
            )
            return None

    id_fields = (
        "object_id", "objectid", "objectId", "object", "objectid_id",
        "place_id", "placeid", "building_id", "buildingid",
    )
    id_matches = []
    for controller in controllers:
        for field in id_fields:
            value = controller.get(field)
            if value is not None and str(value) == object_id:
                id_matches.append(controller)
                break
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = []
    normalized_object_name = re.sub(r"\s+", " ", object_name.lower()).strip()
    if normalized_object_name:
        for controller in controllers:
            for field in ("object_name", "objectName", "object", "name", "description", "title", "address"):
                value = controller.get(field)
                if not value:
                    continue
                normalized_value = re.sub(r"\s+", " ", str(value).lower()).strip()
                if normalized_value == normalized_object_name:
                    name_matches.append(controller)
                    break
    if len(name_matches) == 1:
        return name_matches[0]

    return None


def _build_object_controller_map(
    objects: list[dict[str, Any]], controllers: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for obj in objects:
        object_id = str(obj.get("id") or "")
        if not object_id:
            continue
        controller = _find_auto_controller_for_object(obj, controllers)
        if not controller:
            continue
        controller_id = _controller_id(controller)
        token = _controller_token(controller)
        if controller_id and token:
            mapping[object_id] = {
                "controller_id": controller_id,
                "controller_name": _controller_label(controller),
                "serial": _controller_serial(controller),
                "token1": token,
                "match_method": "auto",
            }
    return mapping


class GuardSaaSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return GuardSaaSOptionsFlow(config_entry)

    def __init__(self):
        self._username = None
        self._password = None
        self._object_list: list[dict[str, Any]] = []
        self._controller_list: list[dict[str, Any]] = []
        self._selected_objects: list[dict[str, Any]] = []
        self._object_options: dict[str, dict[str, Any]] = {}
        self._object_controller_map: dict[str, dict[str, Any]] = {}
        self._object_index = 0

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            self._username = user_input["_username"]
            self._password = user_input["_password"]
            try:
                self._object_list, self._controller_list = await self._fetch_lists(
                    self._username, self._password
                )
                return await self.async_step_select_object()
            except ValueError as err:
                _LOGGER.error("GuardSaaS auth/list error: %s", err)
                errors["base"] = "invalid_auth" if str(err) == "invalid_auth" else "cannot_connect"
            except Exception as err:
                _LOGGER.error("Failed to fetch GuardSaaS lists: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("_username"): str,
                vol.Required("_password"): str,
            }),
            errors=errors,
        )

    async def async_step_select_object(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            selected_names = user_input.get("target_objects") or []
            if isinstance(selected_names, str):
                selected_names = [selected_names]

            selected = [obj for obj in self._object_list if obj.get("name") in selected_names]
            if not selected:
                errors["target_objects"] = "invalid_selection"
            else:
                selected_ids = {str(obj.get("id")) for obj in selected}
                for entry in self._async_current_entries():
                    existing_ids = entry.data.get("object_ids") or []
                    if not existing_ids and entry.data.get("object_id") is not None:
                        existing_ids = [entry.data.get("object_id")]
                    if selected_ids.intersection({str(item) for item in existing_ids}):
                        errors["base"] = "already_configured"
                        break

                if not errors:
                    self._selected_objects = selected
                    self._object_controller_map = _build_object_controller_map(
                        self._selected_objects, self._controller_list
                    )
                    self._object_index = 0
                    return await self.async_step_object_options()

        options = [
            SelectOptionDict(value=str(obj.get("name")), label=str(obj.get("name")))
            for obj in self._object_list
            if obj.get("name") is not None
        ]
        return self.async_show_form(
            step_id="select_object",
            data_schema=vol.Schema({
                vol.Required("target_objects"): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        translation_key="target_objects",
                        sort=True,
                        multiple=True,
                        custom_value=False,
                    )
                )
            }),
            errors=errors,
        )

    async def async_step_object_options(self, user_input=None) -> FlowResult:
        current = self._selected_objects[self._object_index]
        object_id = str(current["id"])
        object_name = str(current["name"])

        if user_input is not None:
            self._object_options[object_id] = {
                "limit": user_input.get("limit", 25),
                "scan_interval": user_input.get("scan_interval", 1),
                "enabled": user_input.get("enabled", True),
            }
            self._object_index += 1
            if self._object_index < len(self._selected_objects):
                return await self.async_step_object_options()

            target_objects = [str(obj["name"]) for obj in self._selected_objects]
            object_ids = [str(obj["id"]) for obj in self._selected_objects]
            return self.async_create_entry(
                title="GuardSaaS",
                data={
                    "_username": self._username,
                    "_password": self._password,
                    "target_objects": target_objects,
                    "object_ids": object_ids,
                    "object_controller_map": self._object_controller_map,
                    "target_object": target_objects[0],
                    "object_id": object_ids[0],
                },
                options={"object_options": self._object_options},
            )

        return self.async_show_form(
            step_id="object_options",
            data_schema=vol.Schema({
                vol.Optional("limit", default=25): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional("scan_interval", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Optional("enabled", default=True): bool,
            }),
            errors={},
            description_placeholders={
                "current": self._object_index + 1,
                "total": len(self._selected_objects),
                "object_name": object_name,
            },
        )

    async def _fetch_lists(self, username: str, password: str):
        headers = {"User-Agent": "Mozilla/5.0"}
        connector = TCPConnector(resolver=ThreadedResolver())
        async with ClientSession(headers=headers, connector=connector) as session:
            await self._login(session, username, password)
            objects = await self._fetch_object_list(session)
            controllers = await self._fetch_controller_list(session)
            return objects, controllers

    async def _login(self, session: ClientSession, username: str, password: str):
        async with session.get(f"{GUARDSAAS_BASE_URL}/login") as resp:
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            token_input = soup.find("input", {"name": "_csrf_token"})
            if not token_input:
                raise Exception("CSRF token not found")
            csrf_token = token_input["value"]

        payload = {
            "_username": username,
            "_password": password,
            "_remember_me": "on",
            "_csrf_token": csrf_token,
        }
        async with session.post(f"{GUARDSAAS_BASE_URL}/login_check", data=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ValueError("invalid_auth")
            if "logout" not in text and "/login" in str(resp.url):
                raise ValueError("invalid_auth")

    async def _fetch_object_list(self, session: ClientSession):
        async with session.get(f"{GUARDSAAS_BASE_URL}/object/list/export") as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                raise ValueError("invalid_auth")
            if resp.status != 200:
                raise Exception(f"Failed to get object list: {resp.status}")
            json_data = await resp.json()
            items = _items_from_response(json_data)
            if not items:
                raise Exception("Unexpected object list format")
            return items

    async def _fetch_controller_list(self, session: ClientSession):
        last_error = None
        for endpoint in CONTROLLER_LIST_ENDPOINTS:
            try:
                async with session.get(
                    f"{GUARDSAAS_BASE_URL}{endpoint}",
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{GUARDSAAS_BASE_URL}/equipment/controller/list",
                    },
                ) as resp:
                    text = await resp.text()
                    if "/login" in str(resp.url) or "_csrf_token" in text:
                        last_error = f"{endpoint}: redirected to login"
                        continue
                    if resp.status != 200:
                        last_error = f"{endpoint}: HTTP {resp.status}"
                        continue
                    data = await resp.json()
                    items = _items_from_response(data)
                    if items:
                        return items
                    last_error = f"{endpoint}: empty controller list"
            except Exception as err:
                last_error = f"{endpoint}: {err}"
        _LOGGER.warning("GuardSaaS controller list unavailable: %s", last_error)
        return []


class GuardSaaSOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry
        self._object_index = 0
        self._target_objects = []
        self._object_ids = []
        self._object_options = {}
        self._object_controller_map = {}
        self._object_list = []
        self._controller_list = []

    async def async_step_init(self, user_input=None):
        data = {**self._entry.data, **(self._entry.options or {})}
        self._target_objects = data.get("target_objects") or [data.get("target_object")]
        self._object_ids = data.get("object_ids") or [data.get("object_id") or data.get("target_object")]
        self._object_options = dict(data.get("object_options") or {})
        self._object_controller_map = dict(data.get("object_controller_map") or {})
        self._object_index = 0

        try:
            flow = GuardSaaSConfigFlow()
            self._object_list, self._controller_list = await flow._fetch_lists(
                data["_username"], data["_password"]
            )
            selected_objects = []
            for object_id, object_name in zip(self._object_ids, self._target_objects):
                found = next(
                    (obj for obj in self._object_list if str(obj.get("id")) == str(object_id)),
                    None,
                )
                if found is None:
                    found = {"id": object_id, "name": object_name}
                selected_objects.append(found)
            self._object_controller_map.update(
                _build_object_controller_map(selected_objects, self._controller_list)
            )
        except Exception as err:
            _LOGGER.error("Failed to refresh GuardSaaS controller map in options flow: %s", err)

        return await self.async_step_object_options()

    async def async_step_object_options(self, user_input=None):
        object_id = str(self._object_ids[self._object_index])
        object_name = str(self._target_objects[self._object_index])
        current_options = self._object_options.get(object_id, {})

        if user_input is not None:
            self._object_options[object_id] = {
                "limit": user_input.get("limit", 25),
                "scan_interval": user_input.get("scan_interval", 1),
                "enabled": user_input.get("enabled", True),
            }
            self._object_index += 1
            if self._object_index < len(self._target_objects):
                return await self.async_step_object_options()

            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    "object_controller_map": self._object_controller_map,
                },
            )
            result = self.async_create_entry(
                title="",
                data={
                    **(self._entry.options or {}),
                    "object_options": self._object_options,
                },
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry.entry_id)
            )
            return result

        return self.async_show_form(
            step_id="object_options",
            data_schema=vol.Schema({
                vol.Optional("limit", default=current_options.get("limit", 25)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional("scan_interval", default=current_options.get("scan_interval", 1)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Optional("enabled", default=current_options.get("enabled", True)): bool,
            }),
            errors={},
            description_placeholders={
                "current": self._object_index + 1,
                "total": len(self._target_objects),
                "object_name": object_name,
            },
        )
