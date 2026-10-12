"""Config flow: Govee account login (with emailed verification code) and effect base URL option."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoveeApi, GoveeAuthError, GoveeError, GoveeNeedsCode
from .const import CONF_ACCOUNT_ID, CONF_CLIENT_ID, CONF_ACCOUNT_TOPIC, CONF_BASE_URL, CONF_CODE, CONF_TOKEN, DOMAIN


class GoveePixelConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._api: GoveeApi | None = None

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GoveePixelOptionsFlow()

    async def _login(self, code: str | None = None) -> tuple[ConfigFlowResult | None, str | None]:
        """Log in; returns (result, error key)."""
        assert self._api
        try:
            session = await self._api.login(code)
            devices = await self._api.devices()
        except GoveeNeedsCode:
            return self.async_show_form(step_id="code", data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
                                        description_placeholders={"email": self._api.email}), None
        except GoveeAuthError:
            return None, "invalid_auth"
        except GoveeError:
            return None, "cannot_connect"
        if not devices:
            return None, "no_devices"
        data = {CONF_EMAIL: self._api.email, CONF_PASSWORD: self._api.password, CONF_TOKEN: session.token,
                CONF_CLIENT_ID: self._api.client_id,
                CONF_ACCOUNT_ID: session.account_id, CONF_ACCOUNT_TOPIC: session.account_topic}
        await self.async_set_unique_id(session.account_id or self._api.email.lower())
        if self.source == "reauth":
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data), None
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._api.email, data=data), None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._api = GoveeApi(async_get_clientsession(self.hass), user_input[CONF_EMAIL].strip(),
                                 user_input[CONF_PASSWORD])
            result, error = await self._login()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}))

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            result, error = await self._login(user_input[CONF_CODE])
            if result:
                return result
            errors["base"] = "invalid_code" if error == "invalid_auth" else error
        return self.async_show_form(step_id="code", errors=errors,
                                    data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
                                    description_placeholders={"email": self._api.email if self._api else ""})

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            self._api = GoveeApi(async_get_clientsession(self.hass), entry.data[CONF_EMAIL],
                                 user_input[CONF_PASSWORD])
            result, error = await self._login()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                                    description_placeholders={"email": entry.data[CONF_EMAIL]})


class GoveePixelOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_BASE_URL: user_input.get(CONF_BASE_URL, "").strip()})
        current = self.config_entry.options.get(CONF_BASE_URL, "")
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_BASE_URL, description={"suggested_value": current}): str}))
