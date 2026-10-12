"""Govee AWS IoT MQTT link: sends the `ptUrl` command and waits for the light's download result.

The light only installs a custom image by downloading it (ptUrl); the URL may point anywhere on
the LAN as long as the response carries `ETag: "<md5 of body>"`. The command itself has to go
through Govee's MQTT broker: the light's LAN API has no ptUrl handler.
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
from pathlib import Path

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

_LOGGER = logging.getLogger(__name__)

# V4 ptUrl header (pactType 2 / pactCode 3): FC 01 05 | timeout LE16 (60 s) | isNeedCache 0 | 01 58
PTURL_HEAD_V4 = bytes([0xFC, 0x01, 0x05, 0x3C, 0x00, 0x00, 0x01, 0x58])
# The light retries a failing download until the 60 s timeout above, then reports failure.
PTURL_REPLY_TIMEOUT = 75
# Switch to the DIY/graffiti scene: 33 05 0A FE 00 (sub-mode 0x0A, code 254)
MODE_PACKET = bytes([0x33, 0x05, 0x0A, 0xFE, 0x00])


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


class GoveeIot:
    def __init__(self, loop: asyncio.AbstractEventLoop, endpoint: str, cert: str, key: str,
                 account_id: str, account_topic: str) -> None:
        self._loop = loop
        self._endpoint = endpoint
        self._cert, self._key = cert, key
        self._account_topic = account_topic
        self._pending: dict[str, asyncio.Future] = {}
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
        self._loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(self, client, userdata, *args):
        self._loop.call_soon_threadsafe(self._connected.clear)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        tx = payload.get("transaction")
        if tx:
            self._loop.call_soon_threadsafe(self._resolve, tx, payload)

    def _resolve(self, tx: str, payload: dict) -> None:
        fut = self._pending.pop(tx, None)
        if fut and not fut.done():
            fut.set_result(payload)
        elif payload.get("cmd"):
            _LOGGER.debug("Unmatched %s reply: %s", payload.get("cmd"), payload.get("op"))

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

    # --- commands ---
    async def async_command(self, topic: str, cmd: str, data: dict, timeout: float = 30) -> dict | None:
        """Publish a command to the light's topic; return its reply (None if it never answered)."""
        if not self._connected.is_set():
            try:
                await asyncio.wait_for(self._connected.wait(), 15)
            except asyncio.TimeoutError as err:
                raise IotError("not connected to Govee MQTT") from err
        tx = f"v_{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"
        fut = self._loop.create_future()
        self._pending[tx] = fut
        msg = {"msg": {"cmd": cmd, "data": data, "cmdVersion": 0, "transaction": tx, "type": 1}}
        info = self._client.publish(topic, json.dumps(msg), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self._pending.pop(tx, None)
            raise IotError(f"publish failed: {mqtt.error_string(info.rc)}")
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(tx, None)

    async def async_pt_url(self, topic: str, url: str, timeout: float = PTURL_REPLY_TIMEOUT) -> bool | None:
        """Tell the light to download and show the effect at `url`.

        Returns True if the light reports a successful download, False if it reports a failure,
        None if it did not answer in time.
        """
        b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
        data = {"value": [b64(PTURL_HEAD_V4), b64(url.encode()), b64(frame20(MODE_PACKET))]}
        reply = await self.async_command(topic, "ptUrl", data, timeout)
        if reply is None:
            return None
        try:
            ack = base64.b64decode(reply["op"]["command"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            _LOGGER.debug("Unexpected ptUrl reply: %s", reply)
            return None
        # 33 05 <result>: 00 = downloaded and shown, 01 = download failed
        return ack[:2] == b"\x33\x05" and ack[2] == 0

    async def async_pt_real(self, topic: str, frames: list[bytes]) -> None:
        """Send raw 20-byte frames (checksum added), e.g. 33 01 01 = power on."""
        b64 = lambda b: base64.b64encode(frame20(b)).decode()  # noqa: E731
        await self.async_command(topic, "ptReal", {"command": [b64(f) for f in frames]}, timeout=0.1)
