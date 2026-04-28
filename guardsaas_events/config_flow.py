import logging
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


class GuardSaaSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return GuardSaaSOptionsFlow(config_entry)

    def __init__(self):
        self._username = None
        self._password = None
        self._object_list = []
        self._selected_object = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            self._username = user_input["_username"]
            self._password = user_input["_password"]

            try:
                self._object_list = await self._fetch_object_list(
                    self._username, self._password
                )
                return await self.async_step_select_object()
            except Exception as e:
                _LOGGER.error("Failed to fetch object list: %s", e)
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
            selected = next(
                (obj for obj in self._object_list if obj["name"] == user_input["target_object"]),
                None
            )
            if selected is None:
                errors["target_object"] = "invalid_selection"
            else:
                for entry in self._async_current_entries():
                    if entry.data.get("object_id") == selected["id"]:
                        errors["base"] = "already_configured"
                        break

                if not errors:
                    self._selected_object = selected
                    return await self.async_step_advanced_options()

        options = [
            SelectOptionDict(value=obj["name"], label=obj["name"])
            for obj in self._object_list
        ]

        return self.async_show_form(
            step_id="select_object",
            data_schema=vol.Schema({
                vol.Required("target_object"): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        translation_key="target_object",
                        sort=True,
                        custom_value=False,
                    )
                )
            }),
            errors=errors,
        )

    async def async_step_advanced_options(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title=self._selected_object["name"],
                data={
                    "_username": self._username,
                    "_password": self._password,
                    "target_object": self._selected_object["name"],
                    "object_id": self._selected_object["id"],
                },
                options={
                    "limit": user_input.get("limit", 25),
                    "scan_interval": user_input.get("scan_interval", 1),
                    "enabled": user_input.get("enabled", True),
                }
            )

        return self.async_show_form(
            step_id="advanced_options",
            data_schema=vol.Schema({
                vol.Optional("limit",         default=25): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Optional("scan_interval", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Optional("enabled",       default=True): bool,
            }),
            errors=errors,
        )

    async def _fetch_object_list(self, username: str, password: str):
        headers = {"User-Agent": "Mozilla/5.0"}
        connector = TCPConnector(resolver=ThreadedResolver())

        async with ClientSession(headers=headers, connector=connector) as session:
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
                if resp.status != 200:
                    raise ValueError("invalid_auth")

            async with session.get(f"{GUARDSAAS_BASE_URL}/object/list/export") as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    raise ValueError("invalid_auth")

                if resp.status != 200:
                    raise Exception(f"Failed to get object list: {resp.status}")

                json_data = await resp.json()
                if "items" not in json_data or not isinstance(json_data["items"], list):
                    raise Exception("Unexpected object list format")

                return json_data["items"]


class GuardSaaSOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry

    async def async_step_init(self, user_input=None):
        errors = {}
        data = {**self._entry.data, **(self._entry.options or {})}

        if user_input is not None:
            _LOGGER.debug("Options Flow — USER INPUT: %s", user_input)
            # Сохраняем изменения
            result = self.async_create_entry(title="", data=user_input)
            # Перезагружаем конфигурационную запись
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry.entry_id)
            )
            # Перезагрузка устройства/объекта
            self.hass.bus.async_fire(
                f"guard_saas_reload_{self._entry.entry_id}",
                {"entry_id": self._entry.entry_id}
            )

            # # Updating the configuration
            # self.hass.config_entries.async_update_entry(self._config_entry)

            # # Reload integration
            # self.hass.async_create_task(
            #     self.hass.config_entries.async_reload(self._config_entry.entry_id)
            # )

            return result

        target_name = data.get("target_object", "объект")
        description = f"Измените параметры GuardSaaS — {target_name}"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
            vol.Optional("limit",         default=data.get("limit", 25)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
            vol.Optional("scan_interval", default=data.get("scan_interval", 1)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440 )),
            vol.Optional("enabled",       default=data.get("enabled", True)): bool,
            }),
            description_placeholders={"description": description},
        )

