from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import BillingApiError, JinoCredentials, JinoDomainsClient
from .const import (
    CONF_JINO_LOGIN,
    CONF_JINO_PASSWORD,
    CONF_NIGHTSCOUT_ACCOUNTS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

CONF_NS_LOGIN = "nightscout_login"
CONF_NS_PASSWORD = "nightscout_password"
CONF_USE_NIGHTSCOUT_AUTH = "use_nightscout_auth"
CONF_ADD_ANOTHER = "add_another"


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_JINO_LOGIN, default=defaults.get(CONF_JINO_LOGIN, "")): str,
            vol.Required(CONF_JINO_PASSWORD, default=defaults.get(CONF_JINO_PASSWORD, "")): str,
            vol.Optional(
                CONF_SCAN_INTERVAL_MINUTES,
                default=defaults.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES),
            ): NumberSelector(
                NumberSelectorConfig(min=5, max=1440, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


def _nightscout_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_USE_NIGHTSCOUT_AUTH,
                default=defaults.get(CONF_USE_NIGHTSCOUT_AUTH, False),
            ): bool,
            vol.Optional(CONF_NS_LOGIN, default=defaults.get(CONF_NS_LOGIN, "")): str,
            vol.Optional(CONF_NS_PASSWORD, default=defaults.get(CONF_NS_PASSWORD, "")): str,
            vol.Optional(CONF_ADD_ANOTHER, default=defaults.get(CONF_ADD_ANOTHER, False)): bool,
        }
    )


async def _async_validate_jino_credentials(hass, login: str, password: str) -> None:
    def _validate() -> None:
        client = JinoDomainsClient(JinoCredentials(login=login, password=password))
        client.authenticate()
        client.get_balance_info()

    await hass.async_add_executor_job(_validate)


class JinoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._jino_login: str = ""
        self._jino_password: str = ""
        self._scan_interval: int = DEFAULT_SCAN_INTERVAL_MINUTES
        self._nightscout_accounts: list[dict[str, str]] = []
        self._reconfigure_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            login = user_input[CONF_JINO_LOGIN].strip()
            password = user_input[CONF_JINO_PASSWORD]
            scan_interval = int(
                user_input.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
            )

            try:
                await _async_validate_jino_credentials(self.hass, login, password)
            except BillingApiError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                self._jino_login = login
                self._jino_password = password
                self._scan_interval = scan_interval

                await self.async_set_unique_id(self._jino_login.lower())
                self._abort_if_unique_id_configured()

                return await self.async_step_nightscout_account()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_nightscout_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            use_auth = bool(user_input.get(CONF_USE_NIGHTSCOUT_AUTH, False))
            login = user_input.get(CONF_NS_LOGIN, "").strip()
            password = user_input.get(CONF_NS_PASSWORD, "")

            has_any_credentials = bool(login or password)
            has_full_credentials = bool(login and password)

            if use_auth and not login:
                errors[CONF_NS_LOGIN] = "required"

            if use_auth and not password:
                errors[CONF_NS_PASSWORD] = "required"

            if has_any_credentials and not login:
                errors[CONF_NS_LOGIN] = "required"

            if has_any_credentials and not password:
                errors[CONF_NS_PASSWORD] = "required"

            if not errors:
                if has_full_credentials:
                    self._nightscout_accounts.append(
                        {
                            "login": login,
                            "password": password,
                        }
                    )

                if user_input.get(CONF_ADD_ANOTHER):
                    return self.async_show_form(
                        step_id="nightscout_account",
                        data_schema=_nightscout_schema(),
                        errors={},
                    )

                return self._create_entry()

        return self.async_show_form(
            step_id="nightscout_account",
            data_schema=_nightscout_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            login = user_input[CONF_JINO_LOGIN].strip()
            password = user_input[CONF_JINO_PASSWORD]
            scan_interval = int(
                user_input.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
            )

            try:
                await _async_validate_jino_credentials(self.hass, login, password)
            except BillingApiError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                self._jino_login = login
                self._jino_password = password
                self._scan_interval = scan_interval
                self._nightscout_accounts = []
                self._reconfigure_entry = entry
                return await self.async_step_reconfigure_nightscout_account()

        defaults = {
            CONF_JINO_LOGIN: entry.data.get(CONF_JINO_LOGIN, ""),
            CONF_JINO_PASSWORD: entry.data.get(CONF_JINO_PASSWORD, ""),
            CONF_SCAN_INTERVAL_MINUTES: entry.options.get(
                CONF_SCAN_INTERVAL_MINUTES,
                DEFAULT_SCAN_INTERVAL_MINUTES,
            ),
        }

        if user_input is not None:
            defaults = {
                CONF_JINO_LOGIN: user_input.get(CONF_JINO_LOGIN, ""),
                CONF_JINO_PASSWORD: user_input.get(CONF_JINO_PASSWORD, ""),
                CONF_SCAN_INTERVAL_MINUTES: user_input.get(
                    CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            }

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(defaults),
            errors=errors,
        )

    async def async_step_reconfigure_nightscout_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            use_auth = bool(user_input.get(CONF_USE_NIGHTSCOUT_AUTH, False))
            login = user_input.get(CONF_NS_LOGIN, "").strip()
            password = user_input.get(CONF_NS_PASSWORD, "")

            has_any_credentials = bool(login or password)
            has_full_credentials = bool(login and password)

            if use_auth and not login:
                errors[CONF_NS_LOGIN] = "required"

            if use_auth and not password:
                errors[CONF_NS_PASSWORD] = "required"

            if has_any_credentials and not login:
                errors[CONF_NS_LOGIN] = "required"

            if has_any_credentials and not password:
                errors[CONF_NS_PASSWORD] = "required"

            if not errors:
                if has_full_credentials:
                    self._nightscout_accounts.append(
                        {
                            "login": login,
                            "password": password,
                        }
                    )

                if user_input.get(CONF_ADD_ANOTHER):
                    return self.async_show_form(
                        step_id="reconfigure_nightscout_account",
                        data_schema=_nightscout_schema(),
                        errors={},
                    )

                entry = self._reconfigure_entry
                self.hass.config_entries.async_update_entry(
                    entry,
                    title=self._jino_login,
                    data={
                        **entry.data,
                        CONF_JINO_LOGIN: self._jino_login,
                        CONF_JINO_PASSWORD: self._jino_password,
                        CONF_NIGHTSCOUT_ACCOUNTS: self._nightscout_accounts,
                    },
                    options={
                        **entry.options,
                        CONF_SCAN_INTERVAL_MINUTES: self._scan_interval,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure_nightscout_account",
            data_schema=_nightscout_schema(user_input),
            errors=errors,
        )

    def _create_entry(self) -> FlowResult:
        return self.async_create_entry(
            title=self._jino_login,
            data={
                CONF_JINO_LOGIN: self._jino_login,
                CONF_JINO_PASSWORD: self._jino_password,
                CONF_NIGHTSCOUT_ACCOUNTS: self._nightscout_accounts,
            },
            options={
                CONF_SCAN_INTERVAL_MINUTES: self._scan_interval,
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return JinoOptionsFlow(config_entry)


class JinoOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input.get(CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES)
                    ),
                },
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=self._config_entry.options.get(
                        CONF_SCAN_INTERVAL_MINUTES,
                        DEFAULT_SCAN_INTERVAL_MINUTES,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=1440, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors={})