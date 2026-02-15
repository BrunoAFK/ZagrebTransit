"""Constants for Zagreb Transit integration."""

from __future__ import annotations

DOMAIN = "zagreb_transit"
PLATFORMS = ["sensor", "select", "number"]

NAME = "Zagreb Transit"
VERSION = "0.2.0"

STATIC_GTFS_URL = "https://www.zet.hr/gtfs-scheduled/latest"
REALTIME_GTFS_URL = "https://www.zet.hr/gtfs-rt-protobuf"
GTFS_LISTING_URL = "https://www.zet.hr/gtfs2"
GTFS_PORTAL_URL = "https://www.zet.hr/odredbe/datoteke-u-gtfs-formatu/669"

CONF_UPDATE_INTERVAL = "update_interval"
CONF_REALTIME_INTERVAL = "realtime_interval"
CONF_STATIC_REFRESH_HOURS = "static_refresh_hours"
CONF_DEFAULT_WINDOW_MINUTES = "default_window_minutes"
CONF_NOTIFICATIONS_ENABLED = "notifications_enabled"

DEFAULT_UPDATE_INTERVAL = 60
DEFAULT_REALTIME_INTERVAL = 60
DEFAULT_STATIC_REFRESH_HOURS = 6
DEFAULT_WINDOW_MINUTES = 30
DEFAULT_NEARBY_RADIUS_METERS = 50
DEFAULT_NOTIFICATIONS_ENABLED = False

MAX_LISTING_CANDIDATES_TO_TRY = 5
MAX_PREVIOUS_VERSION_TRIES = 5
MAX_CACHED_FEEDS = 8
REALTIME_DELAY_MAX_STALE_SECONDS = 300

MIN_WINDOW_MINUTES = 5
MAX_WINDOW_MINUTES = 180
MIN_NEARBY_RADIUS_METERS = 20
MAX_NEARBY_RADIUS_METERS = 500
MIN_WATCH_MAX_STOPS = 2
MAX_WATCH_MAX_STOPS = 40
MIN_WATCH_LIMIT = 1
MAX_WATCH_LIMIT = 40

ICON_TRANSIT = "mdi:bus-clock"
ICON_STATUS = "mdi:information-outline"

SERVICE_REFRESH_STATIC = "refresh_static"
SERVICE_REFRESH_REALTIME = "refresh_realtime"
SERVICE_REBUILD_INDEXES = "rebuild_indexes"
SERVICE_VALIDATE_ACTIVE_FEED = "validate_active_feed"
SERVICE_FORCE_SELECT_FEED = "force_select_feed"
SERVICE_ADD_WATCH = "add_watch"
SERVICE_UPDATE_WATCH = "update_watch"
SERVICE_REMOVE_WATCH = "remove_watch"
SERVICE_DUPLICATE_WATCH = "duplicate_watch"

ATTR_FEED_VERSION = "feed_version"
ATTR_FEED_VALID_FROM = "feed_valid_from"
ATTR_FEED_VALID_TO = "feed_valid_to"
ATTR_FEED_SOURCE = "feed_source"
ATTR_REALTIME_STATUS = "realtime_status"
ATTR_REALTIME_LAST_TIMESTAMP = "realtime_last_timestamp"

WATCH_TYPE_DEPARTURE = "departure"
WATCH_TYPE_OD = "od"
WATCH_TYPE_NEARBY = "nearby"
WATCH_TYPE_STATION_QUERY = "station_query"
WATCH_TYPES = (
    WATCH_TYPE_DEPARTURE,
    WATCH_TYPE_OD,
    WATCH_TYPE_NEARBY,
    WATCH_TYPE_STATION_QUERY,
)
WATCH_LOCATION_PERSON = "person"
WATCH_LOCATION_ZONE = "zone"
WATCH_LOCATION_FIXED = "fixed"
WATCH_VEHICLE_TYPES = ("tram", "bus", "All")
MAX_WATCHES = 30
