"""Config flow for Zagreb Transit."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
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
    MAX_WATCH_LIMIT,
    MAX_WATCH_MAX_STOPS,
    MAX_WINDOW_MINUTES,
    MIN_NEARBY_RADIUS_METERS,
    MIN_WATCH_LIMIT,
    MIN_WATCH_MAX_STOPS,
    MIN_WINDOW_MINUTES,
    WATCH_LOCATION_FIXED,
    WATCH_LOCATION_PERSON,
    WATCH_LOCATION_ZONE,
    WATCH_VEHICLE_TYPES,
)

ACTION_CORE = "core"
ACTION_ADD = "add_watch"
ACTION_EDIT = "edit_watch"
ACTION_REMOVE = "remove_watch"


class ZagrebTransitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Zagreb Transit."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(
            title="Zagreb Transit",
            data={},
            options=_default_options(),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ZagrebTransitOptionsFlow()


class ZagrebTransitOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Zagreb Transit."""

    def __init__(self) -> None:
        self._pending_watch_type: str | None = None
        self._pending_watch_name: str | None = None
        self._pending_watch_enabled: bool = True
        self._pending_cfg: dict = {}
        self._edit_watch_id: str | None = None

    def _is_hr(self) -> bool:
        lang = str(getattr(self.hass.config, "language", "") or "").lower()
        return lang.startswith("hr")

    def _action_menu(self) -> dict[str, str]:
        if self._is_hr():
            return {
                "Osnovne postavke (intervali osvježavanja i ponašanje)": ACTION_CORE,
                "Dodaj praćenje (novi transit entitet)": ACTION_ADD,
                "Uredi praćenje (izmijeni postojeći entitet)": ACTION_EDIT,
                "Ukloni praćenje (obriši postojeći entitet)": ACTION_REMOVE,
            }
        return {
            "Core settings (refresh intervals, base behavior)": ACTION_CORE,
            "Add watch (new transit tracking entity)": ACTION_ADD,
            "Edit watch (modify existing tracking entity)": ACTION_EDIT,
            "Remove watch (delete existing tracking entity)": ACTION_REMOVE,
        }

    def _watch_type_menu(self) -> dict[str, str]:
        if self._is_hr():
            return {
                "Relacija (polazna -> odredišna s ETA)": "od",
                "Polasci (samo s polazne stanice)": "departure",
                "U blizini (osoba/zona/fiksna lokacija)": "nearby",
                "Upit stanica (više naziva stanica)": "station_query",
            }
        return {
            "Route watch (start -> destination with ETA)": "od",
            "Departure watch (from stop only)": "departure",
            "Nearby watch (person/zone/fixed location)": "nearby",
            "Station query watch (multiple station names)": "station_query",
        }

    async def async_step_init(self, user_input=None):
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="unknown")

        action_menu = self._action_menu()
        actions = [label for label, action in action_menu.items() if action in {ACTION_CORE, ACTION_ADD}]
        if coordinator.watch_ids():
            actions.extend(
                [label for label, action in action_menu.items() if action in {ACTION_EDIT, ACTION_REMOVE}]
            )

        if user_input is not None:
            action = action_menu.get(user_input["action"], ACTION_CORE)
            if action == ACTION_CORE:
                return await self.async_step_core()
            if action == ACTION_ADD:
                return await self.async_step_add_watch_basic()
            if action == ACTION_EDIT:
                return await self.async_step_edit_watch_select()
            if action == ACTION_REMOVE:
                return await self.async_step_remove_watch()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default=actions[0]): vol.In(actions),
                }
            ),
        )

    async def async_step_core(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opt = {**_default_options(), **self.config_entry.options}
        schema: dict = {
            vol.Required(CONF_UPDATE_INTERVAL, default=opt.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(CONF_REALTIME_INTERVAL, default=opt.get(CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(CONF_STATIC_REFRESH_HOURS, default=opt.get(CONF_STATIC_REFRESH_HOURS, DEFAULT_STATIC_REFRESH_HOURS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=48)),
            vol.Required(CONF_DEFAULT_WINDOW_MINUTES, default=opt.get(CONF_DEFAULT_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=MIN_WINDOW_MINUTES, max=MAX_WINDOW_MINUTES)),
            vol.Required(CONF_NOTIFICATIONS_ENABLED, default=bool(opt.get(CONF_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED))): cv.boolean,
        }
        return self.async_show_form(step_id="core", data_schema=vol.Schema(schema))

    async def async_step_add_watch_basic(self, user_input=None):
        watch_type_menu = self._watch_type_menu()
        if user_input is not None:
            self._pending_watch_name = str(user_input["name"]).strip()
            self._pending_watch_type = watch_type_menu.get(str(user_input["watch_type"]), "od")
            self._pending_watch_enabled = bool(user_input.get("enabled", True))
            self._pending_cfg = {}
            return await self._next_add_step()

        return self.async_show_form(
            step_id="add_watch_basic",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): cv.string,
                    vol.Required("watch_type"): vol.In(list(watch_type_menu)),
                    vol.Required("enabled", default=True): cv.boolean,
                }
            ),
        )

    async def async_step_edit_watch_select(self, user_input=None):
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="unknown")

        summaries = coordinator.watch_summaries()
        if not summaries:
            return self.async_create_entry(title="", data=self.config_entry.options)

        choices_map = {
            f"{row.get('name') or row.get('watch_id')} [{row.get('type')}]": row["watch_id"]
            for row in summaries
        }
        labels = list(choices_map)

        if user_input is not None:
            watch_id = choices_map.get(str(user_input.get("watch", "")).strip())
            if not watch_id:
                return self.async_abort(reason="unknown")
            watch = coordinator.watch_by_id(watch_id)
            if not watch:
                return self.async_abort(reason="unknown")
            self._edit_watch_id = watch_id
            self._pending_watch_type = str(watch.get("type") or "od")
            self._pending_watch_name = str(watch.get("name") or watch_id)
            self._pending_watch_enabled = bool(watch.get("enabled", True))
            self._pending_cfg = dict(watch.get("config", {}))
            return await self.async_step_edit_watch_basic()

        return self.async_show_form(
            step_id="edit_watch_select",
            data_schema=vol.Schema(
                {
                    vol.Required("watch", default=labels[0]): vol.In(labels),
                }
            ),
        )

    async def async_step_edit_watch_basic(self, user_input=None):
        if self._edit_watch_id is None or self._pending_watch_type is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            self._pending_watch_name = str(user_input["name"]).strip()
            self._pending_watch_enabled = bool(user_input.get("enabled", True))
            return await self._next_add_step()

        return self.async_show_form(
            step_id="edit_watch_basic",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=self._pending_watch_name or "Watch"): cv.string,
                    vol.Required("enabled", default=self._pending_watch_enabled): cv.boolean,
                }
            ),
        )

    async def async_step_remove_watch(self, user_input=None):
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="unknown")

        summaries = coordinator.watch_summaries()
        if not summaries:
            return self.async_create_entry(title="", data=self.config_entry.options)

        choices_map = {
            f"{row.get('name') or row.get('watch_id')} [{row.get('type')}]": row["watch_id"]
            for row in summaries
        }
        labels = list(choices_map)

        if user_input is not None:
            watch_id = choices_map.get(str(user_input.get("watch", "")).strip())
            if not watch_id:
                return self.async_abort(reason="unknown")
            await coordinator.async_remove_watch(watch_id)
            return self.async_create_entry(title="", data=self.config_entry.options)

        return self.async_show_form(
            step_id="remove_watch",
            data_schema=vol.Schema(
                {
                    vol.Required("watch", default=labels[0]): vol.In(labels),
                }
            ),
        )

    async def async_step_watch_mode(self, user_input=None):
        if user_input is not None:
            self._pending_cfg.update(user_input)
            if self._pending_watch_type in {"od", "departure"}:
                return await self.async_step_watch_route()
            if self._pending_watch_type == "station_query":
                return await self.async_step_watch_station_query()

        schema = {
            vol.Required("vehicle_type", default=str(self._pending_cfg.get("vehicle_type", "All"))): vol.In(WATCH_VEHICLE_TYPES),
            vol.Required("window_minutes", default=int(self._pending_cfg.get("window_minutes", DEFAULT_WINDOW_MINUTES))): vol.All(vol.Coerce(int), vol.Range(min=MIN_WINDOW_MINUTES, max=MAX_WINDOW_MINUTES)),
            vol.Required("limit", default=int(self._pending_cfg.get("limit", 20))): vol.All(vol.Coerce(int), vol.Range(min=MIN_WATCH_LIMIT, max=MAX_WATCH_LIMIT)),
        }
        if self._pending_watch_type == "departure":
            schema[vol.Required("max_stops", default=int(self._pending_cfg.get("max_stops", 12)))] = vol.All(vol.Coerce(int), vol.Range(min=MIN_WATCH_MAX_STOPS, max=MAX_WATCH_MAX_STOPS))
        return self.async_show_form(step_id="watch_mode", data_schema=vol.Schema(schema))

    async def async_step_watch_nearby_source(self, user_input=None):
        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self.async_step_watch_nearby_location()

        return self.async_show_form(
            step_id="watch_nearby_source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "location_source_type",
                        default=str(self._pending_cfg.get("location_source_type", WATCH_LOCATION_PERSON)),
                    ): vol.In([WATCH_LOCATION_PERSON, WATCH_LOCATION_ZONE, WATCH_LOCATION_FIXED]),
                }
            ),
        )

    async def async_step_watch_nearby_location(self, user_input=None):
        source_type = str(self._pending_cfg.get("location_source_type", WATCH_LOCATION_PERSON)).strip()

        person_entities = sorted([st.entity_id for st in self.hass.states.async_all("person")])
        zone_entities = sorted([st.entity_id for st in self.hass.states.async_all("zone")])
        person_choices = self._with_default(person_entities if person_entities else [""], str(self._pending_cfg.get("person_entity", "")))
        zone_choices = self._with_default(zone_entities if zone_entities else [""], str(self._pending_cfg.get("zone_entity", "")))

        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self.async_step_watch_nearby_filters()

        if source_type == WATCH_LOCATION_PERSON:
            schema = vol.Schema(
                {
                    vol.Required("person_entity", default=str(self._pending_cfg.get("person_entity", person_choices[0]))): vol.In(person_choices),
                }
            )
        elif source_type == WATCH_LOCATION_ZONE:
            schema = vol.Schema(
                {
                    vol.Required("zone_entity", default=str(self._pending_cfg.get("zone_entity", zone_choices[0]))): vol.In(zone_choices),
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required("fixed_lat", default=float(self._pending_cfg.get("fixed_lat", 0.0) or 0.0)): vol.Coerce(float),
                    vol.Required("fixed_lon", default=float(self._pending_cfg.get("fixed_lon", 0.0) or 0.0)): vol.Coerce(float),
                }
            )

        return self.async_show_form(step_id="watch_nearby_location", data_schema=schema)

    async def async_step_watch_nearby_filters(self, user_input=None):
        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self._finalize_watch()

        schema = {
            vol.Required("vehicle_type", default=str(self._pending_cfg.get("vehicle_type", "All"))): vol.In(WATCH_VEHICLE_TYPES),
            vol.Required("window_minutes", default=int(self._pending_cfg.get("window_minutes", DEFAULT_WINDOW_MINUTES))): vol.All(vol.Coerce(int), vol.Range(min=MIN_WINDOW_MINUTES, max=MAX_WINDOW_MINUTES)),
            vol.Required("radius_meters", default=int(self._pending_cfg.get("radius_meters", DEFAULT_NEARBY_RADIUS_METERS))): vol.All(vol.Coerce(int), vol.Range(min=MIN_NEARBY_RADIUS_METERS, max=MAX_NEARBY_RADIUS_METERS)),
            vol.Required("max_stops", default=int(self._pending_cfg.get("max_stops", 8))): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
            vol.Required("limit_per_stop", default=int(self._pending_cfg.get("limit_per_stop", 6))): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
        }
        return self.async_show_form(step_id="watch_nearby_filters", data_schema=vol.Schema(schema))

    async def async_step_watch_route(self, user_input=None):
        index = self._index()
        if not index:
            return self.async_abort(reason="unknown")

        vehicle_type = str(self._pending_cfg.get("vehicle_type", "All"))
        routes = [""] + index.route_options(vehicle_type)
        routes = self._with_default(routes, str(self._pending_cfg.get("route_filter", "")))

        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self.async_step_watch_direction()

        return self.async_show_form(
            step_id="watch_route",
            data_schema=vol.Schema({
                vol.Required("route_filter", default=str(self._pending_cfg.get("route_filter", routes[0] if routes else ""))): vol.In(routes),
            }),
        )

    async def async_step_watch_direction(self, user_input=None):
        index = self._index()
        if not index:
            return self.async_abort(reason="unknown")

        route = str(self._pending_cfg.get("route_filter", "")).strip()
        directions = ["All"]
        if route:
            directions.extend(index.get_directions_for_route(route))
        directions = self._with_default(directions, str(self._pending_cfg.get("direction", "All")))

        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self.async_step_watch_from()

        return self.async_show_form(
            step_id="watch_direction",
            data_schema=vol.Schema({
                vol.Required("direction", default=str(self._pending_cfg.get("direction", directions[0]))): vol.In(directions),
            }),
        )

    async def async_step_watch_from(self, user_input=None):
        index = self._index()
        if not index:
            return self.async_abort(reason="unknown")

        route = str(self._pending_cfg.get("route_filter", "")).strip()
        direction = str(self._pending_cfg.get("direction", "All")).strip() or "All"

        if route:
            from_options = index.get_stops_for_route(route, direction)
        else:
            from_options = index.station_options()
        from_options = self._with_default(from_options, str(self._pending_cfg.get("from_query", "")))

        if user_input is not None:
            self._pending_cfg.update(user_input)
            if self._pending_watch_type == "od":
                return await self.async_step_watch_to()
            return await self._finalize_watch()

        return self.async_show_form(
            step_id="watch_from",
            data_schema=vol.Schema({
                vol.Required("from_query", default=str(self._pending_cfg.get("from_query", from_options[0] if from_options else ""))): vol.In(from_options),
            }),
        )

    async def async_step_watch_to(self, user_input=None):
        index = self._index()
        if not index:
            return self.async_abort(reason="unknown")

        route = str(self._pending_cfg.get("route_filter", "")).strip()
        direction = str(self._pending_cfg.get("direction", "All")).strip() or "All"
        from_stop = str(self._pending_cfg.get("from_query", "")).strip()

        if route and from_stop:
            to_options = index.get_to_stops(route, from_stop, direction)
        else:
            to_options = [s for s in index.station_options() if s != from_stop]
        to_options = self._with_default(to_options, str(self._pending_cfg.get("to_query", "")))

        errors: dict[str, str] = {}
        if user_input is not None:
            to_stop = str(user_input.get("to_query", "")).strip()
            if to_stop == from_stop:
                errors["base"] = "same_stop"
            elif route and to_stop not in index.get_to_stops(route, from_stop, direction):
                errors["base"] = "invalid_od_order"
            else:
                self._pending_cfg.update(user_input)
                return await self._finalize_watch()

        return self.async_show_form(
            step_id="watch_to",
            data_schema=vol.Schema({
                vol.Required("to_query", default=str(self._pending_cfg.get("to_query", to_options[0] if to_options else ""))): vol.In(to_options),
            }),
            errors=errors,
        )

    async def async_step_watch_station_query(self, user_input=None):
        if user_input is not None:
            self._pending_cfg.update(user_input)
            return await self._finalize_watch()

        index = self._index()
        vehicle_type = str(self._pending_cfg.get("vehicle_type", "All"))
        routes = [""] + (index.route_options(vehicle_type) if index else [])
        routes = self._with_default(routes, str(self._pending_cfg.get("route_filter", "")))

        station_queries = self._pending_cfg.get("station_queries", [])
        if isinstance(station_queries, list):
            station_queries = ",".join(station_queries)

        schema = {
            vol.Required("station_queries", default=str(station_queries or "")): cv.string,
            # Optional by design: empty route_filter means "all routes".
            vol.Optional(
                "route_filter",
                default=str(self._pending_cfg.get("route_filter", routes[0] if routes else "")),
            ): vol.In(routes),
            vol.Required("direction", default=str(self._pending_cfg.get("direction", "All"))): cv.string,
            vol.Required("max_stops", default=int(self._pending_cfg.get("max_stops", 12))): vol.All(vol.Coerce(int), vol.Range(min=MIN_WATCH_MAX_STOPS, max=MAX_WATCH_MAX_STOPS)),
            vol.Required("limit", default=int(self._pending_cfg.get("limit", 20))): vol.All(vol.Coerce(int), vol.Range(min=MIN_WATCH_LIMIT, max=MAX_WATCH_LIMIT)),
        }
        return self.async_show_form(step_id="watch_station_query", data_schema=vol.Schema(schema))

    async def _next_add_step(self):
        if self._pending_watch_type == "nearby":
            return await self.async_step_watch_nearby_source()
        if self._pending_watch_type in {"od", "departure", "station_query"}:
            return await self.async_step_watch_mode()
        return await self._finalize_watch()

    async def _finalize_watch(self):
        coordinator = self._coordinator()
        if coordinator is None or not self._pending_watch_type:
            return self.async_abort(reason="unknown")

        payload = dict(self._pending_cfg)
        if self._pending_watch_type == "station_query":
            raw = str(payload.get("station_queries", "")).strip()
            payload["station_queries"] = [item.strip() for item in raw.split(",") if item.strip()]

        if self._edit_watch_id:
            await coordinator.async_update_watch(
                watch_id=self._edit_watch_id,
                name=self._pending_watch_name or "Watch",
                enabled=self._pending_watch_enabled,
                config=payload,
            )
        else:
            await coordinator.async_add_watch(
                name=self._pending_watch_name or "Watch",
                watch_type=self._pending_watch_type,
                enabled=self._pending_watch_enabled,
                config=payload,
            )

        return self.async_create_entry(title="", data=self.config_entry.options)

    def _coordinator(self):
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    def _index(self):
        coordinator = self._coordinator()
        if coordinator is None:
            return None
        return coordinator.index

    def _with_default(self, options: list[str], default_value: str) -> list[str]:
        out = [opt for opt in options if opt is not None]
        if default_value and default_value not in out:
            out.insert(0, default_value)
        if not out:
            out = [default_value or ""]
        return out


def _default_options() -> dict:
    return {
        CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
        CONF_REALTIME_INTERVAL: DEFAULT_REALTIME_INTERVAL,
        CONF_STATIC_REFRESH_HOURS: DEFAULT_STATIC_REFRESH_HOURS,
        CONF_DEFAULT_WINDOW_MINUTES: DEFAULT_WINDOW_MINUTES,
        CONF_NOTIFICATIONS_ENABLED: DEFAULT_NOTIFICATIONS_ENABLED,
    }
