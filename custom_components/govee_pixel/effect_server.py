"""Serve effect and playlist files to the light.

The light downloads everything itself and rejects a file unless the response carries
`ETag: "<lowercase hex md5 of the body>"` (checked in the firmware's govee_iot). Files are
content-addressed (`/api/govee_pixel/f/<md5>.bin`), so a URL always means the same bytes: the
light caches playlist items by URL, and unchanged items keep their URL across playlist edits.

- One-off images live in memory for EFFECT_TTL.
- Files pinned by a playlist are also written to disk and served until no playlist uses them,
  because the light may fetch them again at any time (e.g. after a reboot).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import HomeAssistantView

from .const import DOMAIN, EFFECT_TTL

_LOGGER = logging.getLogger(__name__)

URL_PATH = f"/api/{DOMAIN}/f"
_KEY = re.compile(r"^[0-9a-f]{32}$")
ORPHAN_GRACE = 7 * 24 * 3600


class FileStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._dir = Path(hass.config.path(".storage", DOMAIN, "files"))
        self._mem: dict[str, bytes] = {}
        self._expiry: dict[str, float] = {}  # unpinned files
        self._pins: dict[str, set[str]] = {}  # owner -> keys

    @callback
    def add(self, body: bytes) -> str:
        """Make `body` downloadable for at least EFFECT_TTL; returns its key (md5)."""
        key = hashlib.md5(body).hexdigest()
        self._mem[key] = body
        if not self._pinned(key):
            self._expiry[key] = time.monotonic() + EFFECT_TTL
        self._prune()
        return key

    def _pinned(self, key: str) -> bool:
        return any(key in keys for keys in self._pins.values())

    @callback
    def _prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, t in self._expiry.items() if t < now]:
            del self._expiry[key]
            if not self._pinned(key):
                self._mem.pop(key, None)

    async def async_pin(self, owner: str, keys: set[str]) -> None:
        """Keep exactly `keys` for `owner` (persisted to disk); release files nobody pins any more."""
        self._pins[owner] = set(keys)
        for key in keys:
            self._expiry.pop(key, None)
        wanted = set().union(*self._pins.values())
        to_write = {k: self._mem[k] for k in keys if k in self._mem}
        await self._hass.async_add_executor_job(self._sync_disk, to_write, wanted)
        self._prune()

    async def async_unpin(self, owner: str) -> None:
        await self.async_pin(owner, set())
        self._pins.pop(owner, None)

    def _sync_disk(self, to_write: dict[str, bytes], wanted: set[str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        for key, body in to_write.items():
            path = self._dir / f"{key}.bin"
            if not path.exists():
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(body)
                os.replace(tmp, path)
        # Unreferenced files get a grace period: another light's playlist may not have re-pinned yet.
        cutoff = time.time() - ORPHAN_GRACE
        for path in self._dir.glob("*.bin"):
            if path.stem not in wanted and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    async def async_get(self, key: str) -> bytes | None:
        if (body := self._mem.get(key)) is not None:
            return body
        if not self._pinned(key):
            return None
        try:
            body = await self._hass.async_add_executor_job((self._dir / f"{key}.bin").read_bytes)
        except OSError:
            return None
        self._mem[key] = body
        return body


class GoveeFileView(HomeAssistantView):
    url = URL_PATH + "/{name}"
    name = f"api:{DOMAIN}:file"
    requires_auth = False

    def __init__(self, store: FileStore) -> None:
        self._store = store

    async def get(self, request: web.Request, name: str) -> web.Response:
        key = name.removesuffix(".bin")
        if not _KEY.match(key) or (body := await self._store.async_get(key)) is None:
            return web.Response(status=404)
        _LOGGER.debug("Serving %s (%d bytes) to %s", key, len(body), request.remote)
        return web.Response(body=body, content_type="text/plain", headers={"ETag": f'"{key}"'})


def async_get_store(hass: HomeAssistant) -> FileStore:
    if (store := hass.data.get(f"{DOMAIN}_store")) is None:
        store = hass.data[f"{DOMAIN}_store"] = FileStore(hass)
        hass.http.register_view(GoveeFileView(store))
    return store
