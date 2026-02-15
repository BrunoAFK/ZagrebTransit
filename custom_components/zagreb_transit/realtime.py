"""Realtime GTFS-RT handling for Zagreb Transit."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from aiohttp import ClientSession

from .const import REALTIME_DELAY_MAX_STALE_SECONDS, REALTIME_GTFS_URL

_LOGGER = logging.getLogger(__name__)

try:
    from google.transit import gtfs_realtime_pb2  # type: ignore
except Exception:  # noqa: BLE001
    gtfs_realtime_pb2 = None


class RealtimeClient:
    """Fetch and parse GTFS-RT trip updates."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self._last_success_utc: datetime | None = None
        self.last_result: dict = {
            "status": "stale",
            "last_timestamp": None,
            "trip_delays": {},
            "error": None,
        }

    async def refresh(self) -> dict:
        """Refresh realtime data and return normalized structure."""
        if gtfs_realtime_pb2 is None:
            self.last_result = {
                "status": "error",
                "last_timestamp": None,
                "trip_delays": {},
                "error": "gtfs_realtime_pb2_unavailable",
            }
            return self.last_result

        try:
            async with self.session.get(REALTIME_GTFS_URL, timeout=30) as response:
                response.raise_for_status()
                payload = await response.read()

            message = gtfs_realtime_pb2.FeedMessage()
            message.ParseFromString(payload)

            trip_delays: dict[str, int] = {}
            last_ts = None

            for entity in message.entity:
                if not entity.HasField("trip_update"):
                    continue
                trip = entity.trip_update.trip
                trip_id = trip.trip_id
                if not trip_id:
                    continue

                delay = 0
                updates = entity.trip_update.stop_time_update
                for update in updates:
                    if update.HasField("departure") and update.departure.HasField("delay"):
                        delay = int(update.departure.delay)
                        break
                    if update.HasField("arrival") and update.arrival.HasField("delay"):
                        delay = int(update.arrival.delay)
                        break

                if entity.trip_update.HasField("timestamp"):
                    ts = int(entity.trip_update.timestamp)
                    last_ts = max(last_ts or ts, ts)

                trip_delays[trip_id] = delay

            last_iso = (
                datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
                if last_ts
                else None
            )
            self.last_result = {
                "status": "ok",
                "last_timestamp": last_iso,
                "trip_delays": trip_delays,
                "error": None,
            }
            self._last_success_utc = datetime.now(timezone.utc)
            return self.last_result

        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Realtime refresh failed: %s", err)
            keep_trip_delays = self.last_result.get("trip_delays", {})
            if self._last_success_utc is None:
                keep_trip_delays = {}
            else:
                stale_for = (datetime.now(timezone.utc) - self._last_success_utc).total_seconds()
                if stale_for > REALTIME_DELAY_MAX_STALE_SECONDS:
                    keep_trip_delays = {}
            self.last_result = {
                "status": "error",
                "last_timestamp": self.last_result.get("last_timestamp"),
                "trip_delays": keep_trip_delays,
                "error": str(err),
            }
            return self.last_result
