"""Playlist on/off switch, and (pixeld) holding the rotation on the item showing."""
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
    async_add_entities(HoldSwitch(c, "playlist_hold") for c in entry.runtime_data.playlists.values()
                       if c.playback_controls)


class PlaylistSwitch(PlaylistEntity, SwitchEntity):
    _attr_icon = "mdi:playlist-play"

    @property
    def is_on(self) -> bool:
        return self.controller.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_active(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_active(False)


class HoldSwitch(PlaylistEntity, SwitchEntity):
    """On: the light stays on the playlist item showing; next / previous / picking an item still move it."""

    _attr_icon = "mdi:pause-circle-outline"

    @property
    def available(self) -> bool:
        return self.controller.entry.runtime_data.iot.available and self.controller.held is not None

    @property
    def is_on(self) -> bool:
        return bool(self.controller.held)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_hold(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_hold(False)
