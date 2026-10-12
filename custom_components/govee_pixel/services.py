"""govee_pixel.show_image service."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import animations, payload
from .const import (
    ATTR_ANIMATION, ATTR_BACKGROUND, ATTR_COLOR, ATTR_CONDITION, ATTR_DEVICE_ID, ATTR_FIT, ATTR_FORECAST,
    ATTR_FRAME_DURATION, ATTR_IMAGE_ENTITY, ATTR_IMAGE_PATH, ATTR_IMAGE_URL, ATTR_MAX_FRAMES, ATTR_MESSAGE,
    ATTR_RESAMPLE, ATTR_SIZE, ATTR_TEMPERATURE, ATTR_TEMPERATURE_UNIT, ATTR_WEATHER_ENTITY, DOMAIN,
    MAX_SOURCE_BYTES, SERVICE_SHOW_ANIMATION, SERVICE_SHOW_IMAGE, SERVICE_SHOW_TEXT, SERVICE_SHOW_WEATHER,
)
from .effect_server import async_get_store
from .iot import IotError

if TYPE_CHECKING:
    from . import GoveePixelConfigEntry

_LOGGER = logging.getLogger(__name__)

SIGNAL_PREVIEW = f"{DOMAIN}_preview_{{}}"

SOURCES = (ATTR_IMAGE_PATH, ATTR_IMAGE_URL, ATTR_IMAGE_ENTITY)

RGB = vol.All(vol.Coerce(tuple), vol.ExactSequence((cv.byte, cv.byte, cv.byte)))
TARGET = {vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string])}

SHOW_IMAGE_SCHEMA = vol.Schema({
    **TARGET,
    vol.Exclusive(ATTR_IMAGE_PATH, "source"): cv.string,
    vol.Exclusive(ATTR_IMAGE_URL, "source"): cv.url,
    vol.Exclusive(ATTR_IMAGE_ENTITY, "source"): cv.entity_id,
    vol.Optional(ATTR_FIT, default=payload.FIT_CONTAIN): vol.In(
        [payload.FIT_CONTAIN, payload.FIT_COVER, payload.FIT_STRETCH]),
    vol.Optional(ATTR_RESAMPLE, default="lanczos"): vol.In(list(payload.RESAMPLE)),
    vol.Optional(ATTR_BACKGROUND, default=[0, 0, 0]): RGB,
    vol.Optional(ATTR_FRAME_DURATION): vol.All(
        vol.Coerce(int), vol.Range(payload.MIN_DURATION_MS, payload.MAX_DURATION_MS)),
    vol.Optional(ATTR_MAX_FRAMES, default=payload.MAX_FRAMES): vol.All(
        vol.Coerce(int), vol.Range(1, payload.MAX_FRAMES)),
})


SHOW_WEATHER_SCHEMA = vol.Schema({
    **TARGET,
    vol.Optional(ATTR_WEATHER_ENTITY): cv.entity_domain("weather"),
    vol.Optional(ATTR_CONDITION): cv.string,
    vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
    vol.Optional(ATTR_TEMPERATURE_UNIT): vol.In(["°C", "°F"]),
    vol.Optional(ATTR_FORECAST, default=False): cv.boolean,
})

SHOW_TEXT_SCHEMA = vol.Schema({
    **TARGET,
    vol.Required(ATTR_MESSAGE): cv.string,
    vol.Optional(ATTR_COLOR): RGB,
    vol.Optional(ATTR_BACKGROUND, default=[0, 0, 0]): RGB,
    vol.Optional(ATTR_SIZE, default="large"): vol.In(["large", "small"]),
    vol.Optional(ATTR_FRAME_DURATION, default=70): vol.All(
        vol.Coerce(int), vol.Range(payload.MIN_DURATION_MS, 1000)),
})

SHOW_ANIMATION_SCHEMA = vol.Schema({
    **TARGET,
    vol.Required(ATTR_ANIMATION): vol.In(list(animations.SCENES)),
})


async def _load_source(hass: HomeAssistant, call: ServiceCall) -> bytes:
    data = call.data
    if path := data.get(ATTR_IMAGE_PATH):
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"{path} is not in allowlist_external_dirs (files under /config/www and /media are allowed by default)")
        try:
            return await hass.async_add_executor_job(Path(path).read_bytes)
        except OSError as err:
            raise ServiceValidationError(f"Cannot read {path}: {err}") from err
    if url := data.get(ATTR_IMAGE_URL):
        try:
            async with async_get_clientsession(hass).get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                r.raise_for_status()
                body = await r.content.read(MAX_SOURCE_BYTES + 1)
        except aiohttp.ClientError as err:
            raise HomeAssistantError(f"Cannot fetch {url}: {err}") from err
        if len(body) > MAX_SOURCE_BYTES:
            raise ServiceValidationError(f"{url} is larger than {MAX_SOURCE_BYTES} bytes")
        return body
    if entity_id := data.get(ATTR_IMAGE_ENTITY):
        domain = entity_id.split(".", 1)[0]
        if domain == "camera":
            from homeassistant.components.camera import async_get_image
        elif domain == "image":
            from homeassistant.components.image import async_get_image
        else:
            raise ServiceValidationError(f"{entity_id} is not a camera or image entity")
        return (await async_get_image(hass, entity_id)).content
    raise ServiceValidationError(f"Provide one of: {', '.join(SOURCES)}")


def _resolve_devices(hass: HomeAssistant, device_ids: list[str]) -> list[tuple[GoveePixelConfigEntry, str]]:
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


async def _async_send(call: ServiceCall, render: Callable[[int], list[payload.Frame]],
                      background: tuple[int, int, int] = (0, 0, 0)) -> ServiceResponse:
    """Render frames for each target light (in the executor), serve them and trigger the download."""
    from . import effect_url  # late import: avoids a cycle with __init__

    hass = call.hass
    targets = _resolve_devices(hass, call.data[ATTR_DEVICE_ID])
    store = async_get_store(hass)
    results = {}
    for entry, govee_id in targets:
        dev = entry.runtime_data.devices[govee_id]
        try:
            frames = await hass.async_add_executor_job(render, dev.width)
            effect = await hass.async_add_executor_job(payload.build_effect, frames, background)
        except (OSError, ValueError) as err:  # PIL.UnidentifiedImageError is an OSError
            raise ServiceValidationError(f"Cannot render image: {err}") from err
        url = effect_url(hass, entry, store.add(effect))
        _LOGGER.debug("Sending %d frame(s), %d bytes to %s via %s", len(frames), len(effect), dev.name, url)
        try:
            ok = await entry.runtime_data.iot.async_pt_url(dev.topic, url)
        except IotError as err:
            raise HomeAssistantError(f"{dev.name}: {err}") from err
        if ok is False:
            raise HomeAssistantError(
                f"{dev.name} could not download {url}. Check that the light can reach Home Assistant at "
                "that address (set 'Effect base URL' in the integration options if not).")
        if ok is None:
            _LOGGER.warning("%s did not confirm the image (no reply)", dev.name)
        preview = await hass.async_add_executor_job(payload.preview_gif, frames)
        entry.runtime_data.previews[govee_id] = (preview, time.time())
        async_dispatcher_send(hass, SIGNAL_PREVIEW.format(govee_id))
        results[dev.name] = {"confirmed": bool(ok), "frames": len(frames), "bytes": len(effect)}
    return {"devices": results}


def _generated(render: Callable[[], animations.Frames], max_frames: int = payload.MAX_FRAMES):
    """Adapt a 32x32 animation renderer to the target matrix size."""
    def run(size: int) -> list[payload.Frame]:
        images, ms = render()
        return payload.frames_from_images(((im, ms) for im in images), size=size, fit=payload.FIT_STRETCH,
                                          resample="nearest", max_frames=max_frames)
    return run


async def _async_show_image(call: ServiceCall) -> ServiceResponse:
    source = await _load_source(call.hass, call)
    background = tuple(call.data[ATTR_BACKGROUND])
    return await _async_send(call, lambda size: payload.frames_from_image(
        source, size=size, fit=call.data[ATTR_FIT], resample=call.data[ATTR_RESAMPLE], background=background,
        duration=call.data.get(ATTR_FRAME_DURATION), max_frames=call.data[ATTR_MAX_FRAMES]), background)


async def _async_show_weather(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    data = call.data
    condition, temperature = data.get(ATTR_CONDITION), data.get(ATTR_TEMPERATURE)
    unit = data.get(ATTR_TEMPERATURE_UNIT)
    entity_id = data.get(ATTR_WEATHER_ENTITY)
    if entity_id:
        if (state := hass.states.get(entity_id)) is None:
            raise ServiceValidationError(f"{entity_id} not found")
        condition = condition or state.state
        if temperature is None:
            temperature = state.attributes.get("temperature")
        unit = unit or state.attributes.get("temperature_unit")
    unit = unit or hass.config.units.temperature_unit

    if data[ATTR_FORECAST]:
        if not entity_id:
            raise ServiceValidationError("A forecast needs weather_entity")
        try:
            resp = await hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": entity_id, "type": "daily"},
                blocking=True, return_response=True)
        except HomeAssistantError as err:
            raise HomeAssistantError(f"{entity_id} has no daily forecast: {err}") from err
        days = [(f.get("condition", ""), f["temperature"], f.get("templow"))
                for f in (resp or {}).get(entity_id, {}).get("forecast", [])[:3] if f.get("temperature") is not None]
        if not days:
            raise HomeAssistantError(f"{entity_id} returned no daily forecast")
        return await _async_send(call, _generated(lambda: animations.forecast(days, unit)))

    if not condition or temperature is None:
        raise ServiceValidationError("Give weather_entity, or both condition and temperature")
    try:
        temperature = float(temperature)
    except (TypeError, ValueError) as err:
        raise ServiceValidationError(f"Temperature {temperature!r} is not a number") from err
    return await _async_send(call, _generated(lambda: animations.weather(condition, temperature, unit)))


async def _async_show_text(call: ServiceCall) -> ServiceResponse:
    data = call.data
    color = tuple(data[ATTR_COLOR]) if ATTR_COLOR in data else None
    background = tuple(data[ATTR_BACKGROUND])
    return await _async_send(call, _generated(lambda: animations.scrolling_text(
        data[ATTR_MESSAGE], color, background, data[ATTR_SIZE] == "large", data[ATTR_FRAME_DURATION])),
        background)


async def _async_show_animation(call: ServiceCall) -> ServiceResponse:
    name = call.data[ATTR_ANIMATION]
    return await _async_send(call, _generated(lambda: animations.scene(name)))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for service, handler, schema in (
        (SERVICE_SHOW_IMAGE, _async_show_image, SHOW_IMAGE_SCHEMA),
        (SERVICE_SHOW_WEATHER, _async_show_weather, SHOW_WEATHER_SCHEMA),
        (SERVICE_SHOW_TEXT, _async_show_text, SHOW_TEXT_SCHEMA),
        (SERVICE_SHOW_ANIMATION, _async_show_animation, SHOW_ANIMATION_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, service, handler, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL)
