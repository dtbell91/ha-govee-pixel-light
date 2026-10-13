"""Notify entity: `notify.send_message` shows the message on the display, then the playlist resumes."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry, content
from .const import ATTR_MESSAGE, DEFAULT_INTERRUPT_SECONDS
from .display import async_show
from .entity import LightStateEntity


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(DisplayNotify(entry, dev_id) for dev_id in entry.runtime_data.devices)


class DisplayNotify(LightStateEntity, NotifyEntity):
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id, "message")

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        text = f"{title}: {message}" if title else message
        options = vol.Schema(content.OPTIONS[content.KIND_TEXT])({ATTR_MESSAGE: text})
        prepared = await content.async_prepare(self.hass, content.KIND_TEXT, options)
        await self.controller.async_interrupt(
            lambda: async_show(self.hass, self.entry, self.dev, prepared), DEFAULT_INTERRUPT_SECONDS)
