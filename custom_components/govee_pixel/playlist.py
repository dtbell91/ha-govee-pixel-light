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
import hashlib
import json
import logging
import random
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from PIL import Image

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, TemplateError
from homeassistant.helpers.event import (
    TrackTemplate, async_call_later, async_track_state_change_event, async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template, result_as_boolean

from . import content, looplay, payload
from .const import (
    CONF_PLAYBACK, CONF_RECLAIM, CONF_REFRESH_MINUTES, CONF_RESUME, DEFAULT_REFRESH_MINUTES, PLAYBACK_HA, PLAYBACK_LIGHT,
    RESUME_MODE_PACKET, RESUME_REPUSH, DOMAIN,
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
REDRAW_DELAY = 3  # seconds: coalesce bursts of entity changes into one redraw


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
        self.light_on: bool | None = None  # from the light's last status reply
        self.light_mode: str | None = None
        self.reapply_pending = False  # an automatic re-send is waiting for the light to be on
        self._ha_fallback = False
        self._save = save
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._resume_cancel: CALLBACK_TYPE | None = None
        self._refresh_cancel: CALLBACK_TYPE | None = None
        self._state_cancel: CALLBACK_TYPE | None = None
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
        if (state := self.entry.runtime_data.states.get(self.dev.device)) is not None:
            self._state_cancel = state.async_add_listener(self._on_light_state)
        if self.active and self.items:
            # The light keeps its playlist file across restarts; re-check content and what it shows.
            self.status = STATUS_HA if self.playback == PLAYBACK_HA else STATUS_LIGHT
            self.hass.async_create_background_task(self._async_refresh_tick(), f"govee_pixel refresh {self.dev.name}")

    async def async_stop(self) -> None:
        for cancel in (self._resume_cancel, self._refresh_cancel, self._state_cancel):
            if cancel:
                cancel()
        self._resume_cancel = self._refresh_cancel = self._state_cancel = None
        await self._async_stop_rotation()

    # ---------- editing ----------
    def _validated(self, item_type: str, options: dict) -> dict:
        try:
            return content.options_schema(item_type)(dict(options))
        except vol.Invalid as err:
            raise ServiceValidationError(f"Invalid {item_type} options: {err}") from err

    async def async_add(self, name: str, item_type: str, options: dict, duration: int,
                        position: int | None = None, show_if: str | None = None) -> None:
        self._validated(item_type, options)
        item = {"name": name, "type": item_type, "options": dict(options), "duration": int(duration)}
        if show_if:
            item["show_if"] = show_if
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
            self.reapply_pending = False
            # Replace the rotation on the light with a blank screen.
            blank = content.Prepared(lambda size: [payload.Frame(Image.new("RGB", (size, size)), 1000)],
                                     (0, 0, 0), "blank")
            effect, _ = await async_render(self.hass, self.entry, self.dev, blank)
            await async_push_effect(self.hass, self.entry, self.dev, effect)
        self._changed(save=True)

    def _render_options(self, item: dict) -> dict | None:
        """The item's options for rendering; now_playing narrowed to the player to show (None: nothing
        playing, or not in the room)."""
        options = self._validated(item["type"], item["options"])
        if item["type"] != content.KIND_NOW_PLAYING:
            return options
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self.dev.device)})
        player = content.pick_player(self.hass, options, device.area_id if device else None)
        return None if player is None else {**options, content.ATTR_MEDIA_PLAYER: [player]}

    async def _async_render_items(self) -> list[tuple[dict, str, list[payload.Frame] | None]]:
        """Render every item: (item, file key, frames). Failed items keep their last good file."""
        store = async_get_store(self.hass)
        out = []
        for item in self.items:
            try:
                if (options := self._render_options(item)) is None:
                    continue
                prepared = await content.async_prepare(self.hass, item["type"], options)
                effect, frames = await async_render(self.hass, self.entry, self.dev, prepared)
                out.append((item, store.add(effect), frames))
            except content.NothingToShow as err:
                _LOGGER.debug("%s: playlist item %r left out: %s", self.dev.name, item["name"], err)
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

    async def _async_safe_push(self, force: bool = False) -> bool:
        try:
            await self.async_push(force)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: playlist update failed: %s", self.dev.name, err)
            return False
        return True

    async def _async_light_state(self) -> dict | None:
        """Ask the light whether it is on and what it shows (None if it doesn't answer)."""
        try:
            state = await self.entry.runtime_data.iot.async_status(self.dev.topic)
        except IotError as err:
            _LOGGER.debug("%s: status query failed: %s", self.dev.name, err)
            return None
        if state is not None:
            self.light_on = state["on"]
            self.light_mode = state["mode"].hex() if state["mode"] else None
            self._changed()
        return state

    @callback
    def _on_light_state(self) -> None:
        """The light pushed a state change: mirror it, and catch up as soon as it is switched back on."""
        state = self.entry.runtime_data.states[self.dev.device]
        was_on = self.light_on
        self.light_on = state.on
        self.light_mode = state.mode.hex() if state.mode else None
        self._changed()
        if (was_on is False and state.on and self.active and self.items
                and self.interrupt_until is None and self.status != STATUS_PAUSED):
            self.hass.async_create_background_task(
                self._async_refresh_tick(), f"govee_pixel catch-up {self.dev.name}")

    @staticmethod
    def _shows_ours(mode: bytes | None) -> bool:
        """True if the light's mode echo is our playlist (aa 05 82 ..) or one of our effects (aa 05 0a fe ..)."""
        if not mode or len(mode) < 5:
            return True  # unknown: don't second-guess
        return mode[2] == 0x82 or mode[2:5] == b"\x0a\xfe\x00"

    async def _async_refresh_tick(self, _now=None) -> None:
        if not (self.active and self.items) or self.interrupt_until is not None or self.status == STATUS_PAUSED:
            return
        if self.playback == PLAYBACK_HA:
            await self._async_safe_push()
            return
        state = await self._async_light_state()
        if state is not None and state["on"] is False:
            return  # don't wake a light that was switched off; catch up when it is back on
        force = self.reapply_pending
        if (state is not None and self.entry.options.get(CONF_RECLAIM, True)
                and not self._shows_ours(state["mode"])):
            # e.g. after a power cycle (the light doesn't restore its playlist mode) or a scene picked elsewhere
            _LOGGER.info("%s shows something else (%s); re-applying the playlist", self.dev.name, self.light_mode)
            force = True
        if await self._async_safe_push(force) and force:
            self.reapply_pending = False

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
        if self.active and self.playback != PLAYBACK_HA:
            state = await self._async_light_state()
            if state is not None and state["on"] is False:
                # Switched off during the interruption: resume on the next refresh once it is on again.
                self.interrupt_until, self.status, self.reapply_pending = None, STATUS_LIGHT, True
                self._changed()
                return
        try:
            await self.async_resume()
        except HomeAssistantError as err:
            _LOGGER.warning("%s: could not resume the playlist: %s", self.dev.name, err)

    async def async_resume(self) -> None:
        self._cancel_resume()
        self.interrupt_until = None
        if not self.active:
            return
        if self.status in (STATUS_INTERRUPTED, STATUS_PAUSED):
            # Leave the interrupted/paused state first: async_push holds back while it is set.
            self.status = STATUS_HA if self.playback == PLAYBACK_HA else STATUS_LIGHT
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


