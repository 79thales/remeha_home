# Changelog

## 0.3.1 — 2026-10-18

- Replace household-specific public dashboard defaults with a generic English overview and Details subview.
- Keep all concrete entity mappings in private files; ship synthetic example bindings only.
- Add private-template and explicit insertion-anchor CLI options, preserving existing custom compositions.
- Add privacy, preservation, CLI, template-equivalence, and installer-exclusion regression checks.
- Translate public documentation and release notes into English.
- Match the maintainer's standard README badge order, download counters, HACS links, and centered logo.
- Exclude recognized private profiles and non-example bindings from release archives.
- Keep heating runtime behavior and existing live dashboards unchanged.

## 0.3.0 — 2026-10-17

- Add independent commit monitoring for the original integration and the community fork.
- Expose two diagnostic counts, exact comparison links, check timestamps, and a manual check button.
- Run automatic checks every 12 hours with a five-minute manual cache, timeouts, and rate-limit backoff.
- Keep GitHub failures independent of thermostat availability; failed checks are unavailable, not zero.
- Add upstream diagnostics to dashboard Details and both bundled template formats.

## 0.2.1 — 2026-10-17

- Add an offline heating-dashboard generator and JSON/YAML main/detail templates.
- Include DHW, boost, heating-curve, activity, and source-energy controls and information.
- Put raw Shelly measurements and diagnostics in Details.
- Display current nonnegative power and calendar week/month/year import energy.
- Preserve unrelated views during repeated generation.

## 0.2.0 — 2026-10-17

- Maintain a fork incorporating community DHW, boost, heating-curve, and activity features.
- Register zones and energy producers using `via_device_id`.
- Preserve original identities, the heat mode contract, and schedule presets.
- Require confirmed API bounds and keep heating/cooling activities separate.
- Confirm DHW state after successful commands and serialize new writes per zone.
- Harden optional reads, timeouts, missing data, and sanitized diagnostics.
- Add regression tests against Home Assistant 2026.10.1 and release automation.
