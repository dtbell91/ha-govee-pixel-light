"""Render content and push one-off effects to a light."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import payload
from .const import CONF_ENCODER, SIGNAL_PREVIEW
from .content import Prepared
from .effect_server import URL_PATH, async_get_store
from .iot import IotError

if TYPE_CHECKING:
    from . import GoveePixelConfigEntry
    from .api import PixelDevice

_LOGGER = logging.getLogger(__name__)


def effect_base_url(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> str:
    """Base URL the light uses to download files (must be reachable from the light; plain HTTP is fine)."""
    from .const import CONF_BASE_URL

    if base := entry.options.get(CONF_BASE_URL):
        return base.rstrip("/")
    api = hass.config.api
    scheme = "https" if api.use_ssl else "http"
    return f"{scheme}://{api.local_ip}:{api.port}"


def file_url(hass: HomeAssistant, entry: GoveePixelConfigEntry, key: str) -> str:
    return f"{effect_base_url(hass, entry)}{URL_PATH}/{key}.bin"


async def async_render(hass: HomeAssistant, entry: GoveePixelConfigEntry, dev: PixelDevice,
                       prepared: Prepared) -> tuple[bytes, list[payload.Frame]]:
    """Run the renderer and encode the effect the light downloads."""
    encoder = entry.options.get(CONF_ENCODER, payload.ENCODER_FRAMES)

    def run():
        frames = prepared.render(dev.width)
        return payload.encode_effect(frames, prepared.background, encoder, prepared.source), frames

    try:
        return await hass.async_add_executor_job(run)
    except (OSError, ValueError) as err:  # PIL.UnidentifiedImageError is an OSError
        raise ServiceValidationError(f"Cannot render {prepared.label}: {err}") from err


async def async_render_frames(hass: HomeAssistant, dev: PixelDevice, prepared: Prepared) -> list[payload.Frame]:
    """Run the renderer only (lights running pixeld take the frames as an image file)."""
    try:
        return await hass.async_add_executor_job(prepared.render, dev.width)
    except (OSError, ValueError) as err:
        raise ServiceValidationError(f"Cannot render {prepared.label}: {err}") from err


async def async_set_preview(hass: HomeAssistant, entry: GoveePixelConfigEntry, dev: PixelDevice,
                            frames: list[payload.Frame]) -> None:
    preview = await hass.async_add_executor_job(payload.preview_gif, frames)
    entry.runtime_data.previews[dev.device] = (preview, time.time())
    async_dispatcher_send(hass, SIGNAL_PREVIEW.format(dev.device))


async def async_push_effect(hass: HomeAssistant, entry: GoveePixelConfigEntry, dev: PixelDevice,
                            effect: bytes) -> bool | None:
    """Serve one effect and tell the light to show it. Raises if the light reports a failed download."""
    url = file_url(hass, entry, async_get_store(hass).add(effect))
    _LOGGER.debug("Sending %d bytes to %s via %s", len(effect), dev.name, url)
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
    return ok


async def async_show(hass: HomeAssistant, entry: GoveePixelConfigEntry, dev: PixelDevice,
                     prepared: Prepared) -> dict:
    """Render, push and preview one piece of content. Returns the per-device result."""
    from .pixeld import PixeldLink, encode_content

    if isinstance(link := entry.runtime_data.iot, PixeldLink):
        # Shown until the playlist resumes (the playlist controller sends the resume when a hold ends).
        frames = await async_render_frames(hass, dev, prepared)
        data = await hass.async_add_executor_job(encode_content, frames, prepared.source)
        try:
            await link.async_show(data, prepared.label)
        except IotError as err:
            raise HomeAssistantError(f"{dev.name}: {err}") from err
        await async_set_preview(hass, entry, dev, frames)
        return {"confirmed": True, "frames": len(frames), "bytes": len(data)}
    effect, frames = await async_render(hass, entry, dev, prepared)
    ok = await async_push_effect(hass, entry, dev, effect)
    await async_set_preview(hass, entry, dev, frames)
    return {"confirmed": bool(ok), "frames": len(frames), "bytes": len(effect)}
