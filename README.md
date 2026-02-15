# Zagreb Transit (HACS Repository)

Standalone HACS-compatible repository for Home Assistant custom integration `zagreb_transit`.

## Repository Layout

- `custom_components/zagreb_transit/` - integration source code
- `hacs.json` - HACS metadata
- `icon.svg` - placeholder repository icon
- `docs/` - usage guides and dashboard docs

## Installation

### Option A: HACS (Custom Repository)

1. Publish this folder as its own GitHub repository.
2. In Home Assistant open `HACS -> Integrations -> ⋮ -> Custom repositories`.
3. Add repository URL and choose category `Integration`.
4. Find and install `Zagreb Transit`.
5. Restart Home Assistant.

### Option B: Manual Installation

1. Copy `custom_components/zagreb_transit` to your HA config folder:
   - target: `<HA_CONFIG>/custom_components/zagreb_transit`
2. Restart Home Assistant.
3. Go to `Settings -> Devices & Services -> Add Integration`.
4. Search for `Zagreb Transit` and finish setup.

## Quick Start (5 minutes)

1. Install integration (HACS custom repo or manual).
2. Restart Home Assistant.
3. Open `Settings -> Devices & Services -> Add Integration`.
4. Add `Zagreb Transit`.
5. Wait 30-60 seconds for initial data load.
6. Check core health entities:
   - `sensor.zagreb_transport_feed_version_active`
   - `sensor.zagreb_transport_feed_source`
   - `sensor.zagreb_transport_realtime_status`
   - `sensor.zagreb_transport_watch_registry`
7. Open integration `Configure` and add your first watch:
   - type: `station_query`
   - station query: your stop name
   - vehicle: `All`
   - window: `30`
8. Confirm generated entity exists:
   - `sensor.zagreb_transport_watch_<your_name_slug>`
9. Import a sample dashboard from:
   - `docs/dashboards/transit_test.yaml`
10. If no departures appear, increase `window_minutes` and relax route/direction filters.

## What's New

- Route filtering fixes now use realtime departure (`dep_rt`) where relevant.
- Nearby and OD outputs include safer deduplication logic.
- Realtime delay data is auto-cleared after longer outage periods (stale protection).
- Static feed cache avoids unnecessary re-download/write when version is already cached.
- Old cached feed files are pruned automatically.
- Optional persistent degraded-status notifications are available in Core settings.
- Watch naming/renaming behavior is more stable for dynamic entities.

## Add To Home Assistant

After installation:

1. Open `Settings -> Devices & Services`.
2. Click `Add Integration`.
3. Choose `Zagreb Transit`.
4. Wait for initial GTFS static + realtime sync.
5. Verify core entities are available (feed, realtime, boards, registry).

## Core Concepts

- Integration domain: `zagreb_transit`
- Entity naming: `zagreb_transport_*`
- Core entities are available immediately after setup.
- Dynamic watch entities are optional and can be added later via integration options.
- Core option `notifications_enabled` controls persistent warning notifications.

## Documentation Index

- Use integration without adding watches:
  - `docs/USAGE_NO_WATCHES.md`
- Watch management (add, edit, remove, types, practical usage):
  - `docs/WATCH_MANAGEMENT.md`
- Dashboards (demo, debug, testing) and how to use them:
  - `docs/DASHBOARDS_GUIDE.md`
- Dashboard import helper:
  - `docs/IMPORT_DASHBOARDS.md`
- Docs index:
  - `docs/README.md`
- Dashboard YAML samples:
  - `docs/dashboards/transit_test.yaml`
  - HR:
    - `docs/dashboards/zagreb_transit_demo.yaml`
    - `docs/dashboards/zagreb_transit_full.yaml`
  - EN:
    - `docs/dashboards/zagreb_transit_demo_en.yaml`
    - `docs/dashboards/zagreb_transit_full_en.yaml`

## Privacy Note

Dashboard samples are intended to be generic and reusable. They are designed as templates and should be customized with your own entities/locations as needed.

## Troubleshooting

- If integration setup fails, first check logs for entries referencing `custom_components.zagreb_transit`.
- Errors mentioning other integrations (for example `component.spook...`) are unrelated to this integration.
- If text/labels look outdated after upgrade, restart Home Assistant and hard-refresh browser cache.
