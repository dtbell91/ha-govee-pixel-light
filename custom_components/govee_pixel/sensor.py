"""Sensors: playlist status (state + items as attributes) and what the display is showing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .device_state import SHOWING_OPTIONS
from .entity import LightStateEntity, PlaylistEntity
from .playlist import (
    STATUS_ERROR, STATUS_HA, STATUS_INTERRUPTED, STATUS_LIGHT, STATUS_PAUSED, STATUS_STOPPED,
)


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PlaylistStatus(c, "playlist_status") for c in entry.runtime_data.playlists.values())
    async_add_entities(DisplaySensor(entry, dev_id) for dev_id in entry.runtime_data.devices)


def _ts(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


class PlaylistStatus(PlaylistEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_STOPPED, STATUS_LIGHT, STATUS_HA, STATUS_INTERRUPTED, STATUS_PAUSED, STATUS_ERROR]
    _attr_icon = "mdi:playlist-star"

    @property
    def native_value(self) -> str:
        return self.controller.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        return {
            "items": [{"name": i["name"], "type": i["type"], "duration": i["duration"]} for i in c.items],
            "item_count": len(c.items),
            "playback": c.playback,
            "resume_at": _ts(c.interrupt_until),
            "last_push": _ts(c.last_push),
            "last_error": c.last_error,
            "light_on": c.light_on,
            "light_mode": c.light_mode,
            "reapply_pending": c.reapply_pending,
        }


class DisplaySensor(LightStateEntity, SensorEntity):
    """What the light shows, from its pushed mode echo: off / playlist / image (ours) / govee_scene."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SHOWING_OPTIONS
    _attr_icon = "mdi:television-ambient-light"

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id, "showing")

    @property
    def native_value(self) -> str | None:
        return self.state_.showing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"mode": self.state_.mode.hex() if self.state_.mode else None,
                "last_report": _ts(self.state_.last_report)}
