"""Config entry diagnostics (credentials, account ids and device ids redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import GoveePixelConfigEntry
from .const import CONF_ACCOUNT_ID, CONF_ACCOUNT_TOPIC, CONF_CLIENT_ID, CONF_DEVICE_ID, CONF_TOKEN

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, CONF_CLIENT_ID, CONF_ACCOUNT_ID, CONF_ACCOUNT_TOPIC,
             CONF_DEVICE_ID, "topic", "device"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GoveePixelConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    link = data.iot
    devices = []
    for index, (dev_id, dev) in enumerate(data.devices.items()):
        controller = data.playlists.get(dev_id)
        state = data.states.get(dev_id)
        devices.append({
            "index": index,
            "device": dev_id,
            "sku": dev.sku,
            "topic": dev.topic,
            "size": f"{dev.width}x{dev.height}",
            "sw_version": dev.sw_version,
            "hw_version": dev.hw_version,
            "state": state.as_dict() if state else None,
            "playlist": {
                "status": controller.status,
                "active": controller.active,
                "playback": controller.playback,
                "items": [{"name": i["name"], "type": i["type"], "duration": i["duration"]} for i in controller.items],
                "last_push": controller.last_push,
                "last_error": controller.last_error,
                "reapply_pending": controller.reapply_pending,
            } if controller else None,
        })
    return {
        "entry": {"title": entry.title, "data": async_redact_data(dict(entry.data), TO_REDACT),
                  "options": dict(entry.options)},
        "link": {
            "type": type(link).__name__,
            "available": link.available,
            "bridge_up": getattr(link, "bridge_up", None),
            "light_connected": getattr(link, "light_connected", None),
            "telemetry": telemetry.as_dict() if (telemetry := getattr(link, "telemetry", None)) else None,
        },
        "devices": async_redact_data(devices, TO_REDACT),
    }
