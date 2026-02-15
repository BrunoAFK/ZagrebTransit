"""Sensor platform for Zagreb Transit."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ICON_STATUS, ICON_TRANSIT

_NEARBY_ATTR_MAX_STOPS = 4
_NEARBY_ATTR_MAX_DEPARTURES_PER_STOP = 4


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known_watch_ids: set[str] = set()

    @callback
    def _add_watch_sensors(_new_watch_id: str | None = None) -> None:
        new_entities: list[SensorEntity] = []
        ordered_watch_ids = coordinator.watch_ids()
        current_watch_ids = set(ordered_watch_ids)
        # Keep local cache aligned so re-used watch ids can be re-added.
        known_watch_ids.intersection_update(current_watch_ids)

        for watch_id in ordered_watch_ids:
            if watch_id in known_watch_ids:
                continue
            known_watch_ids.add(watch_id)
            new_entities.append(
                ZagrebTransitWatchSensor(
                    coordinator,
                    watch_id,
                    coordinator.watch_entity_key(watch_id),
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    async_add_entities(
        [
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_feed_version_active", "feed_version", "mdi:source-branch"),
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_feed_valid_from", "feed_valid_from", "mdi:calendar-start"),
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_feed_valid_to", "feed_valid_to", "mdi:calendar-end"),
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_feed_source", "feed_source", "mdi:database-arrow-down"),
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_realtime_status", "realtime_status", ICON_STATUS),
            ZagrebTransitBasicSensor(coordinator, "zagreb_transport_realtime_last_timestamp", "realtime_last_timestamp", "mdi:clock-check"),
            ZagrebTransitDebugSensor(coordinator),
            ZagrebTransitWatchRegistrySensor(coordinator),
            ZagrebTransitOdDoSensor(coordinator),
            ZagrebTransitStationBoardSensor(coordinator),
            ZagrebTransitNearbyBoardSensor(coordinator),
        ]
    )

    _add_watch_sensors(None)
    entry.async_on_unload(
        async_dispatcher_connect(hass, coordinator.watches_changed_signal, _add_watch_sensors)
    )


class ZagrebTransitBaseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, unique_id: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_icon = icon


class ZagrebTransitBasicSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator, unique_id: str, state_key: str, icon: str) -> None:
        super().__init__(coordinator, unique_id, unique_id, icon)
        self._state_key = state_key

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._state_key)


class ZagrebTransitWatchRegistrySensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zagreb_transport_watch_registry", "zagreb_transport_watch_registry", "mdi:playlist-check")

    @property
    def native_value(self):
        return len((self.coordinator.data or {}).get("watch_ids", []))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        watch_ids = data.get("watch_ids", [])
        rows = []
        for watch_id in watch_ids:
            out = data.get("watches", {}).get(watch_id, {})
            actual_entity_id = self._resolve_watch_entity_id(watch_id)
            rows.append(
                {
                    "watch_id": watch_id,
                    "watch_key": out.get("watch_key"),
                    "entity_id": actual_entity_id
                    or f"sensor.zagreb_transport_watch_{out.get('watch_key') or watch_id}",
                    "expected_entity_id": f"sensor.zagreb_transport_watch_{out.get('watch_key') or watch_id}",
                    "name": out.get("name"),
                    "type": out.get("type"),
                    "enabled": out.get("enabled"),
                    "state": out.get("state", 0),
                    "error": out.get("error"),
                }
            )
        return {
            "watch_ids": watch_ids,
            "watches": rows,
        }

    def _resolve_watch_entity_id(self, watch_id: str) -> str | None:
        if self.hass is None:
            return None
        for st in self.hass.states.async_all("sensor"):
            if st.attributes.get("watch_id") == watch_id:
                return st.entity_id
        return None


class ZagrebTransitWatchSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator, watch_id: str, watch_key: str) -> None:
        self._watch_id = watch_id
        super().__init__(
            coordinator,
            f"zagreb_transport_watch_{watch_key}",
            f"zagreb_transport_watch_{watch_key}",
            "mdi:routes-clock",
        )

    @property
    def native_value(self):
        watch = (self.coordinator.data or {}).get("watches", {}).get(self._watch_id, {})
        return watch.get("state", 0)

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return self._watch_id in data.get("watches", {})

    @property
    def extra_state_attributes(self):
        watch = (self.coordinator.data or {}).get("watches", {}).get(self._watch_id, {})
        attrs = {
            "watch_id": self._watch_id,
            "watch_key": watch.get("watch_key"),
            "name": watch.get("name"),
            "type": watch.get("type"),
            "enabled": watch.get("enabled", False),
            "error": watch.get("error"),
            "config": watch.get("config", {}),
            "departures": watch.get("departures", []),
        }
        if "grouped" in watch:
            attrs["grouped"] = watch.get("grouped", [])
        if "stations" in watch:
            attrs["stations"] = watch.get("stations", [])
        if "stops" in watch:
            attrs["stops"] = watch.get("stops", [])
        if "window_minutes" in watch:
            attrs["window_minutes"] = watch.get("window_minutes")
        if "radius_meters" in watch:
            attrs["radius_meters"] = watch.get("radius_meters")
        if "location_source" in watch:
            attrs["location_source"] = watch.get("location_source")
        return attrs


class ZagrebTransitOdDoSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zagreb_transport_next_trip_od_do", "zagreb_transport_next_trip_od_do", ICON_TRANSIT)

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("od_do", {}).get("state", "unavailable")

    @property
    def extra_state_attributes(self):
        od_do = (self.coordinator.data or {}).get("od_do", {})
        return {
            "line": od_do.get("line"),
            "route": od_do.get("route"),
            "direction": od_do.get("direction"),
            "from_stop": od_do.get("from_stop"),
            "to_stop": od_do.get("to_stop"),
            "departure_planned": od_do.get("departure_planned"),
            "departure_rt": od_do.get("departure_rt"),
            "arrival_planned": od_do.get("arrival_planned"),
            "arrival_rt": od_do.get("arrival_rt"),
            "delay_minutes": od_do.get("delay_minutes"),
            "window_minutes": od_do.get("window_minutes"),
            "next_minutes": od_do.get("next_minutes"),
            "upcoming": od_do.get("upcoming", []),
        }


class ZagrebTransitStationBoardSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zagreb_transport_station_direction_board", "zagreb_transport_station_direction_board", "mdi:bus-stop")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("station_board", {}).get("state", 0)

    @property
    def extra_state_attributes(self):
        board = (self.coordinator.data or {}).get("station_board", {})
        return {
            "stop": board.get("stop"),
            "route": board.get("route"),
            "direction": board.get("direction"),
            "window_minutes": board.get("window_minutes"),
            "departures": board.get("departures", []),
        }


class ZagrebTransitDebugSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zagreb_transport_debug_info", "zagreb_transport_debug_info", "mdi:bug")

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return data.get("status", "unknown")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return data.get("debug", {})


class ZagrebTransitNearbyBoardSensor(ZagrebTransitBaseSensor):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zagreb_transport_nearby_board", "zagreb_transport_nearby_board", "mdi:crosshairs-gps")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("nearby_board", {}).get("state", 0)

    @property
    def extra_state_attributes(self):
        board = (self.coordinator.data or {}).get("nearby_board", {})
        raw_stops = board.get("stops", []) or []
        compact_stops = []
        total_departures = 0

        for stop in raw_stops:
            departures = stop.get("departures", []) or []
            total_departures += len(departures)
            compact_departures = []
            for dep in departures[:_NEARBY_ATTR_MAX_DEPARTURES_PER_STOP]:
                compact_departures.append(
                    {
                        "line": dep.get("line"),
                        "direction": dep.get("direction"),
                        "minutes": dep.get("minutes"),
                        "mode": dep.get("mode"),
                        "rt": dep.get("rt"),
                        "planned": dep.get("planned"),
                        "delay_minutes": dep.get("delay_minutes"),
                    }
                )
            compact_stops.append(
                {
                    "stop": stop.get("stop"),
                    "distance_meters": stop.get("distance_meters"),
                    "map_url": stop.get("map_url"),
                    "departures": compact_departures,
                    "departures_total": len(departures),
                }
            )

        compact_stops = compact_stops[:_NEARBY_ATTR_MAX_STOPS]
        truncated = (
            len(raw_stops) > _NEARBY_ATTR_MAX_STOPS
            or any(
                len((stop.get("departures", []) or [])) > _NEARBY_ATTR_MAX_DEPARTURES_PER_STOP
                for stop in raw_stops
            )
        )

        return {
            "reference_person": board.get("reference_person"),
            "radius_meters": board.get("radius_meters"),
            "window_minutes": board.get("window_minutes"),
            "stops": compact_stops,
            "stops_total": len(raw_stops),
            "departures_total": total_departures,
            "attributes_truncated": truncated,
        }
