"""Command link over Home Assistant's own MQTT broker (no Govee account or cloud).

For lights running the local-MQTT custom firmware: the light's govee_iot talks to a broker on the
light itself, which bridges it to Home Assistant's broker under a topic prefix (default
`govee/h8630/`):

    <prefix>cmd           commands to the light
    <prefix>state         the light's replies and reports
    <prefix>lwt           the light's MQTT last will (connected / disconnected)
    <prefix>bridge/state  1 / 0, retained: the light's bridge to this broker is up
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .iot import CommandLink, IotError

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOPIC_PREFIX = "govee/h8630/"


def normalize_prefix(prefix: str) -> str:
    prefix = prefix.strip().strip("/")
    return f"{prefix}/" if prefix else ""


class LocalMqttLink(CommandLink):
    def __init__(self, hass: HomeAssistant, prefix: str) -> None:
        super().__init__(hass.loop)
        self._hass = hass
        self.prefix = normalize_prefix(prefix)
        self.cmd_topic = f"{self.prefix}cmd"
        self._unsubscribe: list = []
        self.bridge_up: bool | None = None  # None until the retained bridge state arrives
        self._bridge_seen = asyncio.Event()

    async def async_start(self, timeout: float = 10) -> None:
        if not await mqtt.async_wait_for_mqtt_client(self._hass):
            raise IotError("Home Assistant's MQTT integration is not set up")
        self._unsubscribe = [
            await mqtt.async_subscribe(self._hass, f"{self.prefix}state", self._on_state, qos=0),
            await mqtt.async_subscribe(self._hass, f"{self.prefix}bridge/state", self._on_bridge, qos=0),
        ]
        # HA sends subscriptions in debounced batches once connected; the retained bridge state
        # arriving shows the batch (including <prefix>state) is live, so replies won't be missed.
        try:
            await asyncio.wait_for(self._bridge_seen.wait(), timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug("No retained %sbridge/state yet; continuing", self.prefix)

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []

    @callback
    def _on_state(self, msg: mqtt.ReceiveMessage) -> None:
        self._handle_reply(msg.payload)

    @callback
    def _on_bridge(self, msg: mqtt.ReceiveMessage) -> None:
        up = str(msg.payload).strip() == "1"
        if up != self.bridge_up:
            _LOGGER.debug("Light's MQTT bridge (%s) is %s", self.prefix, "up" if up else "down")
        self.bridge_up = up
        self._bridge_seen.set()

    async def _async_wait_connected(self, timeout: float = 15) -> None:
        deadline = self._loop.time() + timeout
        while not mqtt.is_connected(self._hass):
            if self._loop.time() >= deadline:
                raise IotError("Home Assistant is not connected to its MQTT broker")
            await asyncio.sleep(0.5)
        if self.bridge_up is False:
            raise IotError(f"the light's MQTT bridge is down ({self.prefix}bridge/state = 0)")

    async def _async_publish(self, topic: str, payload: str) -> None:
        try:
            await mqtt.async_publish(self._hass, topic, payload, qos=1)
        except HomeAssistantError as err:
            raise IotError(f"publish failed: {err}") from err
