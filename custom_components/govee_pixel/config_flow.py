"""Config flow: Govee account login (with emailed verification code) or local MQTT, and options."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_NAME, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .api import GoveeApi, GoveeAuthError, GoveeError, GoveeNeedsCode
from .const import (
    CONF_ACCOUNT_ID, CONF_ACCOUNT_TOPIC, CONF_BASE_URL, CONF_CLIENT_ID, CONF_CODE, CONF_CONNECTION, CONF_DEVICE_ID,
    CONF_ENCODER, CONF_HW_VERSION, CONF_PLAYBACK, CONF_RECLAIM, CONF_REFRESH_MINUTES, CONF_RESUME, CONF_SKU,
    CONF_SW_VERSION, CONF_TOKEN, CONF_TOPIC_PREFIX, CONNECTION_LOCAL, CONNECTION_PIXELD, DEFAULT_REFRESH_MINUTES,
    DOMAIN, PLAYBACK_HA,
    PLAYBACK_LIGHT, RESUME_MODE_PACKET, RESUME_REPUSH,
)
from .iot import IotError
from .payload import ENCODER_AUTO, ENCODER_FRAMES

DEFAULT_LOCAL_NAME = "Govee Pixel Light"


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
        return self.async_show_menu(step_id="user", menu_options=["cloud", "local", "pixeld"])

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._api = GoveeApi(async_get_clientsession(self.hass), user_input[CONF_EMAIL].strip(),
                                 user_input[CONF_PASSWORD])
            result, error = await self._login()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(step_id="cloud", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}))

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A light running the local-MQTT custom firmware, bridged to Home Assistant's broker."""
        from .local_mqtt import DEFAULT_TOPIC_PREFIX, LocalMqttLink  # late import: needs the mqtt component

        errors = {}
        if user_input is not None:
            link = LocalMqttLink(self.hass, user_input[CONF_TOPIC_PREFIX])
            reply = None
            try:
                await link.async_start()
                reply = await link.async_status_reply(link.cmd_topic)
            except IotError:
                errors["base"] = "mqtt_unavailable"
            finally:
                await link.async_stop()
            if not errors and not (reply and reply.get("device")):
                errors["base"] = "no_reply"
            if not errors:
                device = reply["device"]
                await self.async_set_unique_id(device)
                self._abort_if_unique_id_configured()
                if any(device in e.runtime_data.devices for e in self._async_current_entries()
                       if e.state is ConfigEntryState.LOADED):
                    return self.async_abort(reason="device_in_cloud_entry")
                return self.async_create_entry(title=user_input[CONF_NAME].strip() or DEFAULT_LOCAL_NAME, data={
                    CONF_CONNECTION: CONNECTION_LOCAL, CONF_TOPIC_PREFIX: link.prefix, CONF_DEVICE_ID: device,
                    CONF_SKU: reply.get("sku") or "H8630", CONF_SW_VERSION: reply.get("softVersion", ""),
                    CONF_HW_VERSION: reply.get("wifiHardVersion", "")})
        defaults = user_input or {}
        return self.async_show_form(step_id="local", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_TOPIC_PREFIX, default=defaults.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)): str,
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_LOCAL_NAME)): str,
        }))

    async def _async_probe_pixeld(self, prefix: str) -> tuple[Any, str | None]:
        """Connect to a pixeld light under prefix: (link, error key)."""
        from .pixeld import PixeldLink

        link = PixeldLink(self.hass, prefix)
        try:
            await link.async_start(timeout=5)
        except IotError:
            return link, "mqtt_unavailable"
        finally:
            await link.async_stop()
        if not link.info or not link.info.get("mac"):
            return link, "no_pixeld"
        return link, None

    async def async_step_pixeld(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A light running pixeld (the custom firmware's own display daemon) on Home Assistant's broker."""
        from .local_mqtt import DEFAULT_TOPIC_PREFIX

        errors = {}
        if user_input is not None:
            link, error = await self._async_probe_pixeld(user_input[CONF_TOPIC_PREFIX])
            if error:
                errors["base"] = error
            else:
                device = link.info["mac"].upper()
                await self.async_set_unique_id(device)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_NAME].strip() or DEFAULT_LOCAL_NAME, data={
                    CONF_CONNECTION: CONNECTION_PIXELD, CONF_TOPIC_PREFIX: link.prefix, CONF_DEVICE_ID: device,
                    CONF_SKU: link.info.get("sku") or "H8630", CONF_SW_VERSION: link.info.get("base", ""),
                    CONF_HW_VERSION: ""})
        defaults = user_input or {}
        return self.async_show_form(step_id="pixeld", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_TOPIC_PREFIX, default=defaults.get(CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX)): str,
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_LOCAL_NAME)): str,
        }))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Switch a local light between Govee's protocol (govee_iot, bridged) and pixeld, keeping the device,
        its entities and history."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_CONNECTION) not in (CONNECTION_LOCAL, CONNECTION_PIXELD):
            return self.async_abort(reason="not_local")
        errors = {}
        if user_input is not None:
            connection = user_input[CONF_CONNECTION]
            prefix = user_input[CONF_TOPIC_PREFIX]
            if connection == CONNECTION_PIXELD:
                link, error = await self._async_probe_pixeld(prefix)
                if error:
                    errors["base"] = error
                else:
                    prefix = link.prefix
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CONNECTION: connection, CONF_TOPIC_PREFIX: prefix})
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_CONNECTION, default=entry.data.get(CONF_CONNECTION)): SelectSelector(
                SelectSelectorConfig(options=[CONNECTION_LOCAL, CONNECTION_PIXELD], translation_key=CONF_CONNECTION)),
            vol.Required(CONF_TOPIC_PREFIX, default=entry.data.get(CONF_TOPIC_PREFIX)): str,
        }))

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
            user_input[CONF_BASE_URL] = user_input.get(CONF_BASE_URL, "").strip()
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_BASE_URL, description={"suggested_value": o.get(CONF_BASE_URL, "")}): str,
            vol.Required(CONF_PLAYBACK, default=o.get(CONF_PLAYBACK, PLAYBACK_LIGHT)): SelectSelector(
                SelectSelectorConfig(options=[PLAYBACK_LIGHT, PLAYBACK_HA], translation_key=CONF_PLAYBACK)),
            vol.Required(CONF_RESUME, default=o.get(CONF_RESUME, RESUME_REPUSH)): SelectSelector(
                SelectSelectorConfig(options=[RESUME_REPUSH, RESUME_MODE_PACKET], translation_key=CONF_RESUME)),
            vol.Required(CONF_RECLAIM, default=o.get(CONF_RECLAIM, True)): bool,
            vol.Required(CONF_REFRESH_MINUTES, default=o.get(CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES)):
                vol.All(vol.Coerce(int), vol.Range(1, 1440)),
            vol.Required(CONF_ENCODER, default=o.get(CONF_ENCODER, ENCODER_FRAMES)): SelectSelector(
                SelectSelectorConfig(options=[ENCODER_FRAMES, ENCODER_AUTO], translation_key=CONF_ENCODER)),
        }))
