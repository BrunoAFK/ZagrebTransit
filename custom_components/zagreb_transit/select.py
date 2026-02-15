"""Select entities for Zagreb Transit."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


SELECT_DEFS = [
    ("route_mode", "zet_route_mode", "mdi:train-car", "route_modes"),
    ("route", "zet_route", "mdi:routes", "routes"),
    ("od_direction", "zet_od_direction", "mdi:sign-direction", "od_directions"),
    ("from_stop", "zet_from_stop", "mdi:map-marker-arrow-right", "from_stops"),
    ("to_stop", "zet_to_stop", "mdi:map-marker-arrow-left", "to_stops"),
    ("station", "zet_station", "mdi:bus-stop", "stations"),
    ("direction", "zet_direction", "mdi:sign-direction", "directions"),
    ("board_route", "zet_board_route", "mdi:tram", "board_routes"),
    ("reference_person", "zet_reference_person", "mdi:account-location", "reference_persons"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ZagrebTransitSelectEntity(coordinator, select_key, unique_id, icon, option_key)
        for select_key, unique_id, icon, option_key in SELECT_DEFS
    ]
    async_add_entities(entities)


class ZagrebTransitSelectEntity(CoordinatorEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, select_key: str, unique_id: str, icon: str, option_key: str) -> None:
        super().__init__(coordinator)
        self._select_key = select_key
        self._option_key = option_key
        self._attr_unique_id = unique_id
        self._attr_name = unique_id
        self._attr_icon = icon

    @property
    def options(self) -> list[str]:
        data = self.coordinator.data or {}
        return list(data.get("options", {}).get(self._option_key, []))

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data or {}
        return data.get("selection", {}).get(self._select_key)

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Invalid option {option} for {self.entity_id}")
        await self.coordinator.async_set_selection(self._select_key, option)
