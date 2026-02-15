# Zagreb Transit (Home Assistant)

Custom ZET GTFS integration with dynamic watch entities.

## Install (HACS)

1. HACS -> Integrations -> Custom repositories.
2. Add this repo URL as `Integration`.
3. Install `Zagreb Transit`.
4. Restart Home Assistant.
5. Add integration from `Settings -> Devices & Services`.

## Core options

- `update_interval`
- `realtime_interval`
- `static_refresh_hours`
- `default_window_minutes`
- `notifications_enabled` (persistent degraded-status notifications)

## Core entities

- `sensor.zagreb_transport_feed_version_active`
- `sensor.zagreb_transport_feed_valid_from`
- `sensor.zagreb_transport_feed_valid_to`
- `sensor.zagreb_transport_feed_source`
- `sensor.zagreb_transport_realtime_status`
- `sensor.zagreb_transport_realtime_last_timestamp`
- `sensor.zagreb_transport_next_trip_od_do`
- `sensor.zagreb_transport_station_direction_board`
- `sensor.zagreb_transport_nearby_board`
- `sensor.zagreb_transport_watch_registry`
- dynamic: `sensor.zagreb_transport_watch_<name_slug>`

## Dynamic watch services

- `zagreb_transit.add_watch`
- `zagreb_transit.update_watch`
- `zagreb_transit.remove_watch`
- `zagreb_transit.duplicate_watch`

## Add watch example

```yaml
service: zagreb_transit.add_watch
data:
  name: Utrina do Autobusnog
  watch_type: od
  enabled: true
  config:
    vehicle_type: tram
    from_query: Utrina
    to_query: Autobusni
    window_minutes: 30
    limit: 20
```

## Dashboard files

- HR:
  - `Dashboards/zagreb_transit_demo.yaml`
  - `Dashboards/zagreb_transit_full.yaml`
- EN:
  - `Dashboards/zagreb_transit_demo_en.yaml`
  - `Dashboards/zagreb_transit_full_en.yaml`

## Notes

- Route watch is internally `od` type for backward compatibility.
- If setup fails, confirm log lines are from `custom_components.zagreb_transit`.
