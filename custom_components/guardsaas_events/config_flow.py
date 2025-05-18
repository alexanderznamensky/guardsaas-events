import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN

class GuardSaaSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            return self.async_create_entry(title=user_input["target_object"], data=user_input)

        data_schema = vol.Schema({
            vol.Required("_username"): str,
            vol.Required("_password"): str,
            vol.Required("target_object"): str,
            vol.Optional("limit",         default=25): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
            vol.Optional("scan_interval", default=1):  vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        })

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        return GuardSaaSOptionsFlow(config_entry)


class GuardSaaSOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry

    async def async_step_init(self, user_input=None):
        errors = {}
        data = {**self._entry.data, **(self._entry.options or {})}

        if user_input is not None:
            result = self.async_create_entry(title="", data=user_input)
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry.entry_id)
            )
            return result

        options_schema = vol.Schema({
            vol.Required("_username",     default=data.get("_username", "")): str,
            vol.Required("_password",     default=data.get("_password", "")): str,
            vol.Required("target_object", default=data.get("target_object", "")): str,
            vol.Optional("limit",         default=data.get("limit", 25)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
            vol.Optional("scan_interval", default=data.get("scan_interval", 1)): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440 )),
        })

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
