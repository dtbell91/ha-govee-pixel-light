"""MQTT command link to the light: sends `ptUrl`/`status`/`ptReal` and waits for its replies.

The light only installs a custom image by downloading it (ptUrl); the URL may point anywhere on
the LAN as long as the response carries `ETag: "<md5 of body>"`. The command itself has to go
over MQTT: the light's LAN API has no ptUrl handler. Two transports share the protocol here:
`GoveeIot` (Govee's AWS IoT broker, account certificate) and `local_mqtt.LocalMqttLink` (Home
Assistant's own broker, for lights running the local-MQTT custom firmware).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

_LOGGER = logging.getLogger(__name__)

# ptUrl V4 header: FC 01 <len(op)+4> | timeout LE16 (60 s) | isNeedCache | len(op) | op
# op 0x58 = one graffiti/GIF effect, op 0x61 = a looplay playlist file (Govee sends isNeedCache=1 for those).
OP_EFFECT = 0x58
OP_PLAYLIST = 0x61
# The light retries a failing download until that 60 s timeout, then reports failure.
PTURL_REPLY_TIMEOUT = 75
# Switch to the DIY/graffiti scene: 33 05 0A FE 00 (sub-mode 0x0A, code 254)
MODE_PACKET = bytes([0x33, 0x05, 0x0A, 0xFE, 0x00])


def pturl_head(op: int, need_cache: bool = False, timeout_s: int = 60) -> bytes:
    return bytes([0xFC, 0x01, 0x05]) + timeout_s.to_bytes(2, "little") + bytes([int(need_cache), 0x01, op])


class IotError(Exception):
    """MQTT command failed."""


def frame20(data: bytes) -> bytes:
    """Pad to 19 bytes and append the XOR checksum (Govee BLE-style 20-byte frame)."""
    body = data.ljust(19, b"\x00")
    cs = 0
    for b in body:
        cs ^= b
    return body + bytes([cs])


def write_cert_files(directory: Path, p12: bytes, password: str) -> tuple[str, str]:
    """Unpack the account p12 into PEM files (mode 0600). Blocking."""
    key, cert, _ = pkcs12.load_key_and_certificates(p12, password.encode())
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    for path, content in (
        (cert_path, cert.public_bytes(serialization.Encoding.PEM)),
        (key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())),
    ):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    return str(cert_path), str(key_path)


class CommandLink:
    """The light's MQTT request/reply protocol, independent of how messages are carried.

    Subclasses publish via `_async_publish`, ensure a live connection in `_async_wait_connected`
    and feed every message from the light's reply topic to `_handle_reply` (on the event loop).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._pending: dict[str, asyncio.Future] = {}
        self._report_listeners: list[Callable[[dict], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._availability_listeners: list[Callable[[], None]] = []

    async def _async_wait_connected(self) -> None:
        raise NotImplementedError

    async def _async_publish(self, topic: str, payload: str) -> None:
        raise NotImplementedError

    async def async_stop(self) -> None:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Whether commands can currently reach the light."""
        return True

    def async_add_report_listener(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        """Call `cb(payload)` (on the event loop) for every message from the light: replies and unsolicited
        reports alike (e.g. {"cmd": "brightness", "state": {"brightness": 90}} after any change)."""
        self._report_listeners.append(cb)
        return lambda: self._report_listeners.remove(cb)

    def async_add_availability_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._availability_listeners.append(cb)
        return lambda: self._availability_listeners.remove(cb)

    def _availability_changed(self) -> None:
        for cb in list(self._availability_listeners):
            cb()

    def _handle_reply(self, raw: bytes | str) -> None:
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        if tx := payload.get("transaction"):
            self._resolve(tx, payload)
        for cb in list(self._report_listeners):
            cb(payload)

    def _resolve(self, tx: str, payload: dict) -> None:
        fut = self._pending.pop(tx, None)
        if fut and not fut.done():
            fut.set_result(payload)
        elif payload.get("cmd"):
            _LOGGER.debug("Unmatched %s reply: %s", payload.get("cmd"), payload.get("op"))

    # --- commands ---
    async def async_command(self, topic: str, cmd: str, data: dict, timeout: float = 30,
                            msg_type: int = 1) -> dict | None:
        """Publish a command to the light's topic; return its reply (None if it never answered)."""
        await self._async_wait_connected()
        tx = f"v_{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"
        fut = self._loop.create_future()
        self._pending[tx] = fut
        msg = {"msg": {"cmd": cmd, "data": data, "cmdVersion": 0, "transaction": tx, "type": msg_type}}
        try:
            await self._async_publish(topic, json.dumps(msg))
        except IotError:
            self._pending.pop(tx, None)
            raise
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(tx, None)

    async def async_pt_url(self, topic: str, url: str, timeout: float = PTURL_REPLY_TIMEOUT, *,
                           op: int = OP_EFFECT, mode_packet: bytes = MODE_PACKET,
                           need_cache: bool = False) -> bool | None:
        """Tell the light to download `url` (an effect, or a playlist with op=OP_PLAYLIST) and apply it.

        Returns True if the light reports a successful download, False if it reports a failure,
        None if it did not answer in time.
        """
        b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
        data = {"value": [b64(pturl_head(op, need_cache)), b64(url.encode()), b64(frame20(mode_packet))]}
        reply = await self.async_command(topic, "ptUrl", data, timeout)
        if reply is None:
            return None
        try:
            ack = base64.b64decode(reply["op"]["command"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            _LOGGER.debug("Unexpected ptUrl reply: %s", reply)
            return None
        # 33 05 <result>: 00 = downloaded and shown, 01 = download failed
        ok = ack[:2] == b"\x33\x05" and ack[2] == 0
        if ok:
            # The light doesn't push its new mode after showing content; ask, so state listeners see it.
            task = self._loop.create_task(self._async_status_quietly(topic))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return ok

    async def _async_status_quietly(self, topic: str) -> None:
        try:
            await self.async_status_reply(topic)
        except IotError as err:
            _LOGGER.debug("Status refresh after ptUrl failed: %s", err)

    async def async_status_reply(self, topic: str, timeout: float = 10) -> dict | None:
        """The light's raw status report (sku, device, versions, state, op), or None if no reply.

        Sent as type 0: with type 1 the light answers with an all-zero placeholder state
        (onOff 0, empty op) instead of its real state (seen on firmware 1.00.91).
        """
        return await self.async_command(topic, "status", {}, timeout, msg_type=0)

    async def async_status(self, topic: str, timeout: float = 10) -> dict | None:
        """Ask the light for its state: {"on": bool | None, "mode": bytes | None} (None if no reply).

        "mode" is the light's echo of its current mode packet, e.g. aa 05 0a fe 00 .. for one of our
        images, aa 05 82 .. while its own playlist rotates.
        """
        reply = await self.async_status_reply(topic, timeout)
        if reply is None:
            return None
        on = (reply.get("state") or {}).get("onOff")
        mode = None
        for item in (reply.get("op") or {}).get("command") or []:
            try:
                frame = base64.b64decode(item)
            except ValueError:
                continue
            if frame[:2] == b"\xaa\x05":
                mode = frame
                break
        return {"on": None if on is None else bool(on), "mode": mode}

    async def async_pt_real(self, topic: str, frames: list[bytes]) -> None:
        """Send raw 20-byte frames (checksum added), e.g. 33 01 01 = power on."""
        b64 = lambda b: base64.b64encode(frame20(b)).decode()  # noqa: E731
        await self.async_command(topic, "ptReal", {"command": [b64(f) for f in frames]}, timeout=0.1)

    async def async_set_power(self, topic: str, on: bool) -> None:
        """33 01 01 / 33 01 00. The light then reports its full status."""
        await self.async_pt_real(topic, [bytes([0x33, 0x01, 0x01 if on else 0x00])])

    async def async_set_brightness(self, topic: str, percent: int) -> None:
        """33 04 <1-100>. The light then reports {"cmd": "brightness", "state": {"brightness": n}}."""
        await self.async_pt_real(topic, [bytes([0x33, 0x04, max(1, min(100, int(percent)))])])


class GoveeIot(CommandLink):
    """Commands via Govee's AWS IoT broker, authenticated with the account's IoT certificate."""

    def __init__(self, loop: asyncio.AbstractEventLoop, endpoint: str, cert: str, key: str,
                 account_id: str, account_topic: str) -> None:
        super().__init__(loop)
        self._endpoint = endpoint
        self._cert, self._key = cert, key
        self._account_topic = account_topic
        self._connected = asyncio.Event()
        client_id = f"AP/{account_id}/{uuid.uuid4().hex}"
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:  # paho-mqtt < 2
            self._client = mqtt.Client(client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(1, 60)

    # --- paho callbacks (paho's network thread) ---
    def _on_connect(self, client, userdata, flags, rc, props=None):
        if getattr(rc, "is_failure", rc != 0):
            _LOGGER.warning("Govee MQTT connect refused: %s", rc)
            return
        if self._account_topic:
            client.subscribe(self._account_topic, qos=0)
        self._loop.call_soon_threadsafe(self._set_connected, True)

    def _on_disconnect(self, client, userdata, *args):
        self._loop.call_soon_threadsafe(self._set_connected, False)

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected.is_set():
            return
        (self._connected.set if connected else self._connected.clear)()
        self._availability_changed()

    @property
    def available(self) -> bool:
        return self._connected.is_set()

    def _on_message(self, client, userdata, msg):
        self._loop.call_soon_threadsafe(self._handle_reply, msg.payload)

    # --- lifecycle ---
    def _connect_blocking(self) -> None:
        ctx = ssl.create_default_context()
        ctx.load_cert_chain(self._cert, self._key)
        self._client.tls_set_context(ctx)
        self._client.connect_async(self._endpoint, 8883, keepalive=60)
        self._client.loop_start()

    async def async_start(self, timeout: float = 20) -> None:
        await self._loop.run_in_executor(None, self._connect_blocking)
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError as err:
            await self.async_stop()
            raise IotError("timed out connecting to Govee MQTT") from err

    async def async_stop(self) -> None:
        def stop():
            self._client.disconnect()
            self._client.loop_stop()
        await self._loop.run_in_executor(None, stop)

    async def _async_wait_connected(self) -> None:
        if not self._connected.is_set():
            try:
                await asyncio.wait_for(self._connected.wait(), 15)
            except asyncio.TimeoutError as err:
                raise IotError("not connected to Govee MQTT") from err

    async def _async_publish(self, topic: str, payload: str) -> None:
        info = self._client.publish(topic, payload, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise IotError(f"publish failed: {mqtt.error_string(info.rc)}")
