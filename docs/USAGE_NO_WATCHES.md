# Usage Without Watches

This mode uses only built-in core entities and does not require creating dynamic watch sensors.

## What you get immediately

- Active feed/version metadata
- Feed validity range
- Feed source strategy
- Realtime status and last timestamp
- Manual OD and board entities controlled from integration helpers/selects
- Optional persistent warning notifications (toggle in Core settings)

## Typical flow

1. Install and set up integration.
2. Open entity states for:
   - `sensor.zagreb_transport_feed_version_active`
   - `sensor.zagreb_transport_feed_valid_from`
   - `sensor.zagreb_transport_feed_valid_to`
   - `sensor.zagreb_transport_feed_source`
   - `sensor.zagreb_transport_realtime_status`
3. Use core controls (selects/numbers) to query route, direction, stops, window.
4. Read outputs from:
   - `sensor.zagreb_transport_next_trip_od_do`
   - `sensor.zagreb_transport_station_direction_board`
   - `sensor.zagreb_transport_nearby_board`
5. Optional: enable/disable warnings in Core settings:
   - `notifications_enabled`

## When to use this mode

- You want quick usage without managing many entities.
- You are still validating feed/realtime behavior.
- You prefer manual on-demand checks over persistent watch sensors.

## Related docs

- Watch lifecycle: `WATCH_MANAGEMENT.md`
- Dashboards: `DASHBOARDS_GUIDE.md`
- Docs index: `README.md`
