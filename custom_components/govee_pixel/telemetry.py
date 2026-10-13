"""Device health, buttons and headphone jack, published by the custom firmware's telemetry layer.

    <prefix>telemetry  retained, every 60 s: {"rssi": -65, "uptime": 25947, "mem_available_kb": 71180,
                       "firmware": "3.2.0", "base": "1.00.91", "profile": "local", "slot": "B", ...}
    <prefix>button     {"button": "power|mode|volume|customize", "action": "press|long_press"}
    <prefix>jack       retained: {"headphones": false, "microphone": false}

Only lights on the local-MQTT firmware with the telemetry layer publish these; until something
arrives the telemetry entities stay unavailable.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback

_LOGGER = logging.getLogger(__name__)

BUTTONS = ["power", "mode", "volume", "customize"]
BUTTON_ACTIONS = ["press", "long_press"]


def _json(raw: bytes | str) -> dict | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class DeviceTelemetry:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.received: float | None = None
        self.jack: dict[str, Any] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._button_listeners: list[Callable[[str, str], None]] = []

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def async_add_button_listener(self, cb: Callable[[str, str], None]) -> CALLBACK_TYPE:
        self._button_listeners.append(cb)
        return lambda: self._button_listeners.remove(cb)

    @property
    def boot_time(self) -> float | None:
        if not self.data or not isinstance(self.data.get("uptime"), (int, float)):
            return None
        return self.received - self.data["uptime"]

    @callback
    def async_handle_telemetry(self, raw: bytes | str) -> None:
        if (data := _json(raw)) is None:
            return
        self.data, self.received = data, time.time()
        self._notify()

    @callback
    def async_handle_jack(self, raw: bytes | str) -> None:
        if (data := _json(raw)) is None:
            return
        self.jack = data
        self._notify()

    @callback
    def async_handle_button(self, raw: bytes | str) -> None:
        data = _json(raw) or {}
        button, action = data.get("button"), data.get("action")
        if button not in BUTTONS or action not in BUTTON_ACTIONS:
            _LOGGER.debug("Ignoring button message %r", raw)
            return
        for cb in list(self._button_listeners):
            cb(button, action)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.data or {})
        data.pop("ip", None)
        return {"telemetry": data, "received": self.received, "jack": self.jack}
