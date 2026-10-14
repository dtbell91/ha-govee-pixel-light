"""Live state of one light, kept current by the reports it pushes over MQTT.

After any change the light publishes a report on its reply topic without being asked, e.g.
{"cmd": "brightness", "state": {"brightness": 90}} or, after power changes, a full
{"cmd": "status", "state": {"onOff": 1, "brightness": 100, ...}, "op": {"command": [<aa 05 mode echo>, ...]}}.
"""
from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback

_LOGGER = logging.getLogger(__name__)

SHOWING_OFF = "off"
SHOWING_PLAYLIST = "playlist"
SHOWING_IMAGE = "image"
SHOWING_SCENE = "govee_scene"
SHOWING_IDLE = "idle"  # pixeld with nothing to show
SHOWING_STREAM = "stream"  # pixeld showing live frames (DDP)
SHOWING_OPTIONS = [SHOWING_OFF, SHOWING_PLAYLIST, SHOWING_IMAGE, SHOWING_SCENE, SHOWING_IDLE, SHOWING_STREAM]
_PIXELD_SHOWING = {"off": SHOWING_OFF, "playlist": SHOWING_PLAYLIST, "image": SHOWING_IMAGE, "idle": SHOWING_IDLE,
                   "stream": SHOWING_STREAM}


def mode_from_op(op: Any) -> bytes | None:
    """The light's mode echo (aa 05 ..) from a report's op.command frames, if present."""
    frames = op.get("command") if isinstance(op, dict) else None
    for item in frames or []:
        try:
            frame = base64.b64decode(item)
        except (ValueError, TypeError):
            continue
        if frame[:2] == b"\xaa\x05":
            return frame
    return None


class DeviceState:
    def __init__(self, device: str) -> None:
        self.device = device
        self.on: bool | None = None
        self.brightness: int | None = None  # percent, 1-100
        self.mode: bytes | None = None  # last mode echo (aa 05 ..)
        self.last_report: float | None = None
        self.pixeld_showing: str | None = None  # set when the light runs pixeld (no mode echo)
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @property
    def showing(self) -> str | None:
        if self.on is False:
            return SHOWING_OFF
        if self.pixeld_showing is not None:
            return self.pixeld_showing
        if not self.mode or len(self.mode) < 5:
            return None
        if self.mode[2] == 0x82:
            return SHOWING_PLAYLIST
        if self.mode[2:5] == b"\x0a\xfe\x00":
            return SHOWING_IMAGE
        return SHOWING_SCENE

    @callback
    def async_handle_report(self, payload: dict) -> None:
        """Apply any message from the light (reply or unsolicited report)."""
        if payload.get("device") not in (None, self.device):
            return
        state = payload.get("state")
        if not isinstance(state, dict):
            return
        changed = False
        # Reports and replies to type-0 requests carry type 0; the reply to a type-1 status request is an
        # all-zero placeholder (type 2), and command acks (type 1) carry only {"result": ..}.
        trusted = payload.get("type", 0) == 0
        if trusted and "onOff" in state:
            on = bool(state["onOff"])
            changed |= on != self.on
            self.on = on
        if trusted and state.get("brightness"):
            brightness = int(state["brightness"])
            changed |= brightness != self.brightness
            self.brightness = brightness
        if (mode := mode_from_op(payload.get("op"))) is not None:
            changed |= mode != self.mode
            self.mode = mode
        self.last_report = time.time()
        if changed:
            _LOGGER.debug("%s: on=%s brightness=%s mode=%s", self.device, self.on, self.brightness,
                          self.mode.hex() if self.mode else None)
            self._notify()

    @callback
    def async_handle_pixeld(self, state: dict) -> None:
        """Apply pixeld's retained px/state: {"on", "brightness", "showing", ...}."""
        on = state.get("on")
        brightness = state.get("brightness")
        showing = _PIXELD_SHOWING.get(state.get("showing"), SHOWING_IDLE)
        changed = (on, brightness, showing) != (self.on, self.brightness, self.pixeld_showing)
        if isinstance(on, bool):
            self.on = on
        if isinstance(brightness, int) and brightness > 0:
            self.brightness = brightness
        self.pixeld_showing = showing
        self.last_report = time.time()
        if changed:
            self._notify()

    @callback
    def async_set_optimistic(self, on: bool | None = None, brightness: int | None = None) -> None:
        if on is not None:
            self.on = on
        if brightness is not None:
            self.brightness = brightness
        self._notify()

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def as_dict(self) -> dict[str, Any]:
        return {"on": self.on, "brightness": self.brightness, "mode": self.mode.hex() if self.mode else None,
                "showing": self.showing, "last_report": self.last_report}
