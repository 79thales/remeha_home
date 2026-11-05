# Hugo fork comparison

Snapshot: 2026-11-05. Scope is the target repository only. Reference code was
read through fetched Git objects; no reference repository was edited.

## Reproducible Git basis

| Ref | Commit |
|---|---|
| Target `origin/main` | `38a5fcc55b9031055f3cdffdd5cb3d590bb29be5` |
| Hugo `hugo/main` | `a2bc3413d83222249952948e962702f2cb633262` |
| Original `upstream/main` | `691f0c5f12666c1f0b618e252ceb9255c0ec4690` |
| Target/original merge base | `691f0c5f12666c1f0b618e252ceb9255c0ec4690` |
| Target/Hugo merge base | `20bf0ffa82055c1b341ae538529af4473fbbb5bf` |

`git rev-list --left-right --count A...B` gives original/target **0/79**,
original/Hugo **0/133**, target/Hugo **14/68**. Counts include merge commits;
they are graph counts, not a count of independent features. Historical numbers
in the task happen to match this fetched snapshot. The common base and the
two-sided diffs were both inspected; direct tip-to-tip deletions do not mean
that equivalent target functionality is absent.

There are **65 shared commits after original upstream**, ending at the common
fork base `20bf0ff`. Shared ancestry already contains the community work on DHW,
boost, activities and heating curves; the later forks refine those implementations
differently. This shared code must not be counted again as a new Hugo feature.

Reproduce with `git merge-base origin/main hugo/main`,
`git log origin/main..hugo/main`, `git log hugo/main..origin/main`,
`git diff origin/main...hugo/main`, and `git diff origin/main hugo/main`.
The appendices list all graph-unique commits and changed files.

## Feature decisions

