"""The light's four physical buttons as event entities (from the custom firmware's telemetry layer).

The light still acts on every press itself; these just let automations react too.
"""
from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .entity import TelemetryEntity
from .telemetry import BUTTON_ACTIONS, BUTTONS


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    if getattr(entry.runtime_data.iot, "telemetry", None) is None:
        return
    async_add_entities(ButtonEvent(entry, dev_id, button)
                       for dev_id in entry.runtime_data.devices for button in BUTTONS)


class ButtonEvent(TelemetryEntity, EventEntity):
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = BUTTON_ACTIONS

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str, button: str) -> None:
        super().__init__(entry, dev_id, f"button_{button}")
        self.button = button

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.telemetry.async_add_button_listener(self._on_button))

    @callback
    def _on_button(self, button: str, action: str) -> None:
        if button == self.button:
            self._trigger_event(action)
            self.async_write_ha_state()
