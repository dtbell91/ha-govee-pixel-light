"""Image entity showing the last picture/animation sent to each light (upscaled preview)."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry
from .const import DOMAIN
from .services import SIGNAL_PREVIEW


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(LastImage(hass, entry, dev_id) for dev_id in entry.runtime_data.devices)


class LastImage(ImageEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_image"
    _attr_content_type = "image/gif"

    def __init__(self, hass: HomeAssistant, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(hass)
        self._data = entry.runtime_data
        self._dev_id = dev_id
        self._attr_unique_id = f"{dev_id}_last_image"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dev_id)})
        self._update_from_preview()

    def _update_from_preview(self) -> None:
        if (item := self._data.previews.get(self._dev_id)) is not None:
            self._attr_image_last_updated = datetime.fromtimestamp(item[1], timezone.utc)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_PREVIEW.format(self._dev_id), self._handle_preview))

    @callback
    def _handle_preview(self) -> None:
        self._update_from_preview()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        item = self._data.previews.get(self._dev_id)
        return item[0] if item else None
