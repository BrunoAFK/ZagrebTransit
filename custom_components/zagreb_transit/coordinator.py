"""Coordinator for Zagreb Transit integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import gtfs_store as gtfs_store_module
from .const import (
    ATTR_FEED_SOURCE,
    ATTR_FEED_VALID_FROM,
    ATTR_FEED_VALID_TO,
    ATTR_FEED_VERSION,
    ATTR_REALTIME_LAST_TIMESTAMP,
    ATTR_REALTIME_STATUS,
    CONF_NOTIFICATIONS_ENABLED,
    CONF_DEFAULT_WINDOW_MINUTES,
    CONF_REALTIME_INTERVAL,
    CONF_STATIC_REFRESH_HOURS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_NEARBY_RADIUS_METERS,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_REALTIME_INTERVAL,
    DEFAULT_STATIC_REFRESH_HOURS,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WINDOW_MINUTES,
    DOMAIN,
    MAX_NEARBY_RADIUS_METERS,
    MAX_WATCHES,
    MAX_WATCH_LIMIT,
    MAX_WATCH_MAX_STOPS,
    MAX_WINDOW_MINUTES,
    MIN_NEARBY_RADIUS_METERS,
    MIN_WATCH_LIMIT,
    MIN_WATCH_MAX_STOPS,
    MIN_WINDOW_MINUTES,
    VERSION,
    WATCH_LOCATION_FIXED,
    WATCH_LOCATION_PERSON,
    WATCH_LOCATION_ZONE,
    WATCH_TYPE_DEPARTURE,
    WATCH_TYPE_NEARBY,
    WATCH_TYPE_OD,
    WATCH_TYPE_STATION_QUERY,
    WATCH_TYPES,
)
from .gtfs_index import GtfsIndex
from .gtfs_store import FeedMeta, GtfsStore
from .realtime import RealtimeClient

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
WATCH_STORE_VERSION = 1
SIGNAL_WATCHES_CHANGED_BASE = f"{DOMAIN}_watches_changed"
NOTIFICATION_ID_DEGRADED = f"{DOMAIN}_degraded"


class ZagrebTransitCoordinator(DataUpdateCoordinator[dict]):
    """Main coordinator for Zagreb Transit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.session = async_get_clientsession(hass)
        self.gtfs_store = GtfsStore(hass, self.session)
        self.realtime = RealtimeClient(self.session)
        self.index: GtfsIndex | None = None
        self.active_feed: FeedMeta | None = None
        self.feed_source = "none"
        self.integration_status = "degraded"
        self.error_message: str | None = None

        self._last_static_refresh: datetime | None = None
        self._last_realtime_refresh: datetime | None = None
        self._last_realtime_recovery_at: str | None = None
        self._realtime_backoff_multiplier = 1

        self._state_store = Store(hass, STORE_VERSION, f"{DOMAIN}.state.{entry.entry_id}")
        self._watch_store = Store(hass, WATCH_STORE_VERSION, f"{DOMAIN}.watches.{entry.entry_id}")
        self._watch_registry: dict[str, dict] = {}

        self.selection_state = {
            "route_mode": "tram",
            "route": None,
            "od_direction": "All",
            "from_stop": None,
            "to_stop": None,
            "station": None,
            "direction": "All",
            "board_route": "All",
            "window_minutes": int(entry.options.get(CONF_DEFAULT_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES)),
            "reference_person": None,
            "nearby_radius_meters": DEFAULT_NEARBY_RADIUS_METERS,
        }

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=int(entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))),
        )

    async def async_initialize(self) -> None:
        """Load state and bootstrap data."""
        cached = await self._state_store.async_load()
        if isinstance(cached, dict):
            self.selection_state.update(cached)

        await self._async_load_watch_registry()
        await self.async_refresh_static(force=True)
        await self.async_refresh_realtime(force=True)
        self.data = self._build_state()

    @property
    def watches_changed_signal(self) -> str:
        return f"{SIGNAL_WATCHES_CHANGED_BASE}_{self.entry.entry_id}"

    def watch_ids(self) -> list[str]:
        return sorted(
            self._watch_registry.keys(),
            key=lambda watch_id: (
                str(self._watch_registry[watch_id].get("created_at", "")),
                watch_id,
            ),
        )

    def watch_entity_key(self, watch_id: str) -> str:
        watch = self._watch_registry.get(watch_id, {})
        return str(watch.get("watch_key") or watch_id)

    def watch_summaries(self) -> list[dict]:
        return [
            {
                "watch_id": watch_id,
                "watch_key": self.watch_entity_key(watch_id),
                "name": self._watch_registry[watch_id].get("name"),
                "type": self._watch_registry[watch_id].get("type"),
                "enabled": self._watch_registry[watch_id].get("enabled", True),
            }
            for watch_id in self.watch_ids()
        ]

    def watch_by_id(self, watch_id: str) -> dict | None:
        watch = self._watch_registry.get(watch_id)
        if not watch:
            return None
        return {
            "watch_id": watch.get("watch_id"),
            "watch_key": watch.get("watch_key"),
            "name": watch.get("name"),
            "type": watch.get("type"),
            "enabled": watch.get("enabled", True),
            "config": dict(watch.get("config", {})),
        }

    async def _async_load_watch_registry(self) -> None:
        stored = await self._watch_store.async_load()
        rows = stored.get("watches", []) if isinstance(stored, dict) else []
        for row in rows:
            watch = self._normalize_watch_dict(row)
            if watch:
                self._watch_registry[watch["watch_id"]] = watch

    async def _async_save_watch_registry(self) -> None:
        payload = {
            "watches": [self._watch_registry[wid] for wid in self.watch_ids()],
            "updated_at": dt_util.utcnow().isoformat(),
        }
        await self._watch_store.async_save(payload)

    async def async_add_watch(self, name: str, watch_type: str, enabled: bool = True, config: dict | None = None) -> dict:
        """Add a new watch and trigger sensor update."""
        if watch_type not in WATCH_TYPES:
            raise ValueError(f"Unsupported watch_type: {watch_type}")
        if len(self._watch_registry) >= MAX_WATCHES:
            raise ValueError(f"Maximum watches reached ({MAX_WATCHES})")

        watch_id = self._next_watch_id()
        watch_key = self._next_watch_key(name)
        now_iso = dt_util.utcnow().isoformat()
        watch = {
            "watch_id": watch_id,
            "watch_key": watch_key,
            "name": (name or watch_id).strip() or watch_id,
            "type": watch_type,
            "enabled": bool(enabled),
            "config": self._normalize_watch_config(watch_type, config or {}),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        self._watch_registry[watch_id] = watch
        await self._async_save_watch_registry()
        await self._async_refresh_outputs_and_notify(new_watch_id=watch_id)
        return watch

    async def async_update_watch(
        self,
        watch_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        config: dict | None = None,
    ) -> dict:
        """Update an existing watch."""
        watch = self._watch_registry.get(watch_id)
        if not watch:
            raise ValueError(f"watch_id not found: {watch_id}")

        if name is not None:
            old_name = watch.get("name", "")
            old_watch_key = str(watch.get("watch_key") or watch_id)
            watch["name"] = name.strip() or watch["watch_id"]
            if slugify(old_name) != slugify(watch["name"]):
                watch["watch_key"] = self._next_watch_key(watch["name"], exclude_watch_id=watch_id)
                if watch["watch_key"] != old_watch_key:
                    await self._async_remove_watch_entity(old_watch_key)
        if enabled is not None:
            watch["enabled"] = bool(enabled)
        if config is not None:
            merged = dict(watch.get("config", {}))
            merged.update(config)
            watch["config"] = self._normalize_watch_config(watch["type"], merged)

        watch["updated_at"] = dt_util.utcnow().isoformat()
        await self._async_save_watch_registry()
        await self._async_refresh_outputs_and_notify()
        return watch

    async def async_remove_watch(self, watch_id: str) -> None:
        """Remove watch from registry."""
        if watch_id not in self._watch_registry:
            raise ValueError(f"watch_id not found: {watch_id}")
        removed = self._watch_registry.pop(watch_id)
        removed_watch_key = str(removed.get("watch_key") or watch_id)
        await self._async_remove_watch_entity(removed_watch_key)
        await self._async_save_watch_registry()
        await self._async_refresh_outputs_and_notify()

    async def async_duplicate_watch(self, watch_id: str, name_suffix: str = " Copy") -> dict:
        """Duplicate an existing watch."""
        source = self._watch_registry.get(watch_id)
        if not source:
            raise ValueError(f"watch_id not found: {watch_id}")

        return await self.async_add_watch(
            name=f"{source['name']}{name_suffix}",
            watch_type=source["type"],
            enabled=source.get("enabled", True),
            config=dict(source.get("config", {})),
        )

    async def _async_refresh_outputs_and_notify(self, new_watch_id: str | None = None) -> None:
        self.data = self._build_state()
        self.async_update_listeners()
        async_dispatcher_send(self.hass, self.watches_changed_signal, new_watch_id)

    async def _async_remove_watch_entity(self, watch_key: str) -> None:
        """Remove dynamic watch sensor from entity registry if it exists."""
        entity_id = f"sensor.zagreb_transport_watch_{watch_key}"
        registry = er.async_get(self.hass)
        if registry.async_get(entity_id):
            registry.async_remove(entity_id)

    async def async_refresh_static(self, force: bool = False) -> None:
        now = dt_util.now()
        interval_hours = int(self.entry.options.get(CONF_STATIC_REFRESH_HOURS, DEFAULT_STATIC_REFRESH_HOURS))

        if not force and self._last_static_refresh and now - self._last_static_refresh < timedelta(hours=interval_hours):
            return

        latest_meta = None
        try:
            latest_meta = await self.gtfs_store.refresh_latest()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Static feed refresh failed, trying cached feed fallback: %s", err)

        selected, source, status = await self.gtfs_store.get_active_feed(now.date(), latest_meta)
        self.feed_source = source
        self.integration_status = status

        if not selected:
            self.active_feed = None
            self.index = None
            self.error_message = "No valid GTFS feed available"
            return

        self.active_feed = selected
        payload = await self.gtfs_store.load_feed_bytes(selected)
        self.index = GtfsIndex(payload)
        self.error_message = None
        self._last_static_refresh = now
        self._sync_integration_status(now.date())
        self._apply_default_selection()

    async def async_refresh_realtime(self, force: bool = False) -> None:
        now = dt_util.now()
        interval_sec = int(self.entry.options.get(CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL))
        effective_interval = interval_sec * self._realtime_backoff_multiplier
        if not force and self._last_realtime_refresh and now - self._last_realtime_refresh < timedelta(seconds=effective_interval):
            return

        result = await self.realtime.refresh()
        if result.get("status") == "ok":
            self._realtime_backoff_multiplier = 1
            if self.active_feed and self.active_feed.is_valid_for(now.date()):
                self.integration_status = "ok"
                self._last_realtime_recovery_at = now.isoformat()
        else:
            self._realtime_backoff_multiplier = min(self._realtime_backoff_multiplier * 2, 8)
            self._sync_integration_status(now.date())

        self._last_realtime_refresh = now

    async def async_rebuild_indexes(self) -> None:
        """Rebuild in-memory index from active feed."""
        if not self.active_feed:
            await self.async_refresh_static(force=True)
            return
        payload = await self.gtfs_store.load_feed_bytes(self.active_feed)
        self.index = GtfsIndex(payload)
        self._apply_default_selection()

    async def async_validate_active_feed(self) -> None:
        """Validate currently active feed against current date."""
        self._sync_integration_status(dt_util.now().date())

    async def async_force_select_feed(self, version: str) -> bool:
        """Force selected local feed version."""
        meta = await self.gtfs_store.force_select(version)
        if not meta:
            return False

        self.active_feed = meta
        self.feed_source = "forced"
        payload = await self.gtfs_store.load_feed_bytes(meta)
        self.index = GtfsIndex(payload)
        self._apply_default_selection()
        return True

    async def async_set_selection(self, key: str, value) -> None:
        """Set selector state and refresh calculated outputs."""
        self.selection_state[key] = value

        if key == "route_mode":
            self.selection_state["route"] = None
            self.selection_state["od_direction"] = "All"
            self.selection_state["from_stop"] = None
            self.selection_state["to_stop"] = None
        elif key == "route":
            self.selection_state["od_direction"] = "All"
            self.selection_state["from_stop"] = None
            self.selection_state["to_stop"] = None
        elif key == "od_direction":
            self.selection_state["from_stop"] = None
            self.selection_state["to_stop"] = None
        elif key == "from_stop":
            self.selection_state["to_stop"] = None
        elif key == "station":
            self.selection_state["direction"] = "All"
            self.selection_state["board_route"] = "All"

        await self._state_store.async_save(self.selection_state)
        self.data = self._build_state()
        self.async_update_listeners()

    async def _async_update_data(self) -> dict:
        """Periodic coordinator update."""
        try:
            await self.async_refresh_static(force=False)
            await self.async_refresh_realtime(force=False)
            return self._build_state()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    def _apply_default_selection(self) -> None:
        if not self.index:
            return

        route_mode = self.selection_state.get("route_mode") or "tram"
        if route_mode not in ("tram", "bus", "All"):
            route_mode = "tram"
            self.selection_state["route_mode"] = route_mode

        routes = self.index.route_options(route_mode)
        all_routes = self.index.route_options()
        stations = self.index.station_options()

        route = self.selection_state.get("route")
        if route not in routes:
            route = routes[0] if routes else None
            self.selection_state["route"] = route

        od_directions = ["All"]
        if route:
            od_directions.extend(self.index.get_directions_for_route(route))

        od_direction = self.selection_state.get("od_direction")
        if od_direction not in od_directions:
            od_direction = "All"
            self.selection_state["od_direction"] = od_direction

        from_candidates = self.index.get_stops_for_route(route, od_direction) if route else []
        from_options = [stop for stop in from_candidates if self.index.get_to_stops(route, stop, od_direction)]
        from_stop = self.selection_state.get("from_stop")
        if from_stop not in from_options:
            from_stop = from_options[0] if from_options else None
            self.selection_state["from_stop"] = from_stop

        to_options = self.index.get_to_stops(route, from_stop, od_direction) if route and from_stop else []
        to_stop = self.selection_state.get("to_stop")
        if to_stop not in to_options:
            to_stop = to_options[0] if to_options else None
            self.selection_state["to_stop"] = to_stop

        station = self.selection_state.get("station")
        if station not in stations:
            station = stations[0] if stations else None
            self.selection_state["station"] = station

        directions = self.index.get_directions_for_station(station) if station else []
        direction = self.selection_state.get("direction")
        if direction != "All" and direction not in directions:
            self.selection_state["direction"] = "All"

        board_route = self.selection_state.get("board_route")
        if board_route != "All" and board_route not in all_routes:
            self.selection_state["board_route"] = "All"

    def _build_state(self) -> dict:
        now_local = dt_util.now().replace(tzinfo=None)

        state: dict = {
            "status": self.integration_status,
            "error": self.error_message,
            "integration_version": VERSION,
            ATTR_FEED_VERSION: self.active_feed.version if self.active_feed else "none",
            ATTR_FEED_VALID_FROM: self.active_feed.start_date.isoformat() if self.active_feed and self.active_feed.start_date else None,
            ATTR_FEED_VALID_TO: self.active_feed.end_date.isoformat() if self.active_feed and self.active_feed.end_date else None,
            ATTR_FEED_SOURCE: self.feed_source,
            ATTR_REALTIME_STATUS: self.realtime.last_result.get("status", "stale"),
            ATTR_REALTIME_LAST_TIMESTAMP: self.realtime.last_result.get("last_timestamp"),
            "options": {
                "route_modes": ["tram", "bus", "All"],
                "routes": [],
                "od_directions": ["All"],
                "from_stops": [],
                "to_stops": [],
                "stations": [],
                "directions": ["All"],
                "board_routes": ["All"],
                "reference_persons": [],
            },
            "selection": dict(self.selection_state),
            "od_do": {
                "state": "unavailable",
                "upcoming": [],
            },
            "station_board": {
                "state": 0,
                "departures": [],
            },
            "nearby_board": {
                "state": 0,
                "stops": [],
            },
            "watches": {},
            "watch_ids": self.watch_ids(),
            "debug": {
                **self.gtfs_store.debug,
                "module_file_gtfs_store": getattr(gtfs_store_module, "__file__", None),
                "feed_source": self.feed_source,
                "integration_status": self.integration_status,
                "route_options_count": 0,
                "station_options_count": 0,
                "realtime_backoff_multiplier": self._realtime_backoff_multiplier,
                "last_realtime_recovery_at": self._last_realtime_recovery_at,
                "watch_registry_count": len(self._watch_registry),
            },
        }

        if not self.index:
            return state

        selection = dict(self.selection_state)

        route_mode = selection.get("route_mode") or "tram"
        routes = self.index.route_options(route_mode)
        all_routes = self.index.route_options()
        od_directions = ["All"]
        if selection.get("route"):
            od_directions.extend(self.index.get_directions_for_route(selection["route"]))
        from_candidates = self.index.get_stops_for_route(selection.get("route"), selection.get("od_direction")) if selection.get("route") else []
        from_stops = [
            stop
            for stop in from_candidates
            if self.index.get_to_stops(selection.get("route"), stop, selection.get("od_direction"))
        ]
        to_stops = (
            self.index.get_to_stops(selection.get("route"), selection.get("from_stop"), selection.get("od_direction"))
            if selection.get("route") and selection.get("from_stop")
            else []
        )
        stations = self.index.station_options()
        reference_persons = sorted([st.entity_id for st in self.hass.states.async_all("person")])
        directions = ["All"]
        if selection.get("station"):
            directions.extend(self.index.get_directions_for_station(selection["station"]))
        board_routes = ["All", *all_routes]

        selected_person = selection.get("reference_person")
        if selected_person not in reference_persons:
            selected_person = reference_persons[0] if reference_persons else None
            self.selection_state["reference_person"] = selected_person
        selection["reference_person"] = selected_person

        state["options"] = {
            "route_modes": ["tram", "bus", "All"],
            "routes": routes,
            "od_directions": od_directions,
            "from_stops": from_stops,
            "to_stops": to_stops,
            "stations": stations,
            "directions": directions,
            "board_routes": board_routes,
            "reference_persons": reference_persons,
        }
        state["debug"]["route_options_count"] = len(routes)
        state["debug"]["station_options_count"] = len(stations)
        state["selection"] = dict(self.selection_state)

        delays = self.realtime.last_result.get("trip_delays", {})
        window_minutes = int(
            selection.get("window_minutes")
            or self.entry.options.get(CONF_DEFAULT_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES)
        )

        state["debug"]["ha_now"] = dt_util.now().isoformat()
        state["debug"]["now_local"] = now_local.isoformat()
        state["debug"]["window_minutes"] = window_minutes

        od_do_upcoming = self.index.upcoming_od_do(
            now_local=now_local,
            route_label=selection.get("route") or "",
            direction_label=selection.get("od_direction") or "All",
            from_stop_label=selection.get("from_stop") or "",
            to_stop_label=selection.get("to_stop") or "",
            delay_by_trip=delays,
            limit=8,
        )
        od_do_windowed: list[dict] = []
        for dep in od_do_upcoming:
            minutes = _minutes_until(now_local, dep.get("departure_rt"))
            if minutes is None or minutes > window_minutes:
                continue
            line = _extract_line_code(dep.get("route", ""))
            od_do_windowed.append({**dep, "line": line, "minutes": minutes})

        state["debug"]["od_candidates_total"] = len(od_do_upcoming)
        state["debug"]["od_candidates_windowed"] = len(od_do_windowed)

        if od_do_windowed:
            first = od_do_windowed[0]
            dep_rt = first.get("departure_rt", "")
            hhmm = dep_rt[11:16] if len(dep_rt) >= 16 else dep_rt
            state["od_do"] = {
                "state": hhmm,
                **first,
                "window_minutes": window_minutes,
                "upcoming": od_do_windowed,
            }
        elif od_do_upcoming:
            first = od_do_upcoming[0]
            line = _extract_line_code(first.get("route", ""))
            next_minutes = _minutes_until(now_local, first.get("departure_rt"))
            state["od_do"] = {
                "state": "outside_window",
                "route": first.get("route"),
                "line": line,
                "direction": first.get("direction"),
                "from_stop": first.get("from_stop"),
                "to_stop": first.get("to_stop"),
                "departure_planned": first.get("departure_planned"),
                "departure_rt": first.get("departure_rt"),
                "arrival_planned": first.get("arrival_planned"),
                "arrival_rt": first.get("arrival_rt"),
                "delay_minutes": first.get("delay_minutes"),
                "window_minutes": window_minutes,
                "next_minutes": next_minutes,
                "upcoming": [],
            }
        board_raw = self.index.station_direction_board(
            now_local=now_local,
            station_label=selection.get("station") or "",
            direction_label=selection.get("direction") or "All",
            board_route_label=selection.get("board_route") or "All",
            window_minutes=window_minutes,
            delay_by_trip=delays,
        )
        board: list[dict] = []
        for dep in board_raw:
            line = _extract_line_code(dep.get("route", ""))
            minutes = _minutes_until(now_local, dep.get("rt"))
            if minutes is None:
                continue
            board.append({**dep, "line": line, "minutes": minutes})

        state["station_board"] = {
            "state": len(board),
            "stop": selection.get("station"),
            "direction": selection.get("direction"),
            "route": selection.get("board_route"),
            "window_minutes": window_minutes,
            "departures": board,
        }

        radius_m = int(selection.get("nearby_radius_meters") or DEFAULT_NEARBY_RADIUS_METERS)
        person_state = self.hass.states.get(selected_person) if selected_person else None
        lat = person_state.attributes.get("latitude") if person_state else None
        lon = person_state.attributes.get("longitude") if person_state else None
        if lat is not None and lon is not None:
            nearby_raw = self.index.nearby_board(
                now_local=now_local,
                user_lat=float(lat),
                user_lon=float(lon),
                radius_meters=radius_m,
                window_minutes=window_minutes,
                delay_by_trip=delays,
                max_stops=8,
            )
            nearby: list[dict] = []
            for stop_row in nearby_raw:
                deps: list[dict] = []
                for dep in stop_row.get("departures", []):
                    line = _extract_line_code(dep.get("route", ""))
                    minutes = _minutes_until(now_local, dep.get("rt"))
                    if minutes is None:
                        continue
                    deps.append({**dep, "line": line, "minutes": minutes})
                if not deps:
                    continue
                deps.sort(key=lambda item: item.get("minutes", 9999))
                nearby.append({**stop_row, "departures": deps})

            state["nearby_board"] = {
                "state": len(nearby),
                "reference_person": selected_person,
                "radius_meters": radius_m,
                "window_minutes": window_minutes,
                "stops": nearby,
            }

        watch_outputs: dict[str, dict] = {}
        for watch_id in self.watch_ids():
            watch = self._watch_registry[watch_id]
            out = self._evaluate_watch(watch, now_local, delays, window_minutes)
            watch_outputs[watch_id] = out
        state["watches"] = watch_outputs

        return state

    def _evaluate_watch(
        self,
        watch: dict,
        now_local: datetime,
        delays: dict[str, int],
        fallback_window_minutes: int,
    ) -> dict:
        watch_type = watch.get("type")
        cfg = watch.get("config", {})
        out = {
            "watch_id": watch.get("watch_id"),
            "watch_key": watch.get("watch_key"),
            "name": watch.get("name"),
            "type": watch_type,
            "enabled": bool(watch.get("enabled", True)),
            "config": cfg,
            "state": 0,
            "departures": [],
            "error": None,
        }

        if not out["enabled"]:
            return out

        try:
            if watch_type == WATCH_TYPE_OD:
                return self._eval_od_watch(out, cfg, now_local, delays, fallback_window_minutes)
            if watch_type == WATCH_TYPE_DEPARTURE:
                return self._eval_departure_watch(out, cfg, now_local, delays, fallback_window_minutes)
            if watch_type == WATCH_TYPE_NEARBY:
                return self._eval_nearby_watch(out, cfg, now_local, delays, fallback_window_minutes)
            if watch_type == WATCH_TYPE_STATION_QUERY:
                return self._eval_station_query_watch(out, cfg, now_local, delays, fallback_window_minutes)
            out["error"] = f"unsupported watch type: {watch_type}"
            return out
        except Exception as err:  # noqa: BLE001
            out["error"] = str(err)
            return out

    def _eval_od_watch(self, out: dict, cfg: dict, now_local: datetime, delays: dict[str, int], fallback_window: int) -> dict:
        from_query = str(cfg.get("from_query", "")).strip()
        to_query = str(cfg.get("to_query", "")).strip()
        if not from_query or not to_query:
            out["error"] = "from_query and to_query are required"
            return out

        window = _clamp_int(cfg.get("window_minutes"), fallback_window, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES)
        limit = _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT)
        mode_filter = _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All")))
        route_filter = str(cfg.get("route_filter", "")).strip()
        direction_filter = str(cfg.get("direction", "All")).strip()

        deps = self.index.upcoming_between_stop_names(
            now_local=now_local,
            from_query=from_query,
            to_query=to_query,
            window_minutes=window,
            delay_by_trip=delays,
            mode_filter=None if mode_filter == "All" else mode_filter,
            limit=limit,
        )

        filtered: list[dict] = []
        for dep in deps:
            if direction_filter and direction_filter != "All" and dep.get("direction") != direction_filter:
                continue
            line = _extract_line_code(dep.get("route", ""))
            if route_filter and not _route_filter_match(route_filter, dep.get("route", ""), line):
                continue
            minutes = _minutes_until(now_local, dep.get("departure_rt"))
            if minutes is None:
                continue
            filtered.append({**dep, "line": line, "minutes": minutes})

        out["window_minutes"] = window
        out["departures"] = filtered
        out["state"] = len(filtered)
        return out

    def _eval_departure_watch(self, out: dict, cfg: dict, now_local: datetime, delays: dict[str, int], fallback_window: int) -> dict:
        from_query = str(cfg.get("from_query", "")).strip()
        if not from_query:
            out["error"] = "from_query is required"
            return out

        window = _clamp_int(cfg.get("window_minutes"), fallback_window, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES)
        limit = _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT)
        max_stops = _clamp_int(cfg.get("max_stops"), 12, MIN_WATCH_MAX_STOPS, MAX_WATCH_MAX_STOPS)
        mode_filter = _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All")))
        route_filter = str(cfg.get("route_filter", "")).strip()
        direction_filter = str(cfg.get("direction", "All")).strip()

        boards = self.index.boards_for_station_queries(
            now_local=now_local,
            station_queries=[from_query],
            window_minutes=window,
            delay_by_trip=delays,
            max_stops=max_stops,
        )

        departures: list[dict] = []
        for station in boards:
            stop = station.get("stop")
            for dep in station.get("departures", []):
                if mode_filter != "All" and dep.get("mode") != mode_filter:
                    continue
                if direction_filter and direction_filter != "All" and dep.get("direction") != direction_filter:
                    continue
                line = _extract_line_code(dep.get("route", ""))
                if route_filter and not _route_filter_match(route_filter, dep.get("route", ""), line):
                    continue
                minutes = _minutes_until(now_local, dep.get("rt"))
                if minutes is None:
                    continue
                departures.append({**dep, "line": line, "minutes": minutes, "stop": stop})

        departures.sort(key=lambda item: item.get("minutes", 9999))
        out["window_minutes"] = window
        out["departures"] = departures[:limit]
        out["state"] = len(out["departures"])
        return out

    def _eval_nearby_watch(self, out: dict, cfg: dict, now_local: datetime, delays: dict[str, int], fallback_window: int) -> dict:
        source_type = str(cfg.get("location_source_type", WATCH_LOCATION_PERSON)).strip()
        window = _clamp_int(cfg.get("window_minutes"), fallback_window, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES)
        radius = _clamp_int(cfg.get("radius_meters"), DEFAULT_NEARBY_RADIUS_METERS, MIN_NEARBY_RADIUS_METERS, MAX_NEARBY_RADIUS_METERS)
        max_stops = _clamp_int(cfg.get("max_stops"), 8, 1, 20)
        limit_per_stop = _clamp_int(cfg.get("limit_per_stop"), 6, 1, 30)
        mode_filter = _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All")))

        lat, lon, source_label = self._resolve_location(source_type, cfg)
        if lat is None or lon is None:
            out["error"] = f"location unavailable ({source_type})"
            return out

        nearby = self.index.nearby_board(
            now_local=now_local,
            user_lat=lat,
            user_lon=lon,
            radius_meters=radius,
            window_minutes=window,
            delay_by_trip=delays,
            max_stops=max_stops,
        )

        rows: list[dict] = []
        total = 0
        for stop_row in nearby:
            filtered_departures: list[dict] = []
            for dep in stop_row.get("departures", []):
                if mode_filter != "All" and dep.get("mode") != mode_filter:
                    continue
                line = _extract_line_code(dep.get("route", ""))
                minutes = _minutes_until(now_local, dep.get("rt"))
                if minutes is None:
                    continue
                filtered_departures.append({**dep, "line": line, "minutes": minutes})

            if not filtered_departures:
                continue

            filtered_departures.sort(key=lambda item: item.get("minutes", 9999))
            filtered_departures = filtered_departures[:limit_per_stop]
            total += len(filtered_departures)
            rows.append(
                {
                    "stop": stop_row.get("stop"),
                    "distance_meters": stop_row.get("distance_meters"),
                    "map_url": stop_row.get("map_url"),
                    "departures": filtered_departures,
                }
            )

        out["state"] = total
        out["window_minutes"] = window
        out["radius_meters"] = radius
        out["location_source_type"] = source_type
        out["location_source"] = source_label
        out["stops"] = rows
        return out

    def _eval_station_query_watch(self, out: dict, cfg: dict, now_local: datetime, delays: dict[str, int], fallback_window: int) -> dict:
        queries = cfg.get("station_queries")
        if isinstance(queries, str):
            station_queries = [q.strip() for q in queries.split(",") if q.strip()]
        elif isinstance(queries, list):
            station_queries = [str(q).strip() for q in queries if str(q).strip()]
        else:
            station_queries = []

        if not station_queries:
            out["error"] = "station_queries required"
            return out

        window = _clamp_int(cfg.get("window_minutes"), fallback_window, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES)
        max_stops = _clamp_int(cfg.get("max_stops"), 12, MIN_WATCH_MAX_STOPS, MAX_WATCH_MAX_STOPS)
        limit = _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT)
        mode_filter = _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All")))
        route_filter = str(cfg.get("route_filter", "")).strip()
        direction_filter = str(cfg.get("direction", "All")).strip()

        boards = self.index.boards_for_station_queries(
            now_local=now_local,
            station_queries=station_queries,
            window_minutes=window,
            delay_by_trip=delays,
            max_stops=max_stops,
        )

        stations: list[dict] = []
        grouped: dict[tuple[str, str], dict] = {}
        total = 0

        for station in boards:
            stop = station.get("stop")
            deps_for_stop: list[dict] = []
            for dep in station.get("departures", []):
                if mode_filter != "All" and dep.get("mode") != mode_filter:
                    continue
                if direction_filter and direction_filter != "All" and dep.get("direction") != direction_filter:
                    continue
                line = _extract_line_code(dep.get("route", ""))
                if route_filter and not _route_filter_match(route_filter, dep.get("route", ""), line):
                    continue
                minutes = _minutes_until(now_local, dep.get("rt"))
                if minutes is None:
                    continue

                row = {**dep, "line": line, "minutes": minutes}
                deps_for_stop.append(row)
                total += 1

                key = (line, dep.get("direction") or "Unknown")
                if key not in grouped:
                    grouped[key] = {
                        "line": line,
                        "direction": dep.get("direction") or "Unknown",
                        "minutes": [],
                        "stops": set(),
                    }
                grouped[key]["minutes"].append(minutes)
                grouped[key]["stops"].add(stop)

            if deps_for_stop:
                deps_for_stop.sort(key=lambda item: item.get("minutes", 9999))
                stations.append({"stop": stop, "departures": deps_for_stop[:limit]})

        grouped_rows = [
            {
                "line": item["line"],
                "direction": item["direction"],
                "minutes": sorted(item["minutes"]),
                "stops": sorted(item["stops"]),
            }
            for item in grouped.values()
        ]
        grouped_rows.sort(key=lambda item: (int(item["line"]) if item["line"].isdigit() else 9999, item["direction"]))

        out["state"] = total
        out["window_minutes"] = window
        out["station_queries"] = station_queries
        out["stations"] = stations
        out["grouped"] = grouped_rows
        return out

    def _resolve_location(self, source_type: str, cfg: dict) -> tuple[float | None, float | None, str | None]:
        if source_type == WATCH_LOCATION_PERSON:
            person_entity = str(cfg.get("person_entity") or self.selection_state.get("reference_person") or "").strip()
            if not person_entity:
                return None, None, None
            st = self.hass.states.get(person_entity)
            if not st:
                return None, None, person_entity
            lat = st.attributes.get("latitude")
            lon = st.attributes.get("longitude")
            return _to_float(lat), _to_float(lon), person_entity

        if source_type == WATCH_LOCATION_ZONE:
            zone_entity = str(cfg.get("zone_entity", "")).strip()
            if not zone_entity:
                return None, None, None
            st = self.hass.states.get(zone_entity)
            if not st:
                return None, None, zone_entity
            lat = st.attributes.get("latitude")
            lon = st.attributes.get("longitude")
            return _to_float(lat), _to_float(lon), zone_entity

        if source_type == WATCH_LOCATION_FIXED:
            lat = _to_float(cfg.get("fixed_lat"))
            lon = _to_float(cfg.get("fixed_lon"))
            return lat, lon, "fixed"

        return None, None, source_type

    def _sync_integration_status(self, today) -> None:
        previous_status = self.integration_status
        if not self.active_feed:
            self.integration_status = "degraded"
            self.error_message = "No active feed"
        elif not self.active_feed.is_valid_for(today):
            self.integration_status = "degraded"
            self.error_message = "Active feed is outside valid date range"
        else:
            self.integration_status = "ok"
            if self.error_message in {"No active feed", "Active feed is outside valid date range"}:
                self.error_message = None

        self._handle_status_notification(previous_status, today)

    def _handle_status_notification(self, previous_status: str, today) -> None:
        notifications_enabled = bool(
            self.entry.options.get(CONF_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED)
        )
        if not notifications_enabled:
            persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID_DEGRADED)
            return

        if self.integration_status == "degraded":
            if previous_status == "degraded":
                return
            feed_version = self.active_feed.version if self.active_feed else "none"
            feed_range = self.active_feed.valid_range if self.active_feed else "unknown"
            message = (
                f"Zagreb Transit is degraded.\n"
                f"Date: {today.isoformat()}\n"
                f"Feed version: {feed_version}\n"
                f"Feed valid range: {feed_range}\n"
                f"Source: {self.feed_source}\n"
                f"Reason: {self.error_message or 'unknown'}"
            )
            persistent_notification.async_create(
                self.hass,
                message,
                title="Zagreb Transit warning",
                notification_id=NOTIFICATION_ID_DEGRADED,
            )
            return

        if self.integration_status == "ok":
            persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID_DEGRADED)

    def _normalize_watch_dict(self, row: dict) -> dict | None:
        watch_id = str(row.get("watch_id", "")).strip()
        watch_type = str(row.get("type", "")).strip()
        if not watch_id or watch_type not in WATCH_TYPES:
            return None

        return {
            "watch_id": watch_id,
            "watch_key": self._normalize_watch_key(
                str(row.get("watch_key") or ""),
                str(row.get("name", watch_id)),
            ),
            "name": str(row.get("name", watch_id)).strip() or watch_id,
            "type": watch_type,
            "enabled": bool(row.get("enabled", True)),
            "config": self._normalize_watch_config(watch_type, dict(row.get("config", {}))),
            "created_at": str(row.get("created_at") or dt_util.utcnow().isoformat()),
            "updated_at": str(row.get("updated_at") or dt_util.utcnow().isoformat()),
        }

    def _normalize_watch_config(self, watch_type: str, config: dict) -> dict:
        cfg = dict(config)

        if watch_type == WATCH_TYPE_OD:
            return {
                "vehicle_type": _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All"))),
                "route_filter": str(cfg.get("route_filter", "")).strip(),
                "direction": str(cfg.get("direction", "All")).strip() or "All",
                "from_query": str(cfg.get("from_query", "")).strip(),
                "to_query": str(cfg.get("to_query", "")).strip(),
                "window_minutes": _clamp_int(cfg.get("window_minutes"), DEFAULT_WINDOW_MINUTES, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES),
                "limit": _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT),
            }

        if watch_type == WATCH_TYPE_DEPARTURE:
            return {
                "vehicle_type": _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All"))),
                "route_filter": str(cfg.get("route_filter", "")).strip(),
                "direction": str(cfg.get("direction", "All")).strip() or "All",
                "from_query": str(cfg.get("from_query", "")).strip(),
                "window_minutes": _clamp_int(cfg.get("window_minutes"), DEFAULT_WINDOW_MINUTES, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES),
                "max_stops": _clamp_int(cfg.get("max_stops"), 12, MIN_WATCH_MAX_STOPS, MAX_WATCH_MAX_STOPS),
                "limit": _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT),
            }

        if watch_type == WATCH_TYPE_NEARBY:
            source_type = str(cfg.get("location_source_type", WATCH_LOCATION_PERSON)).strip()
            if source_type not in {WATCH_LOCATION_PERSON, WATCH_LOCATION_ZONE, WATCH_LOCATION_FIXED}:
                source_type = WATCH_LOCATION_PERSON
            return {
                "location_source_type": source_type,
                "person_entity": str(cfg.get("person_entity", "")).strip(),
                "zone_entity": str(cfg.get("zone_entity", "")).strip(),
                "fixed_lat": _to_float(cfg.get("fixed_lat")),
                "fixed_lon": _to_float(cfg.get("fixed_lon")),
                "radius_meters": _clamp_int(cfg.get("radius_meters"), DEFAULT_NEARBY_RADIUS_METERS, MIN_NEARBY_RADIUS_METERS, MAX_NEARBY_RADIUS_METERS),
                "vehicle_type": _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All"))),
                "window_minutes": _clamp_int(cfg.get("window_minutes"), DEFAULT_WINDOW_MINUTES, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES),
                "max_stops": _clamp_int(cfg.get("max_stops"), 8, 1, 20),
                "limit_per_stop": _clamp_int(cfg.get("limit_per_stop"), 6, 1, 30),
            }

        if watch_type == WATCH_TYPE_STATION_QUERY:
            station_queries = cfg.get("station_queries", [])
            if isinstance(station_queries, str):
                station_queries = [q.strip() for q in station_queries.split(",") if q.strip()]
            elif isinstance(station_queries, list):
                station_queries = [str(q).strip() for q in station_queries if str(q).strip()]
            else:
                station_queries = []
            return {
                "station_queries": station_queries,
                "vehicle_type": _normalize_mode(cfg.get("vehicle_type", cfg.get("mode", "All"))),
                "route_filter": str(cfg.get("route_filter", "")).strip(),
                "direction": str(cfg.get("direction", "All")).strip() or "All",
                "window_minutes": _clamp_int(cfg.get("window_minutes"), DEFAULT_WINDOW_MINUTES, MIN_WINDOW_MINUTES, MAX_WINDOW_MINUTES),
                "max_stops": _clamp_int(cfg.get("max_stops"), 12, MIN_WATCH_MAX_STOPS, MAX_WATCH_MAX_STOPS),
                "limit": _clamp_int(cfg.get("limit"), 20, MIN_WATCH_LIMIT, MAX_WATCH_LIMIT),
            }

        return cfg

    def _next_watch_id(self) -> str:
        i = 1
        while True:
            candidate = f"watch_{i}"
            if candidate not in self._watch_registry:
                return candidate
            i += 1

    def _next_watch_key(self, name: str, exclude_watch_id: str | None = None) -> str:
        return self._normalize_watch_key("", name, exclude_watch_id=exclude_watch_id)

    def _normalize_watch_key(self, raw_key: str, name: str, exclude_watch_id: str | None = None) -> str:
        base = slugify(raw_key.strip()) if raw_key.strip() else slugify(name.strip())
        if not base:
            base = "watch"
        candidate = base
        suffix = 2
        used = {
            str(w.get("watch_key"))
            for wid, w in self._watch_registry.items()
            if wid != exclude_watch_id
            if str(w.get("watch_key"))
        }
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate


def _extract_line_code(route_label: str) -> str:
    prefix = route_label.split("-", 1)[0].strip()
    match = re.match(r"^(\d+)", prefix)
    if match:
        return match.group(1)
    return prefix or "?"


def _minutes_until(now_local: datetime, rt_iso: str | None) -> int | None:
    if not rt_iso:
        return None
    try:
        dep_time = datetime.fromisoformat(rt_iso)
    except ValueError:
        return None
    delta = dep_time - now_local
    minutes = int(delta.total_seconds() // 60)
    return minutes if minutes >= 0 else None


def _route_filter_match(route_filter: str, route_label: str, line_code: str) -> bool:
    route_filter_l = route_filter.lower().strip()
    if not route_filter_l:
        return True

    line_l = line_code.lower().strip()
    if line_l == route_filter_l:
        return True

    route_l = route_label.lower().strip()
    if route_l.startswith(f"{route_filter_l} -"):
        return True
    return route_filter_l in route_l


def _to_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_mode(value) -> str:
    raw = str(value or "All").strip()
    if raw in {"tram", "bus", "All"}:
        return raw
    return "All"


def _clamp_int(value, default: int, min_value: int, max_value: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(min_value, min(max_value, out))
