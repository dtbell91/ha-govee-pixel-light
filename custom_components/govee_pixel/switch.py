"""Playlist on/off switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .entity import PlaylistEntity


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PlaylistSwitch(c, "playlist") for c in entry.runtime_data.playlists.values())


class PlaylistSwitch(PlaylistEntity, SwitchEntity):
    _attr_icon = "mdi:playlist-play"

    @property
    def is_on(self) -> bool:
        return self.controller.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_active(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_active(False)
