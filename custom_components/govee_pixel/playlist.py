"""Per-light playlist: stored in HA, played by the light's own rotation ("looplay").

Home Assistant renders every item, serves the files at content-addressed URLs and sends the
light ONE playlist file; the light then rotates on its own. Edits and live-item refreshes re-send
the playlist, and the light only downloads items whose URL is new. Interruptions (e.g. a doorbell
snapshot) show a one-off image, then the playlist is resumed.

If the light rejects the playlist, or playback is set to "home_assistant", HA drives the rotation
itself by pushing each item as a one-off image in turn.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from PIL import Image

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import content, looplay, payload
from .const import (
    CONF_PLAYBACK, CONF_REFRESH_MINUTES, CONF_RESUME, DEFAULT_REFRESH_MINUTES, PLAYBACK_HA, PLAYBACK_LIGHT,
    RESUME_MODE_PACKET, RESUME_REPUSH,
)
from .display import async_push_effect, async_render, async_set_preview, file_url
from .effect_server import async_get_store
from .iot import OP_PLAYLIST, IotError

if TYPE_CHECKING:
    from . import GoveePixelConfigEntry
    from .api import PixelDevice

_LOGGER = logging.getLogger(__name__)

STATUS_STOPPED = "stopped"
STATUS_LIGHT = "rotating"
STATUS_HA = "rotating_ha"
STATUS_INTERRUPTED = "interrupted"
STATUS_PAUSED = "paused"
STATUS_ERROR = "error"


class PlaylistController:
    def __init__(self, hass: HomeAssistant, entry: GoveePixelConfigEntry, dev: PixelDevice,
                 stored: dict[str, Any] | None, save: Callable[[], None]) -> None:
        self.hass, self.entry, self.dev = hass, entry, dev
        stored = stored or {}
        self.items: list[dict[str, Any]] = stored.get("items", [])
        self.active: bool = stored.get("active", False)
        self.playlist_id: int = stored.get("playlist_id") or random.randint(10_000, 999_999)
        self.item_keys: dict[str, str] = stored.get("item_keys", {})  # name -> md5 of its effect file
        self.playlist_key: str | None = stored.get("playlist_key")
        self.status = STATUS_STOPPED
        self.last_error: str | None = None
        self.last_push: float | None = None
        self.interrupt_until: float | None = None
        self._ha_fallback = False
        self._save = save
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._resume_cancel: CALLBACK_TYPE | None = None
        self._refresh_cancel: CALLBACK_TYPE | None = None
        self._rotation: asyncio.Task | None = None

    # ---------- state ----------
    def as_dict(self) -> dict[str, Any]:
        return {"items": self.items, "active": self.active, "playlist_id": self.playlist_id,
                "item_keys": self.item_keys, "playlist_key": self.playlist_key}

    @property
    def owner(self) -> str:
        return f"playlist:{self.dev.device}"

    @property
    def playback(self) -> str:
        if self._ha_fallback:
            return PLAYBACK_HA
        return self.entry.options.get(CONF_PLAYBACK, PLAYBACK_LIGHT)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _changed(self, save: bool = False) -> None:
        if save:
            self._save()
        for cb in list(self._listeners):
            cb()

    # ---------- lifecycle ----------
    async def async_start(self) -> None:
        """Called at setup: re-pin stored files, start the refresh timer, refresh if active."""
        keys = set(self.item_keys.values()) | ({self.playlist_key} if self.playlist_key else set())
        await async_get_store(self.hass).async_pin(self.owner, keys)
        minutes = self.entry.options.get(CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES)
        self._refresh_cancel = async_track_time_interval(
            self.hass, self._async_refresh_tick, timedelta(minutes=max(1, minutes)))
        if self.active and self.items:
            # The light keeps its playlist across restarts; only push if content changed (e.g. weather).
            self.status = STATUS_HA if self.playback == PLAYBACK_HA else STATUS_LIGHT
            self.hass.async_create_background_task(self._async_safe_push(), f"govee_pixel refresh {self.dev.name}")

    async def async_stop(self) -> None:
        for cancel in (self._resume_cancel, self._refresh_cancel):
            if cancel:
                cancel()
        self._resume_cancel = self._refresh_cancel = None
        await self._async_stop_rotation()

    # ---------- editing ----------
    def _validated(self, item_type: str, options: dict) -> dict:
        try:
            return content.options_schema(item_type)(dict(options))
        except vol.Invalid as err:
            raise ServiceValidationError(f"Invalid {item_type} options: {err}") from err

    async def async_add(self, name: str, item_type: str, options: dict, duration: int,
                        position: int | None = None) -> None:
        self._validated(item_type, options)
        item = {"name": name, "type": item_type, "options": dict(options), "duration": int(duration)}
        existing = next((i for i, it in enumerate(self.items) if it["name"] == name), None)
        if existing is not None:
            self.items.pop(existing)
            if position is None:
                position = existing
        elif len(self.items) >= looplay.MAX_ITEMS:
            raise ServiceValidationError(f"A playlist holds at most {looplay.MAX_ITEMS} items")
        self.items.insert(len(self.items) if position is None else max(0, min(position, len(self.items))), item)
        self._changed(save=True)
        await self._async_apply_edit()

    async def async_remove(self, name: str) -> None:
        before = len(self.items)
        self.items = [it for it in self.items if it["name"] != name]
        if len(self.items) == before:
            raise ServiceValidationError(f"No playlist item named {name!r}")
        self.item_keys.pop(name, None)
        self._changed(save=True)
        await self._async_apply_edit()

    async def async_clear(self) -> None:
        self.items, self.item_keys = [], {}
        self._changed(save=True)
        await self._async_apply_edit()

    async def _async_apply_edit(self) -> None:
        if not self.active:
            return
        if not self.items:
            await self.async_set_active(False)
        elif self.interrupt_until is None and self.status != STATUS_PAUSED:
            await self.async_push()

    # ---------- playing ----------
    async def async_set_active(self, on: bool) -> None:
        if on:
            if not self.items:
                raise ServiceValidationError("The playlist is empty; add items with govee_pixel.playlist_add")
            self.active = True
            self._ha_fallback = False
            self._cancel_resume()
            await self.async_push(force=True)
        else:
            self.active = False
            self._cancel_resume()
            await self._async_stop_rotation()
            self.status, self.last_error, self._ha_fallback = STATUS_STOPPED, None, False
            # Replace the rotation on the light with a blank screen.
            blank = content.Prepared(lambda size: [payload.Frame(Image.new("RGB", (size, size)), 1000)],
                                     (0, 0, 0), "blank")
            effect, _ = await async_render(self.hass, self.entry, self.dev, blank)
            await async_push_effect(self.hass, self.entry, self.dev, effect)
        self._changed(save=True)

    async def _async_render_items(self) -> list[tuple[dict, str, list[payload.Frame] | None]]:
        """Render every item: (item, file key, frames). Failed items keep their last good file."""
        store = async_get_store(self.hass)
        out = []
        for item in self.items:
            try:
                prepared = await content.async_prepare(
                    self.hass, item["type"], self._validated(item["type"], item["options"]))
                effect, frames = await async_render(self.hass, self.entry, self.dev, prepared)
                out.append((item, store.add(effect), frames))
            except HomeAssistantError as err:
                if (key := self.item_keys.get(item["name"])) is not None:
                    _LOGGER.warning("%s: playlist item %r failed (%s); keeping its previous image",
                                    self.dev.name, item["name"], err)
                    out.append((item, key, None))
                else:
                    _LOGGER.warning("%s: playlist item %r skipped: %s", self.dev.name, item["name"], err)
        return out

    async def async_push(self, force: bool = False) -> None:
        """Render items, publish files and (if anything changed or `force`) send the playlist to the light."""
        async with self._lock:
            rendered = await self._async_render_items()
            if not rendered:
                self.status, self.last_error = STATUS_ERROR, "no playlist item could be rendered"
                self._changed()
                raise HomeAssistantError(f"{self.dev.name}: {self.last_error}")
            self.item_keys = {item["name"]: key for item, key, _ in rendered}
            store = async_get_store(self.hass)
            playlist = looplay.build_playlist(
                [looplay.Item(file_url(self.hass, self.entry, key), item["duration"]) for item, key, _ in rendered])
            playlist_key = store.add(playlist)
            changed = playlist_key != self.playlist_key
            self.playlist_key = playlist_key
            await store.async_pin(self.owner, set(self.item_keys.values()) | {playlist_key})
            first_frames = next((frames for _, _, frames in rendered if frames), None)
            if first_frames:
                await async_set_preview(self.hass, self.entry, self.dev, first_frames)
            self._changed(save=True)
            if self.interrupt_until is not None or self.status == STATUS_PAUSED:
                return  # files are ready; the resume will apply them
            if self.playback == PLAYBACK_HA:
                if changed or force or self._rotation is None:
                    await self._async_start_rotation(rendered)
                return
            if not changed and not force:
                return
            await self._async_send_playlist()

    async def _async_send_playlist(self) -> None:
        url = file_url(self.hass, self.entry, self.playlist_key)
        _LOGGER.debug("%s: sending playlist %s (%d items, id %d)", self.dev.name, url, len(self.item_keys),
                      self.playlist_id)
        try:
            ok = await self.entry.runtime_data.iot.async_pt_url(
                self.dev.topic, url, op=OP_PLAYLIST, need_cache=True,
                mode_packet=looplay.start_packet(self.playlist_id))
        except IotError as err:
            self.status, self.last_error = STATUS_ERROR, str(err)
            self._changed()
            raise HomeAssistantError(f"{self.dev.name}: {err}") from err
        if ok is False:
            # The light could not take the playlist: rotate from Home Assistant instead.
            _LOGGER.warning("%s rejected the playlist download; falling back to rotating from Home Assistant",
                            self.dev.name)
            self._ha_fallback = True
            self.last_error = "light rejected the playlist; rotating from Home Assistant"
            await self._async_start_rotation(await self._async_render_items())
            return
        if ok is None:
            _LOGGER.warning("%s did not confirm the playlist (no reply)", self.dev.name)
        await self._async_stop_rotation()
        self.status, self.last_error, self.last_push = STATUS_LIGHT, None, time.time()
        self._changed()

    async def _async_safe_push(self, force: bool = False) -> None:
        try:
            await self.async_push(force)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: playlist update failed: %s", self.dev.name, err)

    async def _async_refresh_tick(self, _now=None) -> None:
        if self.active and self.items and self.interrupt_until is None and self.status != STATUS_PAUSED:
            await self._async_safe_push()

    # ---------- interruptions ----------
    async def async_interrupt(self, show: Callable[[], Any], hold: int) -> Any:
        """Show something now; if the playlist is active, resume it after `hold` seconds (0 = stay paused)."""
        self._cancel_resume()
        if self.active:
            await self._async_stop_rotation()
        result = await show()
        if not self.active:
            return result
        if hold > 0:
            self.status, self.interrupt_until = STATUS_INTERRUPTED, time.time() + hold
            self._resume_cancel = async_call_later(self.hass, hold, self._async_resume_cb)
        else:
            self.status, self.interrupt_until = STATUS_PAUSED, None
        self._changed()
        return result

    async def _async_resume_cb(self, _now=None) -> None:
        self._resume_cancel = None
        try:
            await self.async_resume()
        except HomeAssistantError as err:
            _LOGGER.warning("%s: could not resume the playlist: %s", self.dev.name, err)

    async def async_resume(self) -> None:
        self._cancel_resume()
        self.interrupt_until = None
        if not self.active:
            return
        if self.playback == PLAYBACK_HA:
            await self.async_push(force=True)
            return
        method = self.entry.options.get(CONF_RESUME, RESUME_REPUSH)
        if method == RESUME_MODE_PACKET and self.playlist_key:
            # The light keeps its playlist; switching back to the playlist mode should restart it.
            await self.entry.runtime_data.iot.async_pt_real(self.dev.topic, [looplay.start_packet(self.playlist_id)])
            self.status = STATUS_LIGHT
            self._changed()
            # Content may have been refreshed while interrupted.
            await self._async_safe_push()
        else:
            # Re-send the playlist: the light still has every item cached by URL, so this is cheap.
            await self.async_push(force=True)

    @callback
    def _cancel_resume(self) -> None:
        if self._resume_cancel:
            self._resume_cancel()
            self._resume_cancel = None

    # ---------- Home Assistant-driven rotation (fallback) ----------
    async def _async_start_rotation(self, rendered: list[tuple[dict, str, list | None]]) -> None:
        await self._async_stop_rotation()
        plan = [(item, key) for item, key, _ in rendered]
        self._rotation = self.hass.async_create_background_task(
            self._async_rotate(plan), f"govee_pixel rotation {self.dev.name}")
        self.status = STATUS_HA
        self._changed()

    async def _async_rotate(self, plan: list[tuple[dict, str]]) -> None:
        store = async_get_store(self.hass)
        index = 0
        while True:
            item, key = plan[index % len(plan)]
            body = await store.async_get(key)
            if body is not None:
                try:
                    await async_push_effect(self.hass, self.entry, self.dev, body)
                except HomeAssistantError as err:
                    _LOGGER.warning("%s: rotation step %r failed: %s", self.dev.name, item["name"], err)
            await asyncio.sleep(max(5, item["duration"]))
            index += 1

    async def _async_stop_rotation(self) -> None:
        if self._rotation:
            self._rotation.cancel()
            try:
                await self._rotation
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._rotation = None
