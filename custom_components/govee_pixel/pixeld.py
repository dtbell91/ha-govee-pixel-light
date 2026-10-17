"""Link to a light running pixeld (the custom firmware's own display daemon) over Home Assistant's MQTT broker.

pixeld draws the panel itself and rotates the playlist on the light. Content travels as image bytes over MQTT,
addressed by SHA-256: the light caches it, so playlist edits only transfer new items and nothing restarts.

Topics under the prefix (default `govee/h8630/`):

    px/status          <- online / offline (retained, last will)
    px/info            <- {"mac", "sku", "size", "firmware", "base", "pixeld"} (retained)
    px/state           <- {"on", "brightness", "showing", "item", "playlist_items", "paused"} (retained)
    px/set             -> {"on", "brightness", "resume", "next"} (any subset)
    px/show            -> {"key", "hold", "name"}  (hold 0 = until resume)
    px/playlist        -> {"items": [{"name", "key", "duration"}]} (retained)
    px/need            <- {"keys": [...]}  content the light is missing; answered on px/content/<key>
    px/content/<key>   -> PNG / GIF bytes
    telemetry, button, jack <- as for the telemetry layer (see telemetry.py)
    px/update/install  -> {"url", "sha256"}: download and install a pixeld build (not retained; the build itself
                          is served over HTTP, as brokers limit message size); px/update/rollback -> previous
    px/update/state    <- {"state", "version", "detail"} (retained): installing, restarting, trial, ok,
                          rolled_back, failed
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from PIL import Image

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from . import payload
from .iot import IotError
from .local_mqtt import normalize_prefix
from .telemetry import DeviceTelemetry

_LOGGER = logging.getLogger(__name__)

RECENT_CONTENT = 32  # one-off images kept to answer the light's requests


def _shared_palette(images: list[Image.Image]) -> list[Image.Image]:
    """Quantize all frames to one palette without dithering, so a pixel that doesn't change between frames
    keeps exactly its colour (per-frame palettes and dithering made still parts, e.g. album art under
    scrolling text, flicker)."""
    w, h = images[0].size
    sheet = Image.new("RGB", (w, h * len(images)))
    for i, im in enumerate(images):
        sheet.paste(im, (0, i * h))
    palette = sheet.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]


def encode_content(frames: list[payload.Frame], source: bytes | None = None) -> bytes:
    """Frames -> bytes pixeld decodes: PNG for a still, GIF (one shared palette, no dithering) for an
    animation; a source GIF already at the panel size is sent unchanged."""
    size = frames[0].image.width
    if source is not None and len(frames) > 1 and payload.gif_passthrough(source, size) is not None:
        return source
    buf = io.BytesIO()
    if len(frames) == 1:
        frames[0].image.convert("RGB").save(buf, format="PNG", optimize=True)
    else:
        images = _shared_palette([f.image.convert("RGB") for f in frames])
        images[0].save(buf, format="GIF", save_all=True, append_images=images[1:], loop=0,
                       duration=[max(20, f.duration) for f in frames], disposal=1, optimize=False)
    return buf.getvalue()


def content_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PixeldLink:
    def __init__(self, hass: HomeAssistant, prefix: str) -> None:
        self._hass = hass
        self.prefix = normalize_prefix(prefix)
        self.cmd_topic = f"{self.prefix}px/set"  # nominal device "topic" for code that passes one around
        self.telemetry = DeviceTelemetry()
        self.online: bool | None = None
        self.info: dict[str, Any] | None = None
        self.state: dict[str, Any] | None = None
        self.update_state: dict[str, Any] | None = None
        self._info_listeners: list[Callable[[], None]] = []
        self._broker_connected = False
        self._unsubscribe: list[Callable[[], None]] = []
        self._availability_listeners: list[Callable[[], None]] = []
        self._state_listeners: list[Callable[[dict], None]] = []
        self._playlist_content: dict[str, bytes] = {}
        self._recent: OrderedDict[str, bytes] = OrderedDict()
        self._info_seen = asyncio.Event()

    # ---- lifecycle ----
    async def async_start(self, timeout: float = 10) -> None:
        if not await mqtt.async_wait_for_mqtt_client(self._hass):
            raise IotError("Home Assistant's MQTT integration is not set up")
        self._broker_connected = mqtt.is_connected(self._hass)
        p = self.prefix
        self._unsubscribe = [
            mqtt.async_subscribe_connection_status(self._hass, self._on_broker),
            await mqtt.async_subscribe(self._hass, f"{p}px/status", self._on_status, qos=1),
            await mqtt.async_subscribe(self._hass, f"{p}px/info", self._on_info, qos=1),
            await mqtt.async_subscribe(self._hass, f"{p}px/state", self._on_state, qos=1),
            await mqtt.async_subscribe(self._hass, f"{p}px/need", self._on_need, qos=1),
            await mqtt.async_subscribe(self._hass, f"{p}px/update/state", self._on_update_state, qos=1),
            await mqtt.async_subscribe(self._hass, f"{p}telemetry", self._wrap(self.telemetry.async_handle_telemetry)),
            await mqtt.async_subscribe(self._hass, f"{p}jack", self._wrap(self.telemetry.async_handle_jack)),
            await mqtt.async_subscribe(self._hass, f"{p}button", self._wrap(self.telemetry.async_handle_button), qos=1),
        ]
        try:  # px/info is retained: its arrival shows the subscriptions are live
            await asyncio.wait_for(self._info_seen.wait(), timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug("No retained %spx/info yet; continuing", p)

    async def async_stop(self) -> None:
        for unsub in self._unsubscribe:
            unsub()
        self._unsubscribe = []

    @staticmethod
    def _wrap(handler):
        @callback
        def on_message(msg: mqtt.ReceiveMessage) -> None:
            handler(msg.payload)
        return on_message

    # ---- availability / state ----
    @property
    def available(self) -> bool:
        return self._broker_connected and self.online is not False

    def async_add_availability_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._availability_listeners.append(cb)
        return lambda: self._availability_listeners.remove(cb)

    def async_add_info_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Called when px/info or px/update/state changes (e.g. pixeld updated)."""
        self._info_listeners.append(cb)
        return lambda: self._info_listeners.remove(cb)

    @callback
    def _info_changed(self) -> None:
        for cb in list(self._info_listeners):
            cb()

    def async_add_state_listener(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        self._state_listeners.append(cb)
        return lambda: self._state_listeners.remove(cb)

    def async_add_report_listener(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        """Govee-protocol reports don't exist here; kept so shared setup code can register."""
        return lambda: None

    @callback
    def _availability_changed(self) -> None:
        for cb in list(self._availability_listeners):
            cb()

    @callback
    def _on_broker(self, connected: bool) -> None:
        self._broker_connected = connected
        self._availability_changed()

    @callback
    def _on_status(self, msg: mqtt.ReceiveMessage) -> None:
        online = str(msg.payload).strip() == "online"
        if online != self.online:
            self.online = online
            self._availability_changed()

    @callback
    def _on_info(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            self.info = json.loads(msg.payload)
        except ValueError:
            return
        self._info_seen.set()
        self._info_changed()

    @callback
    def _on_update_state(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            state = json.loads(msg.payload)
        except ValueError:
            return
        if isinstance(state, dict):
            self.update_state = state
            self._info_changed()

    @callback
    def _on_state(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            state = json.loads(msg.payload)
        except ValueError:
            return
        if not isinstance(state, dict):
            return
        self.state = state
        for cb in list(self._state_listeners):
            cb(state)

    # ---- content ----
    def add_content(self, data: bytes, playlist: bool = False) -> str:
        key = content_key(data)
        if playlist:
            self._playlist_content[key] = data
        else:
            self._recent[key] = data
            self._recent.move_to_end(key)
            while len(self._recent) > RECENT_CONTENT:
                self._recent.popitem(last=False)
        return key

    def set_playlist_content(self, keys: set[str]) -> None:
        """Forget playlist content no longer referenced."""
        self._playlist_content = {k: v for k, v in self._playlist_content.items() if k in keys}

    def _content(self, key: str) -> bytes | None:
        return self._playlist_content.get(key) or self._recent.get(key)

    async def _async_publish(self, topic: str, data: str | bytes, retain: bool = False) -> None:
        if not mqtt.is_connected(self._hass):
            raise IotError("Home Assistant is not connected to its MQTT broker")
        try:
            await mqtt.async_publish(self._hass, f"{self.prefix}{topic}", data, qos=1, retain=retain)
        except HomeAssistantError as err:
            raise IotError(f"publish failed: {err}") from err

    @callback
    def _on_need(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            keys = json.loads(msg.payload).get("keys", [])
        except (ValueError, AttributeError):
            return
        for key in keys:
            if (data := self._content(key)) is not None:
                self._hass.async_create_task(self._async_send_content(key, data), f"pixeld content {key[:12]}")
            else:
                _LOGGER.debug("Light asked for content %s that Home Assistant no longer has", key[:12])

    async def _async_send_content(self, key: str, data: bytes) -> None:
        try:
            await self._async_publish(f"px/content/{key}", data)
        except IotError as err:
            _LOGGER.warning("Could not send content %s to the light: %s", key[:12], err)

    # ---- commands ----
    async def async_set(self, **fields: Any) -> None:
        await self._async_publish("px/set", json.dumps(fields))

    async def async_set_power(self, topic: str, on: bool) -> None:
        await self.async_set(on=on)

    async def async_set_brightness(self, topic: str, percent: int) -> None:
        await self.async_set(brightness=percent)

    async def async_status_reply(self, topic: str, timeout: float = 10) -> dict | None:
        """State arrives retained on px/state; nothing to ask for."""
        return None

    async def async_show(self, data: bytes, name: str, hold: float = 0) -> str:
        """Send content and show it now (over the playlist); hold 0 = until resumed."""
        key = self.add_content(data)
        await self._async_publish(f"px/content/{key}", data)
        await self._async_publish("px/show", json.dumps({"key": key, "hold": hold, "name": name[:64]}))
        return key

    async def async_show_key(self, key: str, hold: float, name: str) -> None:
        """Show content the light has (or will ask for) now, for hold seconds, then back to the playlist."""
        await self._async_publish("px/show", json.dumps({"key": key, "hold": hold, "name": name[:64]}))

    async def async_send_update(self, url: str, sha256: str) -> None:
        """Have the light download a pixeld build, check it, install it on trial and restart into it."""
        await self._async_publish("px/update/install", json.dumps({"url": url, "sha256": sha256}))

    async def async_set_playlist(self, items: list[dict[str, Any]]) -> None:
        """items: {"name", "key", "duration"}; the light asks for the content it doesn't have."""
        await self._async_publish("px/playlist", json.dumps({"items": items}), retain=True)
