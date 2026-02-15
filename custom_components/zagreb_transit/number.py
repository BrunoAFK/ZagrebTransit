"""Number entities for Zagreb Transit."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_NEARBY_RADIUS_METERS,
    DOMAIN,
    MAX_NEARBY_RADIUS_METERS,
    MAX_WINDOW_MINUTES,
    MIN_NEARBY_RADIUS_METERS,
    MIN_WINDOW_MINUTES,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZagrebTransitWindowMinutesNumber(coordinator),
            ZagrebTransitNearbyRadiusNumber(coordinator),
        ]
    )


class ZagrebTransitWindowMinutesNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_unique_id = "zet_window_minutes"
    _attr_name = "zet_window_minutes"
    _attr_icon = "mdi:timer-cog"
    _attr_native_min_value = MIN_WINDOW_MINUTES
    _attr_native_max_value = MAX_WINDOW_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        value = data.get("selection", {}).get("window_minutes")
        if value is None:
            return MIN_WINDOW_MINUTES
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_selection("window_minutes", int(value))


class ZagrebTransitNearbyRadiusNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_unique_id = "zet_nearby_radius_meters"
    _attr_name = "zet_nearby_radius_meters"
    _attr_icon = "mdi:map-marker-radius"
    _attr_native_min_value = MIN_NEARBY_RADIUS_METERS
    _attr_native_max_value = MAX_NEARBY_RADIUS_METERS
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "m"

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        value = data.get("selection", {}).get("nearby_radius_meters")
        if value is None:
            return float(DEFAULT_NEARBY_RADIUS_METERS)
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_selection("nearby_radius_meters", int(value))
