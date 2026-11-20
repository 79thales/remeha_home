# Changelog

## v0.5.13 — Expose the appliance mode as zone_mode

- Use `zone_mode` for the reported climate zone mode, matching the contract agreed in Hugo's issue #53. Update any templates that referenced `remeha_zone_mode` to the new name.
- The timed override confirmation and pending command attributes remain available, including the record that survives a restart.

## v0.5.12 — Preserve zone choices during holiday recovery

- Align holiday recovery decisions with Hugo's PR #52. Off, Frost protection, Eco and cancelling Boost during a holiday keep the original schedule eligible for recovery. Other successful mode commands take priority. Failed commands keep the previous recovery decision.
- Keep recovery decisions across Home Assistant restarts and serialize a queued recovery with an in-flight command to the same zone. Existing v0.5.11 holiday records can be loaded. For rollback during an active holiday, restore the pre-update holiday journal along with the previous integration.

## v0.5.11 — Native manual climate mode and dashboard controls

- Manual mode follows the zone's cooling capability as in Hugo's integration. Heating-only zones expose `heat` and cooling-capable zones expose `heat_cool`. Both map to the appliance's Manual mode. Automations that specify an HVAC mode should select the one offered by the target zone.
- Generated thermostats display the zone's supported modes, including when regenerated from older private templates. The timed-temperature card follows the dashboard theme. Selecting a duration edits the draft before the user starts the override.
- This stable release includes the phase-one changes from v0.5.3–v0.5.10. When upgrading from v0.4.0, check dashboard and automation references to retired override entities and use `remeha_home.set_temporary_override` for timed control. Site-specific automations are managed separately.

## v0.5.10 — Preserve temporary override confirmation across restart

- Persist the pending command for each zone before sending a timed temperature override.
- Clear the record after a matching device report or a fresh report of a known non-override mode after expiry. Loading the record never sends a heating command.

## v0.5.9 — Use native Remeha dashboard bindings

- Use the shared timed override service for all dashboard temperature and duration presets. Retire the duplicate override entities and integration-specific GitHub diagnostics.
- Match the native Water Pressure binary sensor name. Keep existing stable registry unique IDs for common entities.

## v0.5.8 — Preserve holiday recovery through HA restarts

- Store the original schedule and the device-confirmed holiday state before restoring heating and hot water. Explicit Home Assistant changes remain excluded from restoration after a restart.

## v0.5.7 — Restore the heating schedule after a holiday

- Restore the original heating clock program if an observed holiday leaves the zone in frost protection. Explicit HA overrides take precedence.

## v0.5.6 — Restore hot water after a holiday

- Restore scheduled DHW after a confirmed holiday left the zone off. Explicit HA changes take precedence. The pending recovery is held in memory and cleared on reload.

## v0.5.5 — Clock schedules and appliance holidays

- Heating and DHW clock sensors expose the active weekly schedule. Holiday services schedule or cancel whole days on the selected appliance.
- The bundled dashboard shows both weekly programs and a date form with explicit confirmation. Holiday state comes from the appliance.

## v0.5.4 — Phase 1 entity registry alignment with Hugo v0.10.2

- Migrate the holiday sensor in place, retain existing dashboard and automation references, and recognize zone-level preheat and DHW optimization when reported.
- Use persistent producer identities compatible with Hugo, including hybrid installations that share an instance number. Add the override end, next DHW mode and reported maximum flow temperature.
- The existing heating and PV control logic remains in the fork during phase one.

## 0.5.3 — Preparation for merging with Hugo's fork

This prerelease aligns shared interfaces with `hugo-leij/remeha_home` as a first step toward combining the forks. It keeps the existing installation and its entity identities in place while compatibility is verified.

- Add `remeha_home.set_temporary_override` with an explicit duration, using the existing zone lock, capability checks, and pending-command tracking. Expose the reported end in the `override_end_time` attribute.
- Add the read-only `remeha_home.get_energy_history` action for closed calendar days. Reports identify missing days and keep heat-pump and gas energy separate.
- Expose the active heating and hot-water clock programs and include timed-override capability in diagnostics.
- Include the heating schematic card alongside the dashboard generator and both templates. Preserve existing entity identities and export-overlay services.

## 0.5.2 — 2026-11-05

- Adapt the native temperature interface used by Home Assistant 2026.11 while retaining the legacy interface on 2026.10.
- Prevent an ordinary duplicate login from replacing the configured account; reject a different known account during reauthentication.
- Remove raw API payloads and reauthentication input from debug logs.
- Ignore incomplete or colliding producer records without renaming existing entities or mixing gas and electric measurements.
- Preserve timed overrides, export safeguards, manual OFF, clock programs, upstream diagnostics and existing services.
- Keep prices and cost calculations out of the dashboard templates and generator; retain power and calendar energy displays and the current control-first composition.
- Add account, privacy, temperature-interface and hybrid-producer regressions and a two-version CI matrix.
- Keep this series a prerelease: export-overlay behavior remains experimental. Python runtime changes require a Home Assistant restart after installation.

## 0.5.1 — 2026-10-29

- Add draft-only duration presets from 30 minutes to 48 hours to both anonymous dashboard templates.
- Keep manual minute input and the separate Start button; choosing a preset never sends a heating command or changes an active override.
- Preserve user-specific dashboards through the existing private-template generator workflow.

## 0.5.0 — 2026-10-29

- Add per-zone temporary temperature and duration inputs and a start button for devices reporting a timed override capability.
- Send the end time to the device; expose cloud-reported status, pending requests, and the override end time.
- Add experimental, explicitly invoked export-overlay services that preserve the user's target and end time, journal uncertain outcomes, and verify caller-provided guards and measurement freshness.
- Keep export automation outside the public integration; external cloud writes cannot be atomically compared and may race.
- Extend the anonymous English dashboard generator, both bundled templates, synthetic bindings, and documentation.
- Device expiry and repeated override commands require verification on the particular appliance; no setup or restart sends a heating command.

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
