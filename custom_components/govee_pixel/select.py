"""Playlist item select (pixeld): shows the item on screen; picking one shows it now."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .entity import PlaylistEntity


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PlaylistItemSelect(c, "playlist_item") for c in entry.runtime_data.playlists.values()
                       if c.playback_controls)


class PlaylistItemSelect(PlaylistEntity, SelectEntity):
    _attr_icon = "mdi:playlist-check"

    @property
    def available(self) -> bool:
        return self.controller.entry.runtime_data.iot.available and self.controller.active

    @property
    def options(self) -> list[str]:
        return self.controller.rotation

    @property
    def current_option(self) -> str | None:
        current = self.controller.current_item
        return current if current in self.controller.rotation else None

    async def async_select_option(self, option: str) -> None:
        await self.controller.async_show_item(option)
