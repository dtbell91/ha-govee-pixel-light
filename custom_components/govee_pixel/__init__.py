"""Govee Pixel Light (H8630): show images and GIFs from Home Assistant.

Pipeline: image -> 32x32 PNG frames -> graffiti effect payload -> served by HA with an MD5 ETag
-> `ptUrl` command over Govee's AWS IoT MQTT -> the light downloads it from HA over the LAN.
Only the small trigger command touches Govee's cloud; image bytes stay on the LAN.
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
from homeassistant.helpers.typing import ConfigType

from .api import GoveeApi, GoveeAuthError, GoveeError, GoveeNeedsCode, PixelDevice, Session
from .const import CONF_ACCOUNT_ID, CONF_CLIENT_ID, CONF_ACCOUNT_TOPIC, CONF_BASE_URL, CONF_TOKEN, DOMAIN
from .effect_server import URL_PATH, async_get_store
from .iot import GoveeIot, IotError, write_cert_files
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.IMAGE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class GoveePixelData:
    api: GoveeApi
    iot: GoveeIot
    devices: dict[str, PixelDevice]  # by Govee device id
    previews: dict[str, tuple[bytes, float]] = field(default_factory=dict)  # last sent GIF per device
    listeners: list = field(default_factory=list)


type GoveePixelConfigEntry = ConfigEntry[GoveePixelData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


def effect_base_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Base URL the light uses to download effects (must be reachable from the light, plain HTTP is fine)."""
    if base := entry.options.get(CONF_BASE_URL):
        return base.rstrip("/")
    api = hass.config.api
    scheme = "https" if api.use_ssl else "http"
    return f"{scheme}://{api.local_ip}:{api.port}"


def effect_url(hass: HomeAssistant, entry: ConfigEntry, key: str) -> str:
    return f"{effect_base_url(hass, entry)}{URL_PATH}/{key}.bin"


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

    entry.runtime_data = GoveePixelData(api, iot, {d.device: d for d in devices})
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("Effects will be served from %s", effect_base_url(hass, entry))
    return True


async def _async_reload(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.iot.async_stop()
    return ok
