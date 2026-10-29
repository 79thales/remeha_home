# Dashboard generation and privacy

## Public defaults

`custom_components/remeha_home/generate_dashboard.py` is an offline, standard-library Python tool. It generates two views together from `dashboard-template.json`: **Remeha Heating** (`remeha-heating`) and its **Details** subview (`remeha-heating-details`). The commented YAML template is an equivalent, human-readable copy.

The public files contain symbolic entity roles and generic labels. They contain no real household entity IDs, room names, building identifiers, meter-channel assignments, local automations, or local control sequences. `dashboard-bindings.example.json` contains deliberately synthetic `*.example_*` IDs; it cannot be installed unchanged.

The overview includes a heating thermostat, temporary temperature controls, status/schedule data, DHW operation/targets/boost, current electricity consumption, heating-curve/activity controls, temperatures, and pressure. Details contains raw Shelly meter data, cloud energy, read-only appliance/holiday status, integration updates, and upstream-commit diagnostics. Missing appliance fields remain unknown. The five numbered activity roles are a generic example; adapt a private template to the activities and devices actually present in your installation.

The temporary-temperature card binds to two per-zone number entities and a start button. Choose a duration preset from 30 minutes to 48 hours, or enter a custom number of minutes, then press Start. Presets set only the local duration draft; merely changing a draft does not command the heater or alter an active override. This feature requires an active schedule and a device reporting support for an override end time. The displayed state and end time come from the cloud; pending and unknown do not mean a command succeeded. Remeha controls the end time, and subsequent manual changes may supersede it. The generator does not create or bind these entities automatically.

## Inputs, output, and side effects

`generate_dashboard(source, bindings, template=None, *, insert_after=None)` accepts:

- `source`: the full Lovelace configuration, or a connector wrapper containing `config`;
- `bindings`: a private JSON object mapping every `@@ROLE@@` in the selected template to an existing entity ID;
- `template`: an optional private JSON template defining one main view and one Details subview;
- `insert_after`: an optional existing view path used when inserting a new main view.

`DASHBOARD_BASE` is the one non-entity binding: a relative dashboard URL such as `/lovelace`. Other values must be syntactically valid `domain.entity_id` strings. Generation checks their syntax, not their existence or suitability; validate them against the actual HA registry before deployment.

The result is a new full Lovelace configuration. Source objects are not modified. Target paths come from the selected template. Existing targets are updated in place; a new main view is appended by default, or inserted after the explicit anchor, and new Details follows main. Unrelated views retain their content and order. Repeated generation is idempotent.

Missing bindings, malformed paths, ambiguous duplicate targets, and a missing explicit anchor raise an English `ValueError` before a result is returned. Generation never connects to Home Assistant, calls services, creates helpers, changes history, or adjusts heating. Controls in the resulting dashboard write only when the user operates them.

## Generate a local proposal

Copy the example bindings to a private directory outside the public repository and integration package. Replace every synthetic ID with the actual entity for that role. Export and back up the current Lovelace configuration before generating a proposal.

```sh
python custom_components/remeha_home/generate_dashboard.py --input dashboard-before.json --bindings dashboard-private/bindings-private.json --output dashboard-private/proposed-private.json
```

Review the proposal and validate actual entity availability, units, statistics coverage, navigation, and preservation of unrelated views before writing through Home Assistant's supported API or editor. The CLI saves a local JSON file only; it does not deploy it or restart HA.

### Private compositions and existing dashboards

Keep household-specific titles, paths, room controls, custom conversions, and local automation cards in a private JSON template. Pass it explicitly to retain an existing main/detail composition:

```sh
python custom_components/remeha_home/generate_dashboard.py --input dashboard-before.json --bindings dashboard-private/bindings-private.json --template dashboard-private/template-private.json --output dashboard-private/proposed-private.json
```

Use `--insert-after existing-view` only when you want to place a newly generated main view after that existing path. The generator contains no built-in household path or insertion anchor. A private template can retain its existing paths, labels, cards, and navigation without altering public defaults.

