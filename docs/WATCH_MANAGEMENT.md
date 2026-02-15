# Watch Management

Watch entities are dynamic sensors created by the integration. They allow persistent, named monitoring scenarios.

## Watch lifecycle

1. Add watch
   - `Settings -> Devices & Services -> Zagreb Transit -> Configure`
   - Choose `Add watch`
2. Edit watch
   - Same flow, choose `Edit watch`
3. Remove watch
   - Same flow, choose `Remove watch`

Note:
- UI labels are localized (EN/HR).
- Internally, watch type `route` is stored as `od` for compatibility.

## Watch types

- `route` (`od` internally)
  - start -> destination trips with planned/RT departure and arrival
- `departure`
  - departures from one stop (optionally filtered)
- `station_query`
  - grouped station search (multiple matching stations)
- `nearby`
  - departures near person/zone/fixed coordinates

## Naming

- Give each watch a clear `name`.
- Entity is generated as `sensor.zagreb_transport_watch_<name_slug>`.

## Recommended setup order

1. Start with `station_query` to confirm matching stops.
2. Create a `route` (`od`) watch for your common route.
3. Add `nearby` watch for mobility scenarios.
4. Use `departure` for single-stop quick boards.

## Registry and debugging

- `sensor.zagreb_transport_watch_registry` lists all watch IDs and entity mapping.
- Each watch sensor has `config`, `departures`, and state/error context.

## Practical tip

If a watch shows no results, increase `window_minutes` first, then relax route/direction filters.

## Related docs

- Usage without watches: `USAGE_NO_WATCHES.md`
- Dashboards and debugging: `DASHBOARDS_GUIDE.md`
- Docs index: `README.md`
