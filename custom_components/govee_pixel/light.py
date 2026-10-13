"""The light itself: power, brightness, and effects (the playlist or a built-in animation)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT, ColorMode, LightEntity, LightEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GoveePixelConfigEntry, animations, content
from .const import ATTR_ANIMATION, SIGNAL_PREVIEW
from .device_state import SHOWING_IMAGE, SHOWING_PLAYLIST
from .display import async_show
from .entity import LightStateEntity
from .iot import IotError

EFFECT_PLAYLIST = "Playlist"


async def async_setup_entry(hass: HomeAssistant, entry: GoveePixelConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PixelLight(entry, dev_id) for dev_id in entry.runtime_data.devices)


class PixelLight(LightStateEntity, LightEntity):
    _attr_name = None  # the device's main feature
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_PLAYLIST, *animations.SCENES]

    def __init__(self, entry: GoveePixelConfigEntry, dev_id: str) -> None:
        super().__init__(entry, dev_id, "light")
        self._animation: str | None = None  # last built-in animation started from this entity

    @property
    def is_on(self) -> bool | None:
        return self.state_.on

    @property
    def brightness(self) -> int | None:
        if self.state_.brightness is None:
            return None
        return round(self.state_.brightness * 255 / 100)

    @property
    def effect(self) -> str | None:
        showing = self.state_.showing
        if showing == SHOWING_PLAYLIST and self.controller.active:
            return EFFECT_PLAYLIST
        if showing == SHOWING_IMAGE and self._animation:
            return self._animation
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Anything else shown on the light (images, text, playlist pushes) replaces our animation.
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_PREVIEW.format(self.dev.device), self._on_new_content))

    @callback
    def _on_new_content(self) -> None:
        self._animation = None
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            if self.state_.on is not True:
                await self.iot.async_set_power(self.dev.topic, True)
                self.state_.async_set_optimistic(on=True)
            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                percent = max(1, round(brightness * 100 / 255))
                await self.iot.async_set_brightness(self.dev.topic, percent)
                self.state_.async_set_optimistic(brightness=percent)
        except IotError as err:
            raise HomeAssistantError(f"{self.dev.name}: {err}") from err
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            await self._async_set_effect(effect)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.iot.async_set_power(self.dev.topic, False)
        except IotError as err:
            raise HomeAssistantError(f"{self.dev.name}: {err}") from err
        self.state_.async_set_optimistic(on=False)

    async def _async_set_effect(self, effect: str) -> None:
        if effect == EFFECT_PLAYLIST:
            self._animation = None
            if self.controller.active:
                await self.controller.async_resume()
            else:
                await self.controller.async_set_active(True)
            return
        if effect not in animations.SCENES:
            raise HomeAssistantError(f"Unknown effect {effect!r}")
        options = vol.Schema(content.OPTIONS[content.KIND_ANIMATION])({ATTR_ANIMATION: effect})
        prepared = await content.async_prepare(self.hass, content.KIND_ANIMATION, options)
        # hold 0: an active playlist stays paused until the Playlist effect (or playlist_resume) brings it back
        await self.controller.async_interrupt(
            lambda: async_show(self.hass, self.entry, self.dev, prepared), 0)
        self._animation = effect
        self.async_write_ha_state()