The repository ignores `dashboard-private/`, `private/`, and `*-private.json/yaml/yml`. Keep backups outside Git as well. Do not place private profiles inside `custom_components/remeha_home`; release packaging excludes recognized private-profile paths as an additional safeguard.

## Entity roles and units

| Roles | Purpose / expected units |
|---|---|
| `CLIMATE`, `CLIMATE_STATUS`, `THERMAL` | Main thermostat and reported operating states. |
| `ROOM`, `OUTDOOR`, `DHW_TEMP`, `PRESSURE` | Temperature in °C and heating-water pressure in bar. |
| `DHW`, `COMFORT`, `ECO`, `BOOST`, `DHW_MODE`, `DHW_STATUS`, `DHW_NEXT` | DHW controls, targets in °C, status, and next schedule timestamp. |
| `SCHEDULE_TEMP`, `NEXT_TEMP`, `NEXT_TIME`, `FIREPLACE` | Existing schedule temperatures/timestamp and fireplace mode. |
| `SLOPE`, `BASE`, `ACTIVITY_1` … `ACTIVITY_5` | Dimensionless curve slope; curve base and activity temperatures in °C. |
| `POWER_HP`, `POWER_BACKUP` | Heat-pump and electric-backup active power in W. |
| `ENERGY_HP`, `ENERGY_BACKUP` | Import counters with valid Recorder energy statistics in kWh. |
| `ENERGY_HP_RETURN`, `ENERGY_BACKUP_RETURN` | Reverse-direction counters in kWh, shown in Details only. |
| `METER_HP_TEMP`, `METER_BACKUP_TEMP` | Meter diagnostic temperatures in °C. |
| `PUMP_*`, `BACKUP_*` | Remeha per-source energy in kWh and reported seasonal efficiency. |
| `UPDATE` | Existing update entity for this fork. |
| `TEMPORARY_TEMPERATURE`, `TEMPORARY_DURATION`, `TEMPORARY_START` | Zone-specific temperature number, duration number (minutes), and start button; bind all three to the same zone. |
| `UPSTREAM_ORIGINAL`, `UPSTREAM_COMMUNITY`, `UPSTREAM_CHECK` | Two diagnostic commit-count sensors and the manual check button. |

### Current power

Current total power is `max(heat_pump_W, 0) + max(backup_W, 0)`. Negative readings are ignored for display only, not converted to absolute values. Missing, nonnumeric, or more-than-180-second-old readings make the corresponding value unavailable; they are not zeros. Freshness uses `last_reported` so a continuously reported constant value is not mistaken for a failed meter.

The Markdown template updates when tracked readings change and, through `now()`, at least every minute. Original measurements and stored history retain their signs and values. The explicit negative-value policy is a presentation choice; verify meter wiring separately before drawing operational conclusions from it.

### Calendar energy

Statistic cards use `stat_type: change` on import counters for the current calendar week, month, and year in HA's timezone. These are not rolling 7/30/365-day windows. No new utility meters are created, no reverse-direction energy is added, and missing history is not extrapolated.

Cloud energy stays in Details because reporting can be delayed or incomplete. Unknown cloud values remain unknown; they are not treated as zero consumption or a live measurement of power.

## Maintaining the templates

Update the generator and both template formats together. Tests check JSON/YAML equivalence, symbolic-only public entities, synthetic examples, private-template support, navigation, invalid inputs, preservation, and idempotence. Lovelace storage uses JSON and drops YAML comments; Jinja comments remain part of stored card text.

This English public template does not rename or translate an existing live dashboard. To preserve a local composition, continue using its private template and bindings. Published historical Git objects and earlier release archives are separate from the current public template; this change does not rewrite repository history.

The appliance status roles are `APPLIANCE_ONLINE`, `PRESSURE_PROBLEM`, `OPERATING_MODE`, `ERROR_STATUS`, `PREHEAT`, and `HOLIDAY_STATUS`. The holiday entity is read-only; no automatic restoration or holiday control is added.
