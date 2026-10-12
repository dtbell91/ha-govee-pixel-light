"""Govee account REST API (as used by Govee Home 7.7.02): login, device list, AWS IoT key."""
from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp

APP_HOST = "https://app2.govee.com"
APP_VERSION = "7.7.02"
# goodsType 285/286 = "H663X" in the app; the H8630 Gaming Pixel Light is 285.
SUPPORTED_GOODS_TYPES = (285, 286)


class GoveeError(Exception):
    """API request failed."""


class GoveeAuthError(GoveeError):
    """Credentials rejected or the session token expired."""


class GoveeNeedsCode(GoveeError):
    """Govee emailed a verification code; log in again with it."""


@dataclass
class Session:
    token: str
    account_id: str
    account_topic: str
    client_id: str


@dataclass
class PixelDevice:
    device: str  # Govee device id (MAC-like, AA:BB:...)
    sku: str
    name: str
    topic: str  # AWS IoT topic the light listens on
    width: int
    height: int
    sw_version: str
    hw_version: str


def client_id_for(email: str) -> str:
    # Own namespace, so this never collides with other integrations' Govee sessions.
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"govee-pixel-ha:{email.strip().lower()}").hex


def _headers(client_id: str, token: str | None = None) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "appVersion": APP_VERSION,
        "clientId": client_id,
        "clientType": "1",
        "iotVersion": "0",
        "timestamp": str(int(time.time() * 1000)),
        "User-Agent": f"GoveeHome/{APP_VERSION} (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class GoveeApi:
    def __init__(self, http: aiohttp.ClientSession, email: str, password: str, client_id: str | None = None) -> None:
        self._http = http
        self.email = email
        self.password = password
        self.client_id = client_id or client_id_for(email)
        self.session: Session | None = None

    async def _call(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> dict[str, Any]:
        token = self.session.token if (auth and self.session) else None
        try:
            async with self._http.request(method, f"{APP_HOST}{path}", json=body,
                                          headers=_headers(self.client_id, token),
                                          timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 401:
                    raise GoveeAuthError("token rejected")
                data = await r.json(content_type=None)
        except aiohttp.ClientError as err:
            raise GoveeError(f"{path}: {err}") from err
        if not isinstance(data, dict):
            raise GoveeError(f"{path}: unexpected response")
        if data.get("status") == 401:
            raise GoveeAuthError(data.get("message") or "unauthorised")
        return data

    async def request_code(self) -> None:
        await self._call("POST", "/account/rest/account/v1/verification", {"type": 8, "email": self.email}, auth=False)

    async def login(self, code: str | None = None) -> Session:
        body = {"email": self.email, "password": self.password, "client": self.client_id, "clientType": "1"}
        if code:
            body["code"] = code.strip()
        data = await self._call("POST", "/account/rest/account/v2/login", body, auth=False)
        if data.get("status") == 454 and not code:
            await self.request_code()
            raise GoveeNeedsCode("verification code required")
        client = data.get("client") or {}
        if not client.get("token"):
            raise GoveeAuthError(data.get("message") or f"login failed (status {data.get('status')})")
        self.session = Session(client["token"], str(client.get("accountId", "")), client.get("topic", ""),
                               self.client_id)
        return self.session

    async def devices(self) -> list[PixelDevice]:
        data = await self._call("POST", "/device/rest/devices/v1/list", {})
        out = []
        for dev in data.get("devices") or []:
            if dev.get("goodsType") not in SUPPORTED_GOODS_TYPES:
                continue
            settings = (dev.get("deviceExt") or {}).get("deviceSettings") or {}
            if isinstance(settings, str):
                settings = json.loads(settings)
            ic = int(settings.get("ic") or 1024)
            side = int(ic ** 0.5) if int(ic ** 0.5) ** 2 == ic else 32
            out.append(PixelDevice(
                device=dev["device"], sku=dev["sku"], name=dev.get("deviceName") or dev["sku"],
                topic=settings["topic"], width=side, height=side,
                sw_version=settings.get("wifiSoftVersion") or dev.get("versionSoft", ""),
                hw_version=settings.get("wifiHardVersion") or dev.get("versionHard", ""),
            ))
        return out

    async def iot_key(self) -> tuple[str, bytes, str]:
        """(endpoint, p12 bytes, p12 password) for the account's AWS IoT client certificate."""
        data = (await self._call("GET", "/app/v1/account/iot/key")).get("data") or {}
        if not data.get("p12"):
            raise GoveeError("no IoT key in response")
        return data["endpoint"], base64.b64decode(data["p12"]), data["p12Pass"]
