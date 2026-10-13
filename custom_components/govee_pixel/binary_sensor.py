"""Headphones plugged into the light (from the custom firmware's telemetry layer)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .entity import TelemetryEntity


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    if getattr(entry.runtime_data.iot, "telemetry", None) is None:
        return
    async_add_entities(Headphones(entry, dev_id) for dev_id in entry.runtime_data.devices)


class Headphones(TelemetryEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:headphones"

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id, "headphones")

    @property
    def available(self) -> bool:
        return super().available and self.telemetry.jack is not None

    @property
    def is_on(self) -> bool | None:
        return bool(self.telemetry.jack.get("headphones")) if self.telemetry.jack else None