class PixeldPlaylistController(PlaylistController):
    """Playlist for a light running pixeld: the light rotates the items itself and keeps its place when the
    list changes, so edits and live-item refreshes just publish the new item list (only new content is
    transferred). Interruptions are shown over the playlist and ended with a resume.

    Items can be conditional (show_if template, calendar without events, nothing playing) and are redrawn
    shortly after the entities they use change, not only at the periodic refresh."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._track_signature: tuple | None = None
        self._now_playing: dict[str, tuple | None] = {}  # now_playing item -> the track last seen
        self._track_cancels: list[CALLBACK_TYPE] = []
        self._redraw_cancel: CALLBACK_TYPE | None = None

    @property
    def link(self):
        return self.entry.runtime_data.iot

    @property
    def playback(self) -> str:
        return PLAYBACK_LIGHT

    async def async_start(self) -> None:
        minutes = self.entry.options.get(CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES)
        self._refresh_cancel = async_track_time_interval(
            self.hass, self._async_refresh_tick, timedelta(minutes=max(1, minutes)))
        if (state := self.entry.runtime_data.states.get(self.dev.device)) is not None:
            self._state_cancel = state.async_add_listener(self._on_light_state)
        if self.active and self.items:
            self.status = STATUS_LIGHT
            # Render now so the light's requests for content can be answered after a restart.
            self.hass.async_create_background_task(self._async_safe_push(force=True),
                                                   f"govee_pixel playlist {self.dev.name}")

    async def async_set_active(self, on: bool) -> None:
        if on:
            if not self.items:
                raise ServiceValidationError("The playlist is empty; add items with govee_pixel.playlist_add")
            self.active = True
            self._cancel_resume()
            self.interrupt_until = None
            if self.status in (STATUS_INTERRUPTED, STATUS_PAUSED, STATUS_STOPPED, STATUS_ERROR):
                self.status = STATUS_LIGHT
            await self.async_push(force=True)
            await self._async_send(resume=True)
        else:
            self.active = False
            self._cancel_resume()
            self.status, self.last_error, self.interrupt_until = STATUS_STOPPED, None, None
            try:
                await self.link.async_set_playlist([])
            except IotError as err:
                raise HomeAssistantError(f"{self.dev.name}: {err}") from err
        self._changed(save=True)

    async def _async_send(self, **fields: Any) -> None:
        try:
            await self.link.async_set(**fields)
        except IotError as err:
            raise HomeAssistantError(f"{self.dev.name}: {err}") from err

    async def _async_render_content(self) -> list[tuple[dict, str, list[payload.Frame] | None]]:
        """Render every item to image bytes for the light: (item, content key, frames)."""
        from .display import async_render_frames
        from .pixeld import encode_content

        out = []
        for item in self.items:
            if not self._show_if(item):
                continue
            if item["type"] in content.CARD_KINDS:
                out.append((item, None, None))  # drawn by the light; see _card()
                continue
            try:
                if (options := self._render_options(item)) is None:
                    continue
                prepared = await content.async_prepare(self.hass, item["type"], options)
                frames = await async_render_frames(self.hass, self.dev, prepared)
                data = await self.hass.async_add_executor_job(encode_content, frames, prepared.source)
                out.append((item, self.link.add_content(data, playlist=True), frames))
            except content.NothingToShow as err:
                _LOGGER.debug("%s: playlist item %r left out: %s", self.dev.name, item["name"], err)
            except HomeAssistantError as err:
                if (key := self.item_keys.get(item["name"])) is not None:
                    _LOGGER.warning("%s: playlist item %r failed (%s); keeping its previous image",
                                    self.dev.name, item["name"], err)
                    out.append((item, key, None))
                else:
                    _LOGGER.warning("%s: playlist item %r skipped: %s", self.dev.name, item["name"], err)
        return out

    async def async_push(self, force: bool = False) -> None:
        async with self._lock:
            self._async_retrack()
            rendered = await self._async_render_content()
            if not rendered and not any(it.get("show_if") or it["type"] in content.CONDITIONAL_KINDS
                                        for it in self.items):
                self.status, self.last_error = STATUS_ERROR, "no playlist item could be rendered"
                self._changed()
                raise HomeAssistantError(f"{self.dev.name}: {self.last_error}")
            self.item_keys = {item["name"]: key for item, key, _ in rendered if key}
            self.link.set_playlist_content(set(self.item_keys.values()))
            items = [{"name": item["name"], "duration": item["duration"],
                      **({"card": self._card(item)} if key is None else {"key": key})} for item, key, _ in rendered]
            playlist_key = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
            changed = playlist_key != self.playlist_key
            self.playlist_key = playlist_key
            if first := next((frames for _, _, frames in rendered if frames), None):
                await async_set_preview(self.hass, self.entry, self.dev, first)
            if changed or force:
                try:
                    await self.link.async_set_playlist(items)
                except IotError as err:
                    self.status, self.last_error = STATUS_ERROR, str(err)
                    self._changed()
                    raise HomeAssistantError(f"{self.dev.name}: {err}") from err
                self.last_push = time.time()
            if self.status in (STATUS_STOPPED, STATUS_ERROR, STATUS_HA):
                self.status = STATUS_LIGHT
            self.last_error = None
            self._changed(save=True)
            await self._async_show_new_tracks(rendered)

    def _track(self, item: dict) -> tuple | None:
        """What a now_playing item shows now: (player, title, artist, album), or None if nothing."""
        try:
            options = self._render_options(item)
        except HomeAssistantError:
            return None
        if options is None:
            return None
        player = options[content.ATTR_MEDIA_PLAYER][0]
        attrs = (state := self.hass.states.get(player)) and state.attributes or {}
        return (player, attrs.get("media_title"), attrs.get("media_artist"), attrs.get("media_album_name"))

    async def _async_show_new_tracks(self, rendered: list) -> None:
        """When a now_playing item's track changes (or music starts), show it now for its duration; the
        light then carries on with the rotation. Not over something shown on purpose, and not on the
        first look after starting up."""
        keys = {item["name"]: key for item, key, _ in rendered if key}
        for item in self.items:
            if item["type"] != content.KIND_NOW_PLAYING:
                continue
            name = item["name"]
            track = self._track(item) if name in keys else None
            first = name not in self._now_playing
            changed = self._now_playing.get(name) != track
            self._now_playing[name] = track
            if first or not changed or track is None:
                continue
            if not self.active or self.status != STATUS_LIGHT or self.interrupt_until is not None:
                continue
            try:
                await self.link.async_show_key(keys[name], item["duration"], name)
            except IotError as err:
                _LOGGER.debug("%s: couldn't show the new track: %s", self.dev.name, err)

    def _show_if(self, item: dict) -> bool:
        if not (source := item.get("show_if")):
            return True
        try:
            return result_as_boolean(Template(source, self.hass).async_render(parse_result=False))
        except TemplateError as err:
            _LOGGER.warning("%s: show_if of %r failed (%s); leaving the item out", self.dev.name, item["name"], err)
            return False

    @callback
    def _async_retrack(self) -> None:
        """Redraw soon after anything an item depends on changes: its source entities, its show_if result."""
        entities: set[str] = set()
        templates = []
        for item in self.items:
            try:
                entities.update(content.source_entities(item["type"], self._validated(item["type"], item["options"])))
            except HomeAssistantError:
                pass
            if item.get("show_if"):
                templates.append(item["show_if"])
        signature = (frozenset(entities), tuple(templates), self.active)
        if signature == self._track_signature:
            return
        self._track_signature = signature
        for cancel in self._track_cancels:
            cancel()
        self._track_cancels = []
        if not self.active:
            return
        if entities:
            self._track_cancels.append(async_track_state_change_event(self.hass, sorted(entities), self._on_source))
        if templates:
            info = async_track_template_result(
                self.hass, [TrackTemplate(Template(t, self.hass), None) for t in templates], self._on_source)
            self._track_cancels.append(info.async_remove)

    @callback
    def _on_source(self, *_args) -> None:
        if self._redraw_cancel is None and self.active:
            self._redraw_cancel = async_call_later(self.hass, REDRAW_DELAY, self._async_redraw)

    async def _async_redraw(self, _now=None) -> None:
        self._redraw_cancel = None
        if self.active and self.interrupt_until is None and self.status != STATUS_PAUSED:
            await self._async_safe_push()

    async def async_stop(self) -> None:
        for cancel in self._track_cancels:
            cancel()
        self._track_cancels = []
        if self._redraw_cancel:
            self._redraw_cancel()
            self._redraw_cancel = None
        await super().async_stop()

    def _card(self, item: dict) -> dict:
        options = self._validated(item["type"], item["options"])
        if item["type"] == content.KIND_CLOCK:
            return content.clock_card(self.hass, options)
        raise HomeAssistantError(f"no card for {item['type']}")

    @callback
    def _on_light_state(self) -> None:
        state = self.entry.runtime_data.states[self.dev.device]
        self.light_on = state.on
        self.light_mode = state.showing
        self._changed()

    async def _async_refresh_tick(self, _now=None) -> None:
        if self.active and self.items:
            await self._async_safe_push()  # re-renders live items; publishes only if something changed

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
        self.status = STATUS_LIGHT
        await self._async_send(resume=True)
        self._changed()
        await self._async_safe_push()  # content may have been refreshed meanwhile
