"""Govee Pixel Light (H8630): show images, GIFs and playlists from Home Assistant.

Pipeline: content -> 32x32 effect file (PNG frames or GIF) -> served by HA with an MD5 ETag
-> `ptUrl` command over Govee's AWS IoT MQTT -> the light downloads it from HA over the LAN.
Playlists use the light's own rotation: HA serves a playlist file plus one file per item.
Only the small trigger commands touch Govee's cloud; image bytes stay on the LAN.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import GoveeApi, GoveeAuthError, GoveeError, GoveeNeedsCode, PixelDevice, Session
from .const import CONF_ACCOUNT_ID, CONF_ACCOUNT_TOPIC, CONF_CLIENT_ID, CONF_TOKEN, DOMAIN
from .display import effect_base_url
from .effect_server import async_get_store
from .iot import GoveeIot, IotError, write_cert_files
from .playlist import PlaylistController
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.IMAGE, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class GoveePixelData:
    api: GoveeApi
    iot: GoveeIot
    devices: dict[str, PixelDevice]  # by Govee device id
    previews: dict[str, tuple[bytes, float]] = field(default_factory=dict)  # last sent GIF per device
    playlists: dict[str, PlaylistController] = field(default_factory=dict)  # by Govee device id


type GoveePixelConfigEntry = ConfigEntry[GoveePixelData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> bool:
    api = GoveeApi(async_get_clientsession(hass), entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD],
                   entry.data.get(CONF_CLIENT_ID))
    api.session = Session(entry.data[CONF_TOKEN], entry.data[CONF_ACCOUNT_ID],
                          entry.data[CONF_ACCOUNT_TOPIC], api.client_id)
    try:
        try:
            devices = await api.devices()
        except GoveeAuthError:
            session = await api.login()
            hass.config_entries.async_update_entry(entry, data={
                **entry.data, CONF_TOKEN: session.token, CONF_ACCOUNT_ID: session.account_id,
                CONF_ACCOUNT_TOPIC: session.account_topic})
            devices = await api.devices()
        endpoint, p12, p12_pass = await api.iot_key()
    except (GoveeAuthError, GoveeNeedsCode) as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GoveeError as err:
        raise ConfigEntryNotReady(str(err)) from err

    cert_dir = Path(hass.config.path(".storage", DOMAIN, entry.entry_id))
    cert, key = await hass.async_add_executor_job(write_cert_files, cert_dir, p12, p12_pass)
    iot = GoveeIot(hass.loop, endpoint, cert, key, api.session.account_id, api.session.account_topic)
    try:
        await iot.async_start()
    except IotError as err:
        raise ConfigEntryNotReady(str(err)) from err

    async_get_store(hass)
    dev_reg = dr.async_get(hass)
    for dev in devices:
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, dev.device)},
            manufacturer="Govee", model=dev.sku, name=dev.name,
            sw_version=dev.sw_version, hw_version=dev.hw_version)

    data = entry.runtime_data = GoveePixelData(api, iot, {d.device: d for d in devices})
    store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.playlists")
    saved = (await store.async_load() or {}).get("devices", {})

    def save() -> None:
        store.async_delay_save(lambda: {"devices": {k: c.as_dict() for k, c in data.playlists.items()}}, 1)

    for dev in devices:
        data.playlists[dev.device] = PlaylistController(hass, entry, dev, saved.get(dev.device), save)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for controller in data.playlists.values():
        await controller.async_start()
    _LOGGER.debug("Effects will be served from %s", effect_base_url(hass, entry))
    return True


async def _async_reload(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        for controller in entry.runtime_data.playlists.values():
            await controller.async_stop()
        await entry.runtime_data.iot.async_stop()
    return ok
