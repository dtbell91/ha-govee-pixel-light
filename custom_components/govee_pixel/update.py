"""Update entity for pixeld, the custom firmware's display daemon (pixeld connection only).

New builds are the GitHub releases of dtbell91/pixeld. To install one, Home Assistant downloads the
armv7 binary, checks it against the release's SHA-256 and serves it to the light (the same way as
effect files); the install request goes over MQTT with the URL and checksum. The light runs the build on
trial: it keeps it once it has been healthy for a minute, and otherwise goes back to the build it had.
The light itself never reaches the internet.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import GoveePixelConfigEntry
from .const import CONF_CONNECTION, CONNECTION_PIXELD, DOMAIN
from .display import file_url
from .effect_server import async_get_store
from .iot import IotError

_LOGGER = logging.getLogger(__name__)

REPO = "dtbell91/pixeld"
API = f"https://api.github.com/repos/{REPO}/releases"
ASSET = "pixeld-linux-armv7"
CHECK_EVERY = timedelta(hours=6)
INSTALL_TIMEOUT = 600  # s: send, restart, a minute's trial, confirm
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    if entry.data.get(CONF_CONNECTION) != CONNECTION_PIXELD:
        return
    async_add_entities(PixeldUpdate(entry, dev_id) for dev_id in entry.runtime_data.devices)


class PixeldUpdate(UpdateEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "pixeld"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
                                | UpdateEntityFeature.RELEASE_NOTES | UpdateEntityFeature.SPECIFIC_VERSION)

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        self.entry = entry
        self.link = entry.runtime_data.iot
        self._attr_unique_id = f"{dev_id}_pixeld_update"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dev_id)})
        self._release: dict[str, Any] | None = None
        self._attr_in_progress = False
        self._outcome: asyncio.Future[None] | None = None
        self._target: str | None = None

    @property
    def available(self) -> bool:
        return self.link.available

    @property
    def installed_version(self) -> str | None:
        return (self.link.info or {}).get("pixeld")

    @property
    def latest_version(self) -> str | None:
        return self._release["tag_name"] if self._release else self.installed_version

    @property
    def release_url(self) -> str | None:
        return self._release.get("html_url") if self._release else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info, update = self.link.info or {}, self.link.update_state or {}
        return {"running": info.get("slot"), "light_supports_updates": bool(info.get("updates")),
                "last_update": update.get("state"), "last_update_detail": update.get("detail")}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.link.async_add_info_listener(self._on_info))
        self.async_on_remove(async_track_time_interval(self.hass, self._async_check, CHECK_EVERY))
        self.hass.async_create_background_task(self._async_check(), f"{DOMAIN} pixeld release check")

    async def async_update(self) -> None:
        """homeassistant.update_entity: look for a new release now."""
        await self._async_check()

    async def async_release_notes(self) -> str | None:
        return (self._release or {}).get("body")

    # ---- releases ----
    async def _async_get_json(self, url: str) -> Any:
        session = async_get_clientsession(self.hass)
        async with session.get(url, headers={"Accept": "application/vnd.github+json"},
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _async_check(self, *_: Any) -> None:
        try:
            self._release = await self._async_get_json(f"{API}/latest")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("pixeld release check failed: %s", err)
            return
        self.async_write_ha_state()

    async def _async_download(self, release: dict[str, Any]) -> tuple[bytes, str]:
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        if ASSET not in assets or f"{ASSET}.sha256" not in assets:
            raise HomeAssistantError(f"Release {release.get('tag_name')} has no {ASSET} build")
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(assets[f"{ASSET}.sha256"], timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                match = _SHA256.search(await resp.text())
            async with session.get(assets[ASSET], timeout=aiohttp.ClientTimeout(total=300)) as resp:
                resp.raise_for_status()
                binary = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Download failed: {err}") from err
        if not match:
            raise HomeAssistantError("The release's checksum file is unreadable")
        sha = hashlib.sha256(binary).hexdigest()
        if sha != match.group(0):
            raise HomeAssistantError("Downloaded build doesn't match the release's checksum")
        return binary, sha

    # ---- install ----
    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if not (self.link.info or {}).get("updates"):
            raise HomeAssistantError("This light's firmware can't install pixeld updates yet (needs the updated "
                                     "native layer)")
        if version and version != (self._release or {}).get("tag_name"):
            try:
                release = await self._async_get_json(f"{API}/tags/{version}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise HomeAssistantError(f"No pixeld release {version}: {err}") from err
        elif self._release:
            release = self._release
        else:
            raise HomeAssistantError("No pixeld release known yet")
        binary, sha = await self._async_download(release)
        url = file_url(self.hass, self.entry, async_get_store(self.hass).add(binary))  # served for 10 min
        self._target = release["tag_name"]
        self._outcome = self.hass.loop.create_future()
        self._attr_in_progress = True
        self.async_write_ha_state()
        try:
            await self.link.async_send_update(url, sha)
            async with asyncio.timeout(INSTALL_TIMEOUT):
                await self._outcome
        except IotError as err:
            raise HomeAssistantError(f"Could not send the build to the light: {err}") from err
        except TimeoutError as err:
            raise HomeAssistantError(f"The light didn't confirm pixeld {self._target} in time") from err
        finally:
            self._attr_in_progress = False
            self._outcome = None
            self.async_write_ha_state()
            self.hass.async_create_background_task(self._async_check(), f"{DOMAIN} pixeld release check")

    @callback
    def _on_info(self) -> None:
        state = self.link.update_state or {}
        if self._outcome is not None and not self._outcome.done():
            match state.get("state"):
                case "ok" if state.get("version") == self._target:
                    self._outcome.set_result(None)
                case "failed" | "rolled_back":
                    self._outcome.set_exception(HomeAssistantError(
                        f"pixeld update {state['state'].replace('_', ' ')}: {state.get('detail') or ''}".strip(": ")))
        self.async_write_ha_state()
