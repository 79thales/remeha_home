# Remeha Home for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8.0%2B-41BDF5?logo=home-assistant&logoColor=white&style=flat)](https://www.home-assistant.io/)
[![HACS Integration](https://img.shields.io/badge/HACS-Integration-41BDF5?logo=home-assistant-community-store&logoColor=white&style=flat)](https://my.home-assistant.io/redirect/hacs_repository/?owner=79thales&repository=remeha_home&category=integration)
[![Latest release](https://img.shields.io/github/v/release/79thales/remeha_home?label=Release&logo=github&style=flat)](https://github.com/79thales/remeha_home/releases/latest)
[![Installer downloads, all releases](https://img.shields.io/github/downloads/79thales/remeha_home/remeha_home.zip?label=Downloads%20total&displayAssetName=false&logo=github&style=flat)](https://github.com/79thales/remeha_home/releases)
[![Installer downloads, latest release](https://img.shields.io/github/downloads/79thales/remeha_home/latest/remeha_home.zip?label=Downloads%20latest&displayAssetName=false&logo=github&style=flat)](https://github.com/79thales/remeha_home/releases/latest)
[![Validation](https://img.shields.io/github/check-suites/79thales/remeha_home/main?label=Validation&logo=github&style=flat)](https://github.com/79thales/remeha_home/actions)

<p align="center">
  <img src="https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/remeha_home/logo.png" alt="Remeha Home" width="180">
</p>

HACS displays documentation for the installed version. The unified header and generic English public templates are included from v0.3.1; updating only the default branch does not change an older installed release.

## English overview

Remeha Home is an independently maintained custom integration for Home Assistant **2026.8 or newer**. This fork preserves the `remeha_home` domain, existing device and entity identities, the `heat`, `auto`, and `off` modes, and the `clock_program_1` to `clock_program_3` presets. Your thermostat must already work in the official Remeha Home app.

### Features

- Heating thermostat, operating status, room/outdoor temperature, water pressure, and schedules.
- Domestic hot water operation, Comfort/Eco targets, and boost when the device confirms support.
- Heating-curve slope and base temperature with bounds and increments reported by the API.
- Heating activity temperatures, kept separate from cooling activities.
- Per-source energy and seasonal efficiency when reported; missing values remain unknown.
- Read-only appliance connectivity, pressure-problem, operating/error, pre-heat and holiday status when reported. Missing fields remain unknown.
- Modern parent-device registration through `via_device_id`.
- Serialized writes per zone, input validation, optional-endpoint resilience, and sanitized diagnostics.
- Independent monitoring of new commits in the original integration and the community fork, with a manual check button and links to GitHub comparisons.
- An offline **dashboard generator**, generic English JSON/YAML templates, synthetic example bindings, a main heating view, and a linked **Details** subview for raw meter data and diagnostics. See [dashboard generation and privacy](documentation/DASHBOARD.md).

Installing, loading, refreshing, and restarting the integration do not issue heating commands. Settings change only when a control or service is used. Feature availability is based on the device response, not an assumed capability of a heat-pump model.

## Installation through HACS

1. Add `https://github.com/79thales/remeha_home` under **HACS → Custom repositories**, category **Integration**.
2. Install this fork and restart Home Assistant to load its Python code.
3. For a new setup, open **Settings → Devices & services → Add integration**, choose **Remeha Home**, and sign in using your official app credentials.

[Open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=79thales&repository=remeha_home&category=integration) · [Start configuration](https://my.home-assistant.io/redirect/config_flow_start/?domain=remeha_home) · [Latest release](https://github.com/79thales/remeha_home/releases/latest)

When migrating from the original integration, keep its existing configuration entry, devices, and entities. The original and this fork share one domain and must not be installed side by side. Back up first and ensure HACS tracks this repository. Installed files under `custom_components/remeha_home` must come from one release.

### Installer downloads

The badges follow the same order and flat style as the maintainer's other integrations. The Home Assistant badge states the minimum supported version. **Validation** summarizes the default branch's GitHub check suites, including HACS, hassfest, code checks, and tests.

HACS installs the `remeha_home.zip` release asset. For manual installation, extract its contents directly into `custom_components/remeha_home`, with `manifest.json` at that directory's root. GitHub's automatic **Source code (zip)** archive has a different layout.

Download badges count installer downloads, including updates and repeated manual downloads. They do not count unique users or active installations, source-code archives, or default-branch installations. GitHub counts may be cached; the integration adds no usage telemetry. See [release packaging](scripts/README.md).

## Controls and data limitations

Boost can start from Schedule mode; turning boost off returns DHW to Schedule. Its duration is determined by the device. Eco temperature control requires confirmed `dhwType=Tank` support. An ambiguous Comfort/Eco schedule target blocks the general target-temperature write; use the explicit supported Comfort or Eco number control instead.

The heating curve is not adjusted automatically. Slope is dimensionless and the base temperature is in °C. Each change reads and preserves the other curve parameter. API failure, unknown bounds, or out-of-range values prevent the write.

Core Remeha data refreshes every 60 seconds; energy, curves, and activities refresh every 15 minutes. Successful writes request a refresh. Cloud energy can be delayed and is not an independent live-power measurement. Missing data is not estimated or replaced with zero. See the [technical design](documentation/FORK.md) and inherited [API notes](documentation/api.md).

## Upstream commit monitoring

Two diagnostic sensors track `msvisser/remeha_home` and `hugo-leij/remeha_home`, branch `main`. Counts are relative to the exact upstream revisions incorporated into this release, not the time of the last check. New commits in this fork are excluded.

The first check runs in the background after platform setup, then every 12 hours. The manual button shares a five-minute cache and respects GitHub rate limits. Each source has a 15-second timeout. Requests use public GitHub metadata without Remeha/HACS tokens or household data.

A confirmed zero means no new commits. Network/HTTP errors, malformed responses, and rewritten history make only the affected sensor unavailable. Attributes expose the check time, last success, error code, retry time where applicable, and comparison links. Monitoring does not merge or install code and does not block the thermostat. Maintainers advance a reference SHA only after reviewing and incorporating the corresponding changes.

## Development and verification

Tests use Home Assistant 2026.10.1, Python 3.14, and Linux. Cloud HTTP is mocked; Home Assistant registries and platforms are real.

```sh
python -m pip install -r requirements_test.txt
pytest -q
ruff check custom_components tests scripts/build_release.py
ruff format --check custom_components tests scripts/build_release.py
```

Passing tests do not establish support for every device or confirm execution of a real cloud command. Keep household bindings, private templates, backups, and registry exports outside the public repository and release package.

## Credits

The integration originates from [msvisser/remeha_home](https://github.com/msvisser/remeha_home). Community features and their author history were incorporated from [hugo-leij/remeha_home](https://github.com/hugo-leij/remeha_home), revision `20bf0ffa82055c1b341ae538529af4473fbbb5bf`. This fork maintains compatibility fixes, validation, diagnostics, tests, and releases. The logo is served from [Home Assistant Brands](https://github.com/home-assistant/brands/tree/master/custom_integrations/remeha_home).
