"""What can be shown: image / weather / text / animation.

Each kind has a voluptuous schema for its options (shared by the show_* actions and playlist
items) and is turned into a renderer at display time, so playlist items such as weather are
redrawn with fresh data every time they come round.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import animations, payload
from .const import (
    ATTR_ANIMATION, ATTR_BACKGROUND, ATTR_COLOR, ATTR_CONDITION, ATTR_FIT, ATTR_FORECAST, ATTR_FRAME_DURATION,
    ATTR_IMAGE_ENTITY, ATTR_IMAGE_PATH, ATTR_IMAGE_URL, ATTR_MAX_FRAMES, ATTR_MESSAGE, ATTR_RESAMPLE, ATTR_SIZE,
    ATTR_TEMPERATURE, ATTR_TEMPERATURE_UNIT, ATTR_WEATHER_ENTITY, MAX_SOURCE_BYTES,
)

KIND_IMAGE = "image"
KIND_WEATHER = "weather"
KIND_TEXT = "text"
KIND_ANIMATION = "animation"

RGB = vol.All(vol.Coerce(tuple), vol.ExactSequence((cv.byte, cv.byte, cv.byte)))

IMAGE_OPTIONS = {
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
}

WEATHER_OPTIONS = {
    vol.Optional(ATTR_WEATHER_ENTITY): cv.entity_domain("weather"),
    vol.Optional(ATTR_CONDITION): cv.string,
    vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
    vol.Optional(ATTR_TEMPERATURE_UNIT): vol.In(["°C", "°F"]),
    vol.Optional(ATTR_FORECAST, default=False): cv.boolean,
}

TEXT_OPTIONS = {
    vol.Required(ATTR_MESSAGE): cv.string,
    vol.Optional(ATTR_COLOR): RGB,
    vol.Optional(ATTR_BACKGROUND, default=[0, 0, 0]): RGB,
    vol.Optional(ATTR_SIZE, default="large"): vol.In(["large", "small"]),
    vol.Optional(ATTR_FRAME_DURATION, default=70): vol.All(
        vol.Coerce(int), vol.Range(payload.MIN_DURATION_MS, 1000)),
}

ANIMATION_OPTIONS = {
    vol.Required(ATTR_ANIMATION): vol.In(list(animations.SCENES)),
}

OPTIONS = {
    KIND_IMAGE: IMAGE_OPTIONS,
    KIND_WEATHER: WEATHER_OPTIONS,
    KIND_TEXT: TEXT_OPTIONS,
    KIND_ANIMATION: ANIMATION_OPTIONS,
}


def _check_image(data: dict) -> dict:
    if not any(k in data for k in (ATTR_IMAGE_PATH, ATTR_IMAGE_URL, ATTR_IMAGE_ENTITY)):
        raise vol.Invalid("give one of image_path, image_url or image_entity")
    return data


def _check_weather(data: dict) -> dict:
    if data.get(ATTR_FORECAST) and ATTR_WEATHER_ENTITY not in data:
        raise vol.Invalid("a forecast needs weather_entity")
    if ATTR_WEATHER_ENTITY not in data and (ATTR_CONDITION not in data or ATTR_TEMPERATURE not in data):
        raise vol.Invalid("give weather_entity, or both condition and temperature")
    return data


CHECKS: dict[str, Callable[[dict], dict]] = {KIND_IMAGE: _check_image, KIND_WEATHER: _check_weather}


def options_schema(kind: str) -> vol.Schema:
    """Schema for one kind's options (no device_id), with cross-field checks."""
    schema = vol.Schema(OPTIONS[kind])
    return vol.All(schema, CHECKS[kind]) if kind in CHECKS else schema


Render = Callable[[int], list[payload.Frame]]


@dataclass
class Prepared:
    render: Render  # matrix size -> frames (blocking, run in the executor)
    background: tuple[int, int, int]
    label: str
    source: bytes | None = None  # original file, for sending a GIF unchanged when possible


def _generated(render: Callable[[], animations.Frames]) -> Render:
    """Adapt a 32x32 animation renderer to the target matrix size."""
    def run(size: int) -> list[payload.Frame]:
        images, ms = render()
        return payload.frames_from_images(((im, ms) for im in images), size=size, fit=payload.FIT_STRETCH,
                                          resample="nearest")
    return run


async def _load_image(hass: HomeAssistant, data: dict) -> bytes:
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
    entity_id = data[ATTR_IMAGE_ENTITY]
    domain = entity_id.split(".", 1)[0]
    if domain == "camera":
        from homeassistant.components.camera import async_get_image
    elif domain == "image":
        from homeassistant.components.image import async_get_image
    else:
        raise ServiceValidationError(f"{entity_id} is not a camera or image entity")
    return (await async_get_image(hass, entity_id)).content


async def _prepare_image(hass: HomeAssistant, data: dict) -> Prepared:
    source = await _load_image(hass, data)
    background = tuple(data[ATTR_BACKGROUND])
    label = data.get(ATTR_IMAGE_PATH) or data.get(ATTR_IMAGE_URL) or data.get(ATTR_IMAGE_ENTITY)
    untouched = ATTR_FRAME_DURATION not in data and data[ATTR_MAX_FRAMES] == payload.MAX_FRAMES
    return Prepared(lambda size: payload.frames_from_image(
        source, size=size, fit=data[ATTR_FIT], resample=data[ATTR_RESAMPLE], background=background,
        duration=data.get(ATTR_FRAME_DURATION), max_frames=data[ATTR_MAX_FRAMES]), background, f"image {label}",
        source if untouched else None)


async def _prepare_weather(hass: HomeAssistant, data: dict) -> Prepared:
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

    if data.get(ATTR_FORECAST):
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
        return Prepared(_generated(lambda: animations.forecast(days, unit)), (0, 0, 0), "forecast")

    if not condition or condition in ("unavailable", "unknown") or temperature is None:
        raise HomeAssistantError(f"No current weather available ({entity_id or 'condition/temperature'})")
    try:
        temperature = float(temperature)
    except (TypeError, ValueError) as err:
        raise ServiceValidationError(f"Temperature {temperature!r} is not a number") from err
    return Prepared(_generated(lambda: animations.weather(condition, temperature, unit)), (0, 0, 0),
                    f"weather {condition} {round(temperature)}")


async def _prepare_text(hass: HomeAssistant, data: dict) -> Prepared:
    color = tuple(data[ATTR_COLOR]) if ATTR_COLOR in data else None
    background = tuple(data[ATTR_BACKGROUND])
    return Prepared(_generated(lambda: animations.scrolling_text(
        data[ATTR_MESSAGE], color, background, data[ATTR_SIZE] == "large", data[ATTR_FRAME_DURATION])),
        background, f"text {data[ATTR_MESSAGE]!r}")


async def _prepare_animation(hass: HomeAssistant, data: dict) -> Prepared:
    name = data[ATTR_ANIMATION]
    return Prepared(_generated(lambda: animations.scene(name)), (0, 0, 0), f"animation {name}")


PREPARE = {
    KIND_IMAGE: _prepare_image,
    KIND_WEATHER: _prepare_weather,
    KIND_TEXT: _prepare_text,
    KIND_ANIMATION: _prepare_animation,
}


async def async_prepare(hass: HomeAssistant, kind: str, data: dict[str, Any]) -> Prepared:
    """Fetch whatever the content needs (file, URL, entity state, forecast) and return its renderer."""
    return await PREPARE[kind](hass, data)
