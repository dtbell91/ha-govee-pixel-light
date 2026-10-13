"""Bases for entities that follow a light's playlist controller or its live state."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .iot import CommandLink
from .playlist import PlaylistController

if TYPE_CHECKING:
    from . import GoveePixelConfigEntry


class PlaylistEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: PlaylistController, key: str) -> None:
        self.controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = f"{controller.dev.device}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, controller.dev.device)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_add_listener(self.async_write_ha_state))


class LightStateEntity(Entity):
    """Base for entities that follow the light's live (pushed) state and its link's availability."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str, key: str) -> None:
        self.entry = entry
        self.dev = entry.runtime_data.devices[dev_id]
        self.state_ = entry.runtime_data.states[dev_id]
        self.controller = entry.runtime_data.playlists[dev_id]
        self._attr_translation_key = key
        self._attr_unique_id = f"{dev_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dev_id)})

    @property
    def iot(self) -> CommandLink:
        return self.entry.runtime_data.iot

    @property
    def available(self) -> bool:
        return self.iot.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.state_.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self.iot.async_add_availability_listener(self.async_write_ha_state))


class TelemetryEntity(Entity):
    """Base for entities fed by the custom firmware's telemetry layer (local-MQTT entries only)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str, key: str) -> None:
        self.entry = entry
        self.telemetry = entry.runtime_data.iot.telemetry
        self._attr_translation_key = key
        self._attr_unique_id = f"{dev_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dev_id)})

    @property
    def available(self) -> bool:
        return self.entry.runtime_data.iot.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.telemetry.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self.entry.runtime_data.iot.async_add_availability_listener(self.async_write_ha_state))
