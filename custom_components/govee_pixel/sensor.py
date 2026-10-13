"""Sensors: playlist status (state + items as attributes), what the display is showing, and (custom
firmware with the telemetry layer) diagnostics: WiFi signal, last boot, free memory/storage, firmware."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .device_state import SHOWING_OPTIONS
from .entity import LightStateEntity, PlaylistEntity, TelemetryEntity
from .playlist import (
    STATUS_ERROR, STATUS_HA, STATUS_INTERRUPTED, STATUS_LIGHT, STATUS_PAUSED, STATUS_STOPPED,
)


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PlaylistStatus(c, "playlist_status") for c in entry.runtime_data.playlists.values())
    async_add_entities(DisplaySensor(entry, dev_id) for dev_id in entry.runtime_data.devices)
    if getattr(entry.runtime_data.iot, "telemetry", None) is not None:
        async_add_entities(cls(entry, dev_id) for dev_id in entry.runtime_data.devices
                           for cls in (WifiSignal, LastBoot, MemoryAvailable, StorageFree, Firmware, IpAddress))


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


class TelemetrySensor(TelemetryEntity, SensorEntity):
    """A sensor reading one field of the light's telemetry message."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    key: str
    field: str

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id, self.key)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> Any:
        return (self.telemetry.data or {}).get(self.field)


class WifiSignal(TelemetrySensor):
    key = field = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT


class LastBoot(TelemetrySensor):
    key, field = "last_boot", "uptime"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id)
        self._boot: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        boot = self.telemetry.boot_time
        if boot is None:
            return None
        # Uptime and receive time jitter by a second or two; only move on a real reboot.
        if self._boot is None or abs(self._boot.timestamp() - boot) > 120:
            self._boot = datetime.fromtimestamp(round(boot), timezone.utc)
        return self._boot


class MemoryAvailable(TelemetrySensor):
    key, field = "memory_available", "mem_available_kb"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.KIBIBYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.MEBIBYTES
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT


class StorageFree(MemoryAvailable):
    key, field = "storage_free", "udisk_free_kb"
    _attr_entity_registry_enabled_default = False


class Firmware(TelemetrySensor):
    key = field = "firmware"
    _attr_icon = "mdi:chip"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.telemetry.data or {}
        return {"govee_base": data.get("base"), "profile": data.get("profile"), "slot": data.get("slot")}


class IpAddress(TelemetrySensor):
    key, field = "ip_address", "ip"
    _attr_icon = "mdi:ip-network"
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        return (self.telemetry.data or {}).get("ip") or None
