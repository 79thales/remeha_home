# Technical design

## Setup and identity

`__init__.py` builds the OAuth API, passes the config entry to the main coordinator, and waits for its first successful refresh. It then loads binary_sensor, button, climate, number, sensor, switch, and water_heater. Authentication failure requests reauthentication; core-data failure uses the normal HA retry behavior.

The coordinator registers each parent appliance with its original API identifier and config-entry owner. It passes the returned internal device ID to zones and energy producers as `via_device_id`. Existing IDs survive refreshes, restarts, and migration from upstream. Identical cloud identifiers in different accounts are separated by config-entry ownership.

The integration preserves the domain, original unique IDs, `heat=Manual`, `auto=Scheduling`, and schedule presets. Setup does not rename or remove entities and does not modify user automations.

## Data, time, and failures

Core data refreshes every 60 seconds; energy and optional endpoints refresh every 15 minutes. API requests have a 30-second timeout, with an additional 10-second bound on optional curve/activity reads. Failed optional reads also wait until the next interval. Successful curve/activity writes invalidate their cache and request a fresh read.

HTTP 401 requests reauthentication. Optional network failures make the corresponding controls unavailable without disabling the thermostat. A coordinator listener adds controls when support becomes known later; stable unique IDs prevent duplicates, and unload disconnects listeners.

Energy request timestamps and day tracking use UTC. Cloud daily totals are not an independent local-calendar meter. Timestamp sensors preserve API offsets, interpret naive times in HA's configured timezone, and convert to UTC. Invalid times and the year-one sentinel mean unknown.

Energy is in kWh; gas volume is in m³. Conversion requires a real positive calorific value in kWh/m³ from the appliance API. Empty responses, nulls, invalid numbers, outages, and missing producers are not replaced with zero. A real reported zero is retained. Cloud totals are not live power measurements.

## Commands, priority, and concurrency

The integration adds no autonomous heating-control priority. Commands originate from explicit HA services, controls, or existing user automations. Setup, restart, entity addition, and refresh issue no heating commands. Pending commands are not replayed after restart.

New DHW, boost, and number writes share an `asyncio.Lock` per zone. Completion is serialized; errors and cancellation release the lock. Writes are not automatically retried because a timeout does not establish whether the cloud executed a request. Subsequent reads confirm the actual state. This lock does not coordinate with the official app or another HA instance.

Numeric inputs must be finite and within API-confirmed bounds. Slope is dimensionless; temperatures are in °C. Steps come from the API, with 0.5 °C UI resolution for DHW. Device-model guesses do not establish physical limits. Last confirmed bounds may remain visible during an outage, but writes require valid current data.

The heating-curve POST requires slope and base together. Each write fetches and validates both, changing only the selected parameter. The cloud has no conditional-write mechanism, so an external change between GET and POST cannot be excluded. Activities similarly fetch the complete list and modify a Heating entry without confusing it with a Cooling entry of the same number.

Boost requires an explicitly confirmed `capabilityBoostMode`. Starting requires Scheduling; stopping returns to Scheduling. Unknown DHW types are not assumed to be Tank. New controls confirm state after successful writes. Failed writes must not leave a false target in the UI. The inherited climate entity restores its previous optimistic state on error and later polling confirms reality.

## Independent upstream checks

`upstream.py` uses a separate read-only coordinator for two fixed public GitHub repositories and the exact SHA revisions incorporated into this fork. `button.py` requests a refresh; two diagnostic sensor instances expose counts and metadata. Initial checking runs as a config-entry background task after platform setup and cannot block thermostat setup.

Automatic checks run every 12 hours, with a five-minute shared manual cache. Sources run concurrently with a 15-second timeout each. Each check fetches the branch SHA and compares the incorporated baseline with that immutable SHA. Counts use `ahead_by`, not the number of commits on the first response page.

Requests contain only public repository names, branch names, and SHA values. They use neither Remeha nor HACS credentials. HTTP/network failures, malformed responses, and rewritten/rolled-back history make the affected count unavailable. Last success is a separate attribute. Rate-limit headers defer retries; redirects are rejected. Config-entry unload/restart manages background-task cancellation and removes scheduled listeners.

The baseline advances only when a maintainer reviews and incorporates upstream changes. Monitoring does not merge, install, or issue heating commands. It does not change the main Remeha polling interval or write priorities.

## Diagnostics and privacy

Diagnostics allowlist technical types, modes, and capability flags. They do not export OAuth configuration, tokens, household names, zone identifiers, serial numbers, addresses, or raw cloud responses. New fields must preserve that allowlist.

Production code lives under `custom_components/remeha_home`. Tests use synthetic data. Household backups, history, registry exports, and dashboard profiles belong outside the public repository and release package.

The [dashboard generator](DASHBOARD.md) contains no household paths, room layout, or local control logic. Its English public JSON/YAML templates use symbolic entity roles and synthetic example bindings. Private templates can preserve existing main/detail compositions without publishing them. Generation is offline; publishing a new template does not edit a running dashboard.

## Tests and releases

Test plugin 0.13.372 uses Home Assistant 2026.10.1 and Python 3.14. Tests cover registry identity, restart-style reloads, account isolation, missing optional data, the existing heat contract, write errors, DHW/boost, curves, activities, concurrency, diagnostics, all seven platforms, upstream HTTP failures/rate limits, and dashboard generation/privacy.

Separate workflows run HACS/hassfest, Ruff, and tests. The installer ZIP includes Python, JSON, and YAML integration sources, not tests or household profiles. Its manifest version must match the release tag. Publishing a release does not install it or restart a user's HA instance. See [packaging](../scripts/README.md).

HACS validation targets a custom repository. Its `license` check is explicitly skipped because inherited upstream has no LICENSE and this fork does not invent a license for that code. Manifest, HACS metadata, and other checks still run.
