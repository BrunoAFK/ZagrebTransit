"""In-memory GTFS indexing and trip calculations for Zagreb Transit."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import io
import math
import logging
from typing import Any
import zipfile

_LOGGER = logging.getLogger(__name__)


WEEKDAY_MAP = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}


@dataclass(slots=True)
class StopTime:
    stop_id: str
    stop_sequence: int
    departure_secs: int
    arrival_secs: int


class GtfsIndex:
    """GTFS indexes for route/stop queries."""

    def __init__(self, zip_payload: bytes) -> None:
        self.routes: dict[str, str] = {}
        self.route_types: dict[str, int] = {}
        self.route_label_to_id: dict[str, str] = {}
        self.stops: dict[str, str] = {}
        self.stop_coords: dict[str, tuple[float, float]] = {}
        self.stop_label_to_id: dict[str, str] = {}
        self.trips: dict[str, dict[str, str]] = {}
        self.stop_times_by_trip: dict[str, list[StopTime]] = defaultdict(list)
        self.trips_by_route: dict[str, list[str]] = defaultdict(list)
        self.departures_by_stop: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.calendar: dict[str, dict[str, str]] = {}
        self.calendar_dates: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._active_services_cache: dict = {}

        self._load(zip_payload)

    def route_options(self, mode_filter: str | None = None) -> list[str]:
        if not mode_filter or mode_filter == "All":
            return sorted(self.route_label_to_id)
        return sorted(
            label
            for label, route_id in self.route_label_to_id.items()
            if _route_mode(self.route_types.get(route_id)) == mode_filter
        )

    def station_options(self) -> list[str]:
        return sorted(self.stop_label_to_id)

    def get_stops_for_route(self, route_label: str, direction_label: str | None = None) -> list[str]:
        route_id = self.route_label_to_id.get(route_label)
        if not route_id:
            return []

        seq_min: dict[str, int] = {}
        for trip_id in self.trips_by_route.get(route_id, []):
            trip = self.trips.get(trip_id, {})
            if direction_label and direction_label != "All":
                headsign = trip.get("trip_headsign") or "Unknown"
                if headsign != direction_label:
                    continue
            for st in self.stop_times_by_trip.get(trip_id, []):
                seq_min[st.stop_id] = min(seq_min.get(st.stop_id, st.stop_sequence), st.stop_sequence)

        ordered = sorted(seq_min.items(), key=lambda item: item[1])
        return [self._stop_label(stop_id) for stop_id, _ in ordered]

    def get_to_stops(self, route_label: str, from_stop_label: str, direction_label: str | None = None) -> list[str]:
        route_id = self.route_label_to_id.get(route_label)
        from_stop = self.stop_label_to_id.get(from_stop_label)
        if not route_id or not from_stop:
            return []

        to_stops: set[str] = set()
        for trip_id in self.trips_by_route.get(route_id, []):
            trip = self.trips.get(trip_id, {})
            if direction_label and direction_label != "All":
                headsign = trip.get("trip_headsign") or "Unknown"
                if headsign != direction_label:
                    continue
            stop_times = self.stop_times_by_trip.get(trip_id, [])
            from_entry = next((st for st in stop_times if st.stop_id == from_stop), None)
            if not from_entry:
                continue
            for st in stop_times:
                if st.stop_sequence > from_entry.stop_sequence:
                    to_stops.add(self._stop_label(st.stop_id))

        return sorted(to_stops)

    def get_directions_for_route(self, route_label: str) -> list[str]:
        route_id = self.route_label_to_id.get(route_label)
        if not route_id:
            return []
        directions: set[str] = set()
        for trip_id in self.trips_by_route.get(route_id, []):
            headsign = self.trips.get(trip_id, {}).get("trip_headsign") or "Unknown"
            directions.add(headsign)
        return sorted(directions)

    def get_directions_for_station(self, station_label: str) -> list[str]:
        stop_id = self.stop_label_to_id.get(station_label)
        if not stop_id:
            return []

        directions: set[str] = set()
        for trip_id, _ in self.departures_by_stop.get(stop_id, []):
            headsign = self.trips.get(trip_id, {}).get("trip_headsign") or "Unknown"
            directions.add(headsign)
        return sorted(directions)

    def upcoming_od_do(
        self,
        now_local: datetime,
        route_label: str,
        direction_label: str,
        from_stop_label: str,
        to_stop_label: str,
        delay_by_trip: dict[str, int],
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        route_id = self.route_label_to_id.get(route_label)
        from_stop = self.stop_label_to_id.get(from_stop_label)
        to_stop = self.stop_label_to_id.get(to_stop_label)
        if not route_id or not from_stop or not to_stop:
            return []

        service_dates = [now_local.date() - timedelta(days=1), now_local.date(), now_local.date() + timedelta(days=1)]
        active_services = {
            d: self._active_services_for_day(d)
            for d in service_dates
        }

        results: list[dict[str, Any]] = []
        for trip_id in self.trips_by_route.get(route_id, []):
            headsign = self.trips.get(trip_id, {}).get("trip_headsign") or "Unknown"
            if direction_label and direction_label != "All" and headsign != direction_label:
                continue
            service_id = self.trips.get(trip_id, {}).get("service_id")
            if not service_id:
                continue

            stop_times = self.stop_times_by_trip.get(trip_id, [])
            from_entry = next((st for st in stop_times if st.stop_id == from_stop), None)
            to_entry = next((st for st in stop_times if st.stop_id == to_stop and from_entry and st.stop_sequence > from_entry.stop_sequence), None)
            if not from_entry or not to_entry:
                continue

            for service_day, services in active_services.items():
                if service_id not in services:
                    continue

                dep_planned = self._time_for_service_day(service_day, from_entry.departure_secs)
                arr_planned = self._time_for_service_day(service_day, to_entry.arrival_secs)
                delay_seconds = int(delay_by_trip.get(trip_id, 0))
                dep_rt = dep_planned + timedelta(seconds=delay_seconds)
                arr_rt = arr_planned + timedelta(seconds=delay_seconds)
                if dep_rt < now_local:
                    continue

                results.append(
                    {
                        "trip_id": trip_id,
                        "route": route_label,
                        "direction": headsign,
                        "from_stop": from_stop_label,
                        "to_stop": to_stop_label,
                        "departure_planned": dep_planned.isoformat(),
                        "departure_rt": dep_rt.isoformat(),
                        "arrival_planned": arr_planned.isoformat(),
                        "arrival_rt": arr_rt.isoformat(),
                        "delay_minutes": round(delay_seconds / 60, 1),
                    }
                )

        # Deduplicate potentially repeated rows across service-day overlap.
        dedup: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in sorted(results, key=lambda row: row["departure_rt"]):
            key = (item.get("trip_id", ""), item.get("departure_rt", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        results = dedup
        return results[:limit]

    def station_direction_board(
        self,
        now_local: datetime,
        station_label: str,
        direction_label: str,
        board_route_label: str,
        window_minutes: int,
        delay_by_trip: dict[str, int],
    ) -> list[dict[str, Any]]:
        stop_id = self.stop_label_to_id.get(station_label)
        if not stop_id:
            return []

        window_end = now_local + timedelta(minutes=window_minutes)
        service_dates = [now_local.date() - timedelta(days=1), now_local.date(), now_local.date() + timedelta(days=1)]
        active_services = {
            d: self._active_services_for_day(d)
            for d in service_dates
        }

        entries: list[dict[str, Any]] = []
        for trip_id, dep_secs in self.departures_by_stop.get(stop_id, []):
            trip = self.trips.get(trip_id, {})
            service_id = trip.get("service_id")
            if not service_id:
                continue

            headsign = trip.get("trip_headsign") or "Unknown"
            if direction_label and direction_label != "All" and headsign != direction_label:
                continue

            for service_day, services in active_services.items():
                if service_id not in services:
                    continue

                dep_planned = self._time_for_service_day(service_day, dep_secs)
                delay_seconds = int(delay_by_trip.get(trip_id, 0))
                dep_rt = dep_planned + timedelta(seconds=delay_seconds)

                if dep_rt < now_local or dep_rt > window_end:
                    continue

                route_id = trip.get("route_id", "")
                trip_route_label = self.routes.get(route_id, route_id)
                if board_route_label and board_route_label != "All" and board_route_label != trip_route_label:
                    continue

                entries.append(
                    {
                        "trip_id": trip_id,
                        "route": trip_route_label,
                        "direction": headsign,
                        "mode": _route_mode(self.route_types.get(route_id)),
                        "planned": dep_planned.isoformat(),
                        "rt": dep_rt.isoformat(),
                        "delay_minutes": round(delay_seconds / 60, 1),
                    }
                )

        entries.sort(key=lambda item: item["rt"])
        return entries

    def upcoming_between_stop_names(
        self,
        now_local: datetime,
        from_query: str,
        to_query: str,
        window_minutes: int,
        delay_by_trip: dict[str, int],
        mode_filter: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """Return upcoming departures between stop-name queries across all routes."""
        from_query_l = from_query.lower().strip()
        to_query_l = to_query.lower().strip()
        if not from_query_l or not to_query_l:
            return []

        # Support both plain name queries ("Utrina") and full labels ("Utrina [183_2]").
        from_ids = self._stop_ids_for_query(from_query)
        to_ids = self._stop_ids_for_query(to_query)
        if not from_ids or not to_ids:
            return []

        window_end = now_local + timedelta(minutes=window_minutes)
        service_dates = [now_local.date() - timedelta(days=1), now_local.date(), now_local.date() + timedelta(days=1)]
        active_services = {d: self._active_services_for_day(d) for d in service_dates}

        results: list[dict[str, Any]] = []
        for trip_id, trip in self.trips.items():
            route_id = trip.get("route_id", "")
            route_mode = _route_mode(self.route_types.get(route_id))
            if mode_filter and route_mode != mode_filter:
                continue

            service_id = trip.get("service_id")
            if not service_id:
                continue

            stop_times = self.stop_times_by_trip.get(trip_id, [])
            if not stop_times:
                continue

            from_entry = next((st for st in stop_times if st.stop_id in from_ids), None)
            if not from_entry:
                continue

            to_entry = next(
                (st for st in stop_times if st.stop_id in to_ids and st.stop_sequence > from_entry.stop_sequence),
                None,
            )
            if not to_entry:
                continue

            for service_day, services in active_services.items():
                if service_id not in services:
                    continue

                dep_planned = self._time_for_service_day(service_day, from_entry.departure_secs)
                arr_planned = self._time_for_service_day(service_day, to_entry.arrival_secs)
                delay_seconds = int(delay_by_trip.get(trip_id, 0))
                dep_rt = dep_planned + timedelta(seconds=delay_seconds)
                arr_rt = arr_planned + timedelta(seconds=delay_seconds)

                if dep_rt < now_local or dep_rt > window_end:
                    continue

                from_label = self._stop_label(from_entry.stop_id)
                to_label = self._stop_label(to_entry.stop_id)
                route_label = self.routes.get(route_id, route_id)
                results.append(
                    {
                        "trip_id": trip_id,
                        "route": route_label,
                        "mode": route_mode,
                        "direction": trip.get("trip_headsign") or "Unknown",
                        "from_stop": from_label,
                        "to_stop": to_label,
                        "departure_planned": dep_planned.isoformat(),
                        "departure_rt": dep_rt.isoformat(),
                        "arrival_planned": arr_planned.isoformat(),
                        "arrival_rt": arr_rt.isoformat(),
                        "delay_minutes": round(delay_seconds / 60, 1),
                    }
                )

        dedup: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in sorted(results, key=lambda row: row["departure_rt"]):
            key = (item.get("trip_id", ""), item.get("departure_rt", ""))
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup[:limit]

    def _stop_ids_for_query(self, query: str) -> set[str]:
        raw = (query or "").strip()
        ql = raw.lower()
        out: set[str] = set()
        if not raw:
            return out

        # Exact full stop label match.
        exact = self.stop_label_to_id.get(raw)
        if exact:
            out.add(exact)

        # Optional label suffix with explicit stop id, e.g. "Utrina [183_2]".
        if "[" in raw and raw.endswith("]"):
            maybe_id = raw.rsplit("[", 1)[1][:-1].strip()
            if maybe_id in self.stops:
                out.add(maybe_id)

        # Fallback substring match by stop name.
        for sid, name in self.stops.items():
            if ql in name.lower():
                out.add(sid)

        return out

    def nearby_board(
        self,
        now_local: datetime,
        user_lat: float,
        user_lon: float,
        radius_meters: int,
        window_minutes: int,
        delay_by_trip: dict[str, int],
        max_stops: int = 8,
    ) -> list[dict[str, Any]]:
        """Return nearby stops with departures for the selected window."""
        candidates: list[tuple[str, float]] = []
        for stop_id, coords in self.stop_coords.items():
            distance = _haversine_m(user_lat, user_lon, coords[0], coords[1])
            if distance <= radius_meters:
                candidates.append((stop_id, distance))

        candidates.sort(key=lambda item: item[1])
        out: list[dict[str, Any]] = []

        for stop_id, distance in candidates[:max(1, int(max_stops))]:
            stop_label = self._stop_label(stop_id)
            departures = self.station_direction_board(
                now_local=now_local,
                station_label=stop_label,
                direction_label="All",
                board_route_label="All",
                window_minutes=window_minutes,
                delay_by_trip=delay_by_trip,
            )
            if not departures:
                continue

            tram_count = sum(1 for d in departures if d.get("mode") == "tram")
            bus_count = sum(1 for d in departures if d.get("mode") == "bus")
            lat, lon = self.stop_coords.get(stop_id, (None, None))
            map_url = None
            if lat is not None and lon is not None:
                map_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

            out.append(
                {
                    "stop": stop_label,
                    "distance_meters": round(distance, 1),
                    "tram_departures": tram_count,
                    "bus_departures": bus_count,
                    "map_url": map_url,
                    "departures": departures,
                }
            )

        return out

    def stations_matching_queries(self, queries: list[str], max_stops: int = 12) -> list[str]:
        """Return station labels matching any query substring."""
        clean_queries = [q.strip().lower() for q in queries if q and q.strip()]
        if not clean_queries:
            return []

        seen: set[str] = set()
        matched: list[str] = []
        stations = self.station_options()
        for query in clean_queries:
            for station in stations:
                if query in station.lower() and station not in seen:
                    seen.add(station)
                    matched.append(station)
                    if len(matched) >= max_stops:
                        return matched
        return matched

    def boards_for_station_queries(
        self,
        now_local: datetime,
        station_queries: list[str],
        window_minutes: int,
        delay_by_trip: dict[str, int],
        max_stops: int = 12,
    ) -> list[dict[str, Any]]:
        """Return station boards for all matched stations."""
        stations = self.stations_matching_queries(station_queries, max_stops=max_stops)
        out: list[dict[str, Any]] = []
        for station in stations:
            departures = self.station_direction_board(
                now_local=now_local,
                station_label=station,
                direction_label="All",
                board_route_label="All",
                window_minutes=window_minutes,
                delay_by_trip=delay_by_trip,
            )
            if departures:
                out.append(
                    {
                        "stop": station,
                        "departures": departures,
                    }
                )
        return out

    def _time_for_service_day(self, service_day, value_secs: int) -> datetime:
        return datetime.combine(service_day, datetime.min.time()) + timedelta(seconds=value_secs)

    def _active_services_for_day(self, day) -> set[str]:
        cached = self._active_services_cache.get(day)
        if cached is not None:
            return set(cached)

        services = set()
        weekday = WEEKDAY_MAP[day.weekday()]

        for service_id, row in self.calendar.items():
            start = _yyyymmdd_to_date(row.get("start_date"))
            end = _yyyymmdd_to_date(row.get("end_date"))
            if start and day < start:
                continue
            if end and day > end:
                continue
            if row.get(weekday) == "1":
                services.add(service_id)

        date_key = day.strftime("%Y%m%d")
        for service_id, exception_type in self.calendar_dates.get(date_key, []):
            if exception_type == "1":
                services.add(service_id)
            elif exception_type == "2":
                services.discard(service_id)

        self._active_services_cache[day] = set(services)
        if len(self._active_services_cache) > 8:
            newest_days = sorted(self._active_services_cache.keys())[-8:]
            self._active_services_cache = {
                k: self._active_services_cache[k]
                for k in newest_days
            }
        return services

    def _stop_label(self, stop_id: str) -> str:
        name = self.stops.get(stop_id, stop_id)
        return f"{name} [{stop_id}]"

    def _load(self, zip_payload: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(zip_payload)) as archive:
            for row in _iter_csv(archive, "routes.txt"):
                route_id = row.get("route_id")
                if not route_id:
                    continue
                short = (row.get("route_short_name") or "").strip()
                long = (row.get("route_long_name") or "").strip()
                label = f"{short} - {long}".strip(" -") or route_id
                self.routes[route_id] = label
                try:
                    self.route_types[route_id] = int((row.get("route_type") or "3").strip())
                except ValueError:
                    self.route_types[route_id] = 3
                self.route_label_to_id[label] = route_id

            for row in _iter_csv(archive, "stops.txt"):
                stop_id = row.get("stop_id")
                stop_name = (row.get("stop_name") or "").strip()
                if not stop_id or not stop_name:
                    continue
                self.stops[stop_id] = stop_name
                self.stop_label_to_id[f"{stop_name} [{stop_id}]"] = stop_id
                try:
                    lat = float(row.get("stop_lat") or "")
                    lon = float(row.get("stop_lon") or "")
                    self.stop_coords[stop_id] = (lat, lon)
                except (TypeError, ValueError):
                    pass

            for row in _iter_csv(archive, "trips.txt"):
                trip_id = row.get("trip_id")
                route_id = row.get("route_id")
                service_id = row.get("service_id")
                if not trip_id or not route_id or not service_id:
                    continue
                self.trips[trip_id] = {
                    "route_id": route_id,
                    "service_id": service_id,
                    "trip_headsign": (row.get("trip_headsign") or "").strip(),
                }
                self.trips_by_route[route_id].append(trip_id)

            for row in _iter_csv(archive, "stop_times.txt"):
                trip_id = row.get("trip_id")
                stop_id = row.get("stop_id")
                if not trip_id or not stop_id:
                    continue
                departure = _hhmmss_to_seconds(row.get("departure_time") or "")
                arrival = _hhmmss_to_seconds(row.get("arrival_time") or "")
                seq = int(row.get("stop_sequence") or 0)
                stop_time = StopTime(
                    stop_id=stop_id,
                    stop_sequence=seq,
                    departure_secs=departure,
                    arrival_secs=arrival,
                )
                self.stop_times_by_trip[trip_id].append(stop_time)
                self.departures_by_stop[stop_id].append((trip_id, departure))

            for trip_id, items in self.stop_times_by_trip.items():
                items.sort(key=lambda item: item.stop_sequence)
                self.stop_times_by_trip[trip_id] = items

            for stop_id, items in self.departures_by_stop.items():
                items.sort(key=lambda item: item[1])
                self.departures_by_stop[stop_id] = items

            for row in _iter_csv(archive, "calendar.txt"):
                service_id = row.get("service_id")
                if service_id:
                    self.calendar[service_id] = row

            for row in _iter_csv(archive, "calendar_dates.txt"):
                service_id = row.get("service_id")
                date_key = row.get("date")
                exc_type = row.get("exception_type")
                if service_id and date_key and exc_type:
                    self.calendar_dates[date_key].append((service_id, exc_type))

        _LOGGER.debug(
            "GTFS index loaded routes=%d stops=%d trips=%d",
            len(self.routes),
            len(self.stops),
            len(self.trips),
        )


def _iter_csv(archive: zipfile.ZipFile, filename: str):
    members = archive.namelist()
    target = next(
        (name for name in members if name.lower().endswith(f"/{filename.lower()}") or name.lower() == filename.lower()),
        None,
    )
    if not target:
        return []
    with archive.open(target) as handle:
        text = handle.read().decode("utf-8-sig", errors="replace")
    return csv.DictReader(io.StringIO(text))


def _hhmmss_to_seconds(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        return 0
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return 0


def _yyyymmdd_to_date(value: str | None):
    if not value or len(value) != 8:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _route_mode(route_type: int | None) -> str:
    if route_type is None:
        return "other"

    # Standard GTFS + extended TPEG/HVT families used by many EU feeds.
    if route_type == 0 or 900 <= route_type <= 906:
        return "tram"
    if (
        route_type == 3
        or route_type == 11  # trolleybus
        or 700 <= route_type <= 716
        or route_type == 800
    ):
        return "bus"
    return "other"
