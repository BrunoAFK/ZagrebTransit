# Dashboards: Demo, Debug, Testing

The repository includes reusable dashboard YAML examples.

## Available samples

- `docs/dashboards/transit_test.yaml`
  - Step-by-step validation and testing
- `docs/dashboards/zagreb_transit_demo.yaml` (HR)
  - Demo layout with practical outputs (Croatian)
- `docs/dashboards/zagreb_transit_demo_en.yaml` (EN)
  - Demo layout with practical outputs (English)
- `docs/dashboards/zagreb_transit_full.yaml` (HR)
  - Extended all-in-one dashboard (Croatian)
- `docs/dashboards/zagreb_transit_full_en.yaml` (EN)
  - Extended all-in-one dashboard (English)

## How to use

1. Create/open a dashboard in Home Assistant.
2. Open Raw configuration editor.
3. Copy sections/cards from sample YAML.
4. Replace any sample entities with your own where needed.

## Debug strategy

Use debug cards for:

- feed validity and source
- realtime timestamp freshness
- watch registry mapping
- raw watch attributes (`departures`, `stops`, `grouped`)
- nearby attributes truncation indicators:
  - `stops_total`
  - `departures_total`
  - `attributes_truncated`

## Recorder note

`sensor.zagreb_transport_nearby_board` is optimized to avoid oversized attributes for Recorder DB limits.

## Design goal

Dashboards are intentionally structured to be intuitive and general-purpose. They are templates, not personal/private presets.

## Related docs

- Import helper: `docs/IMPORT_DASHBOARDS.md`
- Docs index: `docs/README.md`
- Repository README: `README.md`
