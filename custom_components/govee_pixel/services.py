"""Actions: show_image / show_weather / show_text / show_animation and the playlist_* actions."""
from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import content, looplay
from .const import (
    ATTR_DEVICE_ID, ATTR_DURATION, ATTR_ITEM_TYPE, ATTR_NAME, ATTR_OPTIONS, ATTR_POSITION, ATTR_SHOW_IF,
    DEFAULT_INTERRUPT_SECONDS,
    DEFAULT_ITEM_SECONDS, DOMAIN, SERVICE_PLAYLIST_ADD, SERVICE_PLAYLIST_CLEAR, SERVICE_PLAYLIST_REFRESH,
    SERVICE_PLAYLIST_REMOVE, SERVICE_PLAYLIST_RESUME, SERVICE_SHOW_ANIMATION, SERVICE_SHOW_IMAGE, SERVICE_SHOW_TEXT,
    SERVICE_SHOW_WEATHER,
)
from .display import async_show

if TYPE_CHECKING:
    from . import GoveePixelConfigEntry
    from .playlist import PlaylistController

TARGET = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}
HOLD = {vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(0, 86400))}

SHOW_SERVICES = {
    SERVICE_SHOW_IMAGE: content.KIND_IMAGE,
    SERVICE_SHOW_WEATHER: content.KIND_WEATHER,
    SERVICE_SHOW_TEXT: content.KIND_TEXT,
    SERVICE_SHOW_ANIMATION: content.KIND_ANIMATION,
}


def _show_schema(kind: str):
    schema = vol.Schema({**TARGET, **HOLD, **content.OPTIONS[kind]})
    check = content.CHECKS.get(kind)

    def validate(data):
        data = schema(data)
        if check:
            options = {k: v for k, v in data.items() if k not in (ATTR_DEVICE_ID, ATTR_DURATION)}
            try:
                check(options)
            except vol.Invalid as err:
                raise vol.Invalid(str(err)) from err
        return data
    return validate


PLAYLIST_ADD_SCHEMA = vol.Schema({
    **TARGET,
    vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(1, 64)),
    vol.Required(ATTR_ITEM_TYPE): vol.In(list(content.OPTIONS)),
    vol.Optional(ATTR_OPTIONS, default=dict): dict,
    vol.Optional(ATTR_DURATION, default=DEFAULT_ITEM_SECONDS): vol.All(vol.Coerce(int), vol.Range(1, 65535)),
    vol.Optional(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(0, looplay.MAX_ITEMS - 1)),
    vol.Optional(ATTR_SHOW_IF): cv.string,
})
PLAYLIST_REMOVE_SCHEMA = vol.Schema({**TARGET, vol.Required(ATTR_NAME): cv.string})
TARGET_SCHEMA = vol.Schema(TARGET)


def _resolve(hass: HomeAssistant, device_ids: list[str]) -> list[tuple[GoveePixelConfigEntry, str]]:
    dev_reg = dr.async_get(hass)
    out = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        govee_id = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None) if device else None
        entry = next((e for e in hass.config_entries.async_entries(DOMAIN)
                      if device and e.entry_id in device.config_entries), None)
        if govee_id is None or entry is None:
            raise ServiceValidationError(f"{device_id} is not a Govee Pixel Light device")
        if entry.state is not ConfigEntryState.LOADED or govee_id not in entry.runtime_data.devices:
            raise HomeAssistantError(f"{device.name} is not available")
        out.append((entry, govee_id))
    return out


def _controllers(call: ServiceCall) -> list[PlaylistController]:
    return [entry.runtime_data.playlists[gid] for entry, gid in _resolve(call.hass, call.data[ATTR_DEVICE_ID])]


async def _async_show(call: ServiceCall, kind: str) -> ServiceResponse:
    hass = call.hass
    options = {k: v for k, v in call.data.items() if k not in (ATTR_DEVICE_ID, ATTR_DURATION)}
    results = {}
    for entry, govee_id in _resolve(hass, call.data[ATTR_DEVICE_ID]):
        dev = entry.runtime_data.devices[govee_id]
        controller = entry.runtime_data.playlists[govee_id]
        prepared = await content.async_prepare(hass, kind, options)
        hold = call.data.get(ATTR_DURATION, DEFAULT_INTERRUPT_SECONDS)
        results[dev.name] = await controller.async_interrupt(
            lambda dev=dev, prepared=prepared, entry=entry: async_show(hass, entry, dev, prepared), hold)
    return {"devices": results}


async def _async_playlist_add(call: ServiceCall) -> None:
    for c in _controllers(call):
        await c.async_add(call.data[ATTR_NAME], call.data[ATTR_ITEM_TYPE], call.data[ATTR_OPTIONS],
                          call.data[ATTR_DURATION], call.data.get(ATTR_POSITION), call.data.get(ATTR_SHOW_IF))


async def _async_playlist_remove(call: ServiceCall) -> None:
    for c in _controllers(call):
        await c.async_remove(call.data[ATTR_NAME])


async def _async_playlist_clear(call: ServiceCall) -> None:
    for c in _controllers(call):
        await c.async_clear()


async def _async_playlist_resume(call: ServiceCall) -> None:
    for c in _controllers(call):
        await c.async_resume()


async def _async_playlist_refresh(call: ServiceCall) -> None:
    for c in _controllers(call):
        if c.active:
            await c.async_push(force=True)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for service, kind in SHOW_SERVICES.items():
        async def handler(call: ServiceCall, kind=kind) -> ServiceResponse:
            return await _async_show(call, kind)
        hass.services.async_register(DOMAIN, service, handler, schema=_show_schema(kind),
                                     supports_response=SupportsResponse.OPTIONAL)
    for service, handler, schema in (
        (SERVICE_PLAYLIST_ADD, _async_playlist_add, PLAYLIST_ADD_SCHEMA),
        (SERVICE_PLAYLIST_REMOVE, _async_playlist_remove, PLAYLIST_REMOVE_SCHEMA),
        (SERVICE_PLAYLIST_CLEAR, _async_playlist_clear, TARGET_SCHEMA),
        (SERVICE_PLAYLIST_RESUME, _async_playlist_resume, TARGET_SCHEMA),
        (SERVICE_PLAYLIST_REFRESH, _async_playlist_refresh, TARGET_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, service, handler, schema=schema)
