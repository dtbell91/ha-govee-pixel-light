"""Serve effect payloads to the light.

The light downloads the effect itself and rejects the file unless the response carries
`ETag: "<lowercase hex md5 of the body>"` (checked in the firmware's govee_iot). HA's /local/
sends an mtime-based ETag, hence this dedicated, unauthenticated view with random one-off keys.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from aiohttp import web

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import HomeAssistantView

from .const import DOMAIN, EFFECT_TTL

URL_PATH = f"/api/{DOMAIN}/effect"


class EffectStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, str, float]] = {}

    @callback
    def add(self, body: bytes) -> str:
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if v[2] > now}
        key = secrets.token_urlsafe(16)
        self._items[key] = (body, hashlib.md5(body).hexdigest(), now + EFFECT_TTL)
        return key

    @callback
    def get(self, key: str) -> tuple[bytes, str] | None:
        item = self._items.get(key)
        if item is None or item[2] < time.monotonic():
            return None
        return item[0], item[1]


class GoveeEffectView(HomeAssistantView):
    url = URL_PATH + "/{key}"
    name = f"api:{DOMAIN}:effect"
    requires_auth = False

    def __init__(self, store: EffectStore) -> None:
        self._store = store

    async def get(self, request: web.Request, key: str) -> web.Response:
        item = self._store.get(key.removesuffix(".bin"))
        if item is None:
            return web.Response(status=404)
        body, md5 = item
        return web.Response(body=body, content_type="text/plain", headers={"ETag": f'"{md5}"'})


def async_get_store(hass: HomeAssistant) -> EffectStore:
    if (store := hass.data.get(f"{DOMAIN}_store")) is None:
        store = hass.data[f"{DOMAIN}_store"] = EffectStore()
        hass.http.register_view(GoveeEffectView(store))
    return store