Commit links refer to [Hugo's source history](https://github.com/hugo-leij/remeha_home/commits/main/).
Target implementations were checked by symbol and behavior, not just filename.

| Feature/fix and source commits | Files | Existing target implementation | Benefit | Risk | Decision and evidence |
|---|---|---|---|---|---|
| Native temperature members, `a2bc341` | climate.py, water_heater.py | Legacy accessors, Celsius, protected control methods | HA 2026.11 compatibility | Native/legacy accessors differ across HA releases | **ADAPT**: capability-selected aliases; regression covers both interfaces and OFF; full target suite run locally. Full 2026.11 runtime remains a verification gap. |
| Remove obsolete connection class, `a2bc341` | config_flow.py | Deprecated constant retained | Avoid upcoming HA warning/removal | None to cloud transport | **ADOPT**: remove unused declaration; flow tests run. |
| Debug privacy, `01f308b` | api.py, coordinator.py, config_flow.py, diagnostics.py | Diagnostic download already summarizes without identities, but debug logged raw payloads/reauth input | Remove household names, serials and tokens from debug output | Removing useful debugging detail | **ADAPT**: log counts and endpoint health, no raw payloads; sentinel privacy tests and reauth token test. |
| Account overwrite protection, `57f96bb` | config_flow.py, translations | One fixed unique ID, ordinary login could update existing entry | Duplicate setup stops; reauth targets original entry and rejects another known subject | Older tokens can lack decodable subject | **ADAPT**: preserve entry IDs/OAuth; compare available subjects only; six real HA flow tests. JWT decoding is metadata inspection, not signature verification. |
| Short-outage data retention, `b193402` | coordinator.py | Failed read marks data unavailable; explicit command guards rely on refresh success | Fewer unavailable transitions | Hugo returns cached data as a successful refresh; unsafe for our confirmed-state guards | **SKIP**: retain fail-closed refresh semantics. New timestamped cache design would be needed before adaptation. |
| Per-request timeout/retry | api.py, coordinator.py | 30s request/dashboard timeout, 10s optional reads; writes never blindly retried | Bounded failures already provided | Retrying a timed-out write can duplicate a physical command | **SKIP** bulk replacement; target is stricter. Existing API-failure tests retained. |
| Daily energy history, `35df9af` | energy_history.py, energy_history_service.py, api.py, services.yaml | Current daily cloud sensors plus independent meter statistics and cost draft | On-demand historical estimates, missing-day coverage, hybrid separation | Daily data cannot price intraday tariff changes; date timezone semantics and real hybrid response need verification | **INVESTIGATE**, integration plan below; no new sensors or Recorder writes introduced. |
| Holiday set/cancel, `745bf69`, `a0d79be` | holiday.py, api.py, binary_sensor.py, services.yaml | Read-only holiday status exists in const.py/sensor architecture | User-visible holiday scheduling | Device-wide command affects multiple zones; conflicts with timed/export overrides | **INVESTIGATE** manual-only API after arbitration design; not implemented. |
| Holiday restoration, `ea784c3`, times `40a0ebe` | holiday.py, __init__.py | No automatic holiday restoration | Restore prior modes across restart | Current FrostProtection/Off may be user's later OFF; saved TemporaryOverride loses original expiry when restored as schedule; retries send commands in background | **SKIP**: violates required OFF precedence without ownership/CAS. Tests in Hugo do not resolve indistinguishable external OFF. |
| Persistent hybrid producer identities, `76421ea`, `2de00a3`, `d9e4016` | producer_ids.py, coordinator.py, __init__.py, sensor.py | Legacy appliance/instance IDs can collide | Separates gas and electric sources with overlapping instance numbers | Existing IDs might already contain mixed history; allocating suffixes requires explicit registry migration | **ADAPT partially**: filter incomplete records and suppress ambiguous legacy instances, retaining IDs and unknown values. Persisted allocation/migration deferred. New hybrid disappearance/recovery regression. |
| via_device_id, `c8da01c` | coordinator.py | Already implemented, stable parent IDs | HA registry compatibility | Replacing target registry setup would regress | **SKIP**, already covered by parent/restart/full setup tests. |
| Appliance/holiday diagnostics, `2b2c0a4`, `ec2a935`, `31461b8` | binary_sensor.py, sensor.py, const.py | Already has online, pressure, preheat, mode, error and holiday read-only fields | Similar information | Duplicate entities/IDs if new platform copied | **SKIP** duplicate creation. Existing missing/invalid boolean tests retained. |
| Rich diagnostics, `f1320e6`, `a8ec8cd` | diagnostics.py | Sanitized structural/availability summary | More debugging detail | Full redacted payload needs broader privacy validation | **INVESTIGATE** optional fields; keep conservative target diagnostics. |
| Hot-water next mode/program/optimization, `d8225ac` | const.py, sensor.py | Current schedule/status, boost and target controls exist | Additional read-only context | Per-device support and stable roles not confirmed | **INVESTIGATE** additive fields only, no duplicated controls. |
| Heating curves, `ee7aab3`, `7589e3c` | number.py, coordinator.py | Fresh range validation, optional endpoint throttling, serialized writes and late discovery already exist | Extra source tests useful | Hugo defaults missing limits and uses cached activity values | **SKIP** runtime replacement; retain safer validation and heating-only payloads. |
| DHW boost switches | switch.py, water_heater.py | Confirmed capability, shared lock, update only after successful command | Target already has deterministic behavior | Reference assumes boost support when field absent and uses optimistic state | **SKIP** overwrite. Preserve OFF/Eco, schedule/heat_pump, comfort/performance and boost semantics. |
| Climate HEAT/HEAT_COOL mapping | climate.py | Manual heat contract, no inferred cooling capability | External automations retain stable modes | Hugo mapping differs from target manual heat contract | **SKIP** control mapping. Only accessor adaptation applied. |
| UTC/time helpers, `660f4e0`, `6ad7c42` | util.py, sensor.py | Timestamp parsing preserves offset and rejects sentinel dates | Shared normalization useful | Changing interpreted calendar boundaries can shift history | **INVESTIGATE** before replacing existing tested timestamp behavior. |
| Combi boiler temperature omission, `d5d7de6` | sensor.py | Missing values remain unknown; existing entity IDs retained | Hide unsupported measurements | Removing existing entities is unnecessary migration | **INVESTIGATE** for new devices; do not delete established entities. |
| Tooling/dependency workflows, `4948dbf`, `88dbee0`, `6f36d87` | .github, requirements, devcontainer | Python 3.14, pinned HA tests, Ruff, HACS/hassfest and private-safe release builder | Broader supported-version coverage | Blind action upgrades and license changes need separate review | **ADAPT** test-plugin matrix; retain package/privacy checks and explicit inherited-license skip. Remote jobs not dispatched. |
| Target-only features | export_override.py, temporary.py, upstream.py, generate_dashboard.py, templates, services | Timed overrides, guarded export journal, upstream monitor, private-aware dashboards | Required user functionality | Hugo has no equivalent complete replacement | **PRESERVE** unchanged control/journal/upstream files and public interfaces. |

## Energy history: examined behavior and integration gate

Hugo adds a read-only response-only `get_energy_history` action, resolving the
parent appliance from device registry ancestry rather than producer ID prefixes.
It requests `GET /appliances/{id}/energyconsumption/daily` with inclusive date
bounds, split into at most 31-day batches and at most 366 requested days.
This is a client request limit, **not evidence of server retention**.

Normalization returns daily heat-pump heating consumption/delivery in kWh,
gas heating consumption in kWh, per-producer values and calculated COP. It does
not provide an intraday price series, a full TUV/cooling breakdown in the
normalized result, or independently measured heat output. Missing values remain
unknown; duplicate conflicting dates become missing. Period sums can still be
partial when `complete` is false and must not be presented as a complete bill.

The service publishes neither sensors nor HA statistics, so it would not itself
duplicate target daily sensors. It would inherit our API timeout, while avoiding
the target coordinator's gas kWh-to-m³ conversion by using raw endpoint results.
Before adoption: verify real multi-device/hybrid samples, UTC request bounds vs
local calendar labels (including DST), quota/backoff and a request concurrency
limit; include energy basis/coverage in all response fields. No automatic
Recorder backfill, entity migration, or cost calculation should be coupled to it.

## Conflicts and protected paths

The common ancestor predates both forks' newer features. Main conflict areas are
climate.py (locks/journal vs mappings), coordinator.py (freshness/identities),
__init__.py (journals/upstream vs holiday restoration), services.yaml and sensor
registration. A wholesale merge would remove or weaken target-specific controls.
No holiday/energy service, producer-ID migration or new heating automation is
enabled by this branch. Existing dashboard/cost work found dirty at task start
is preserved separately and is not attributed to Hugo.


## Appendix: Target-only

```text
38a5fcc Add longer duration presets including 20 hours
6cc73c7 Add draft-only duration presets to dashboard
d3dae0f Account for restored unavailable entities on unload
5e46e14 Prepare 0.5.0 and test timed controls in Home Assistant
a04ccb2 Format timed override changes
71d39d3 Add timed zone overrides and guarded export overlay
ad4cdf0 Add read-only appliance and holiday status diagnostics
53986d4 Anonymize public dashboard generator and publish English documentation with standard badges
b0a7ec5 Run GitHub fixtures in HA event loop and validate comparison consistency
6619e50 Add independent upstream commit monitoring and manual check
1ddda9c Add heating dashboard generator with Shelly details and calendar consumption
2a61c0a Configure HACS validation for inherited unlicensed upstream
e5be274 Handle ambiguous hot water activity and late optional data safely
c862de5 Add maintained Remeha fork with DHW, curve controls and HA compatibility fixes
```

## Appendix: Hugo-only (including merges)

```text
a2bc341 Use the native temperature members on Home Assistant 2026.11 (#22)
35df9af Add read-only daily energy history action (#18)
190f7b7 Merge pull request #17 from hugo-leij/dependabot/pip/python-3d5b315546
17d0b0b Bump the python group across 1 directory with 2 updates
e6ece08 Merge pull request #21 from hugo-leij/fix/debug-log-privacy
01f308b Keep personal data out of the debug log
7ae9d1d Merge pull request #20 from hugo-leij/fix/config-flow
57f96bb Don't let a new login replace the configured account
a0acd7c Merge pull request #19 from hugo-leij/fix/coordinator-timeouts
b193402 Keep the data when the Remeha API times out briefly
69dc01e Merge pull request #16 from ErikT80/fix/hybrid-producer-id-collision-clean
d9e4016 Log expected incomplete producer records at debug level
2de00a3 Address review: preserve legacy IDs and ignore incomplete producers
76421ea Fix hybrid producer ID collisions and preserve stable entity identities
4f05947 Merge pull request #14 from hugo-leij/docs/holiday-times
40a0ebe Correct the holiday times in the API documentation
32680bc Merge pull request #13 from hugo-leij/fix/holiday-restore
ea784c3 Restore the zone modes after a holiday
725638c Merge pull request #12 from hugo-leij/fix/via-device-id
c8da01c Link the devices to the appliance with via_device_id
1485c6d Merge pull request #11 from hugo-leij/docs/contributing
c8d7deb Merge pull request #8 from hugo-leij/dependabot/pip/python-fc093c6737
f4b4cdd Ask for the diagnostics in the bug form
23e1093 Add a contributing guide
efc45dc Add issue forms for bugs and feature requests
95452e4 Merge branch 'main' into dependabot/pip/python-fc093c6737
61ede64 Merge pull request #6 from buhito81/feature/more-status-sensors
51b0419 Merge pull request #10 from hugo-leij/chore/ruff-0.17
2aeb6e4 Import from datetime like the other modules
1be2d5b Merge pull request #9 from hugo-leij/fix/translations
90e4b5b Merge pull request #5 from buhito81/feature/diagnostics
1f604c3 Bump the python group with 3 updates
0186be8 Merge pull request #7 from hugo-leij/chore/tooling
a4c695a Translate the login texts in Dutch and French
b932b00 Explain the diagnostics download in the README
a8ec8cd Test the diagnostics download
f1320e6 Add a diagnostics download
d8225ac Add Next Mode, Clock program and Optimization to hot water zones
ec2a935 Add an Operating Mode sensor
88dbee0 Require Home Assistant 2026.10
6f36d87 Add Dependabot for the actions and Python requirements
4948dbf Update the GitHub Actions
d3b1ba6 Update the development container to Python 3.14
3f1c5f4 Update ruff to 0.16.10
d41bd2d Merge pull request #4 from hugo-leij/feature/heating-curve
f3580d5 Explain the heating curve in the API documentation
7589e3c Test the heating curve entities
ee7aab3 Improve the heating curve entities
eaaf693 Merge pull request #3 from hugo-leij/docs/api
be72bd4 Use readable headings in the API documentation
6e6fa32 Merge branch 'main' into docs/api
d63b01b Merge pull request #2 from hugo-leij/feature/pr132-status
fc4791f Rework the API documentation
497ee71 Point the manifest to this fork
7915fd5 Explain how to install the fork
5a37f5d Skip the HACS license check
c2aff0d Add tests and run them on every pull request
31461b8 Add Error Status and Override End sensors
660f4e0 Add a helper for the times that the API sends in UTC
2b2c0a4 Add Online, Water Pressure and Pre-heat binary sensors
d5d7de6 Skip the water temperature sensor for a combi boiler without one
6e1472c Remove an unused import
6ad7c42 Parse Remeha times with a shared helper
b012e8b Explain holiday mode in the README, with example scripts
a0d79be Polish the holiday mode errors and delayed refresh
1168b30 Bring the README in line with the entities the integration creates
b38a95f Merge pull request #1 from wilczman/holiday-mode
745bf69 Add holiday mode: binary sensor and set/cancel actions
```

## Appendix: Changed files between tips

```text
.devcontainer.json
.github/ISSUE_TEMPLATE/bug_report.yml
.github/ISSUE_TEMPLATE/feature_request.yml
.github/dependabot.yml
.github/workflows/lint.yaml
.github/workflows/release.yaml
.github/workflows/tests.yaml
.github/workflows/validate.yaml
.gitignore
.ruff.toml
.vscode/settings.json
CHANGELOG.md
CONTRIBUTING.md
README.md
custom_components/remeha_home/__init__.py
custom_components/remeha_home/api.py
custom_components/remeha_home/binary_sensor.py
custom_components/remeha_home/button.py
custom_components/remeha_home/climate.py
custom_components/remeha_home/config_flow.py
custom_components/remeha_home/const.py
custom_components/remeha_home/coordinator.py
custom_components/remeha_home/dashboard-bindings.example.json
custom_components/remeha_home/dashboard-template.json
custom_components/remeha_home/dashboard-template.yaml
custom_components/remeha_home/diagnostics.py
custom_components/remeha_home/energy_history.py
custom_components/remeha_home/energy_history_service.py
custom_components/remeha_home/export_override.py
custom_components/remeha_home/generate_dashboard.py
custom_components/remeha_home/holiday.py
custom_components/remeha_home/manifest.json
custom_components/remeha_home/number.py
custom_components/remeha_home/producer_ids.py
custom_components/remeha_home/sensor.py
custom_components/remeha_home/services.yaml
custom_components/remeha_home/switch.py
custom_components/remeha_home/temporary.py
custom_components/remeha_home/translations/en.json
custom_components/remeha_home/translations/fr.json
custom_components/remeha_home/translations/nl.json
custom_components/remeha_home/translations/pl.json
custom_components/remeha_home/upstream.py
custom_components/remeha_home/util.py
custom_components/remeha_home/water_heater.py
documentation/DASHBOARD.md
documentation/FORK.md
documentation/api.md
documentation/releases/v0.2.0.md
documentation/releases/v0.2.1.md
documentation/releases/v0.3.0.md
documentation/releases/v0.3.1.md
hacs.json
pytest.ini
requirements.txt
requirements_test.txt
scripts/README.md
scripts/build_release.py
scripts/develop
scripts/lint
scripts/setup
tests/__init__.py
tests/conftest.py
tests/fixtures/activities.json
tests/fixtures/consumption.json
tests/fixtures/dashboard.json
tests/fixtures/heating_curve.json
tests/fixtures/hybrid_same_instance.json
tests/fixtures/technical.json
tests/test_appliance_status.py
tests/test_binary_sensor.py
tests/test_boost.py
tests/test_config_flow.py
tests/test_coordinator.py
tests/test_devices.py
tests/test_diagnostics.py
tests/test_energy_history.py
tests/test_export_override.py
tests/test_generate_dashboard.py
tests/test_heating_curve.py
tests/test_holiday.py
tests/test_hot_water_sensors.py
tests/test_hot_water_temperature.py
tests/test_integration.py
tests/test_logging.py
tests/test_producer_storage.py
tests/test_release.py
tests/test_setup.py
tests/test_status_sensors.py
tests/test_temperature_members.py
tests/test_temporary.py
tests/test_time.py
tests/test_translations.py
tests/test_upstream.py
```

Patch comparison (`git cherry origin/main hugo/main`): 47 distinct non-merge patches, 0 patch-equivalent commits. This does not imply 47 missing features; semantic equivalents are identified in the matrix.
