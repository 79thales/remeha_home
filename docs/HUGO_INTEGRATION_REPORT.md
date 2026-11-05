# Selective integration report

Date: 2026-11-05. Branch: `feature/hugo-selective-audit-20261105`.
Source snapshot and all decisions: [HUGO_FORK_COMPARISON.md](HUGO_FORK_COMPARISON.md).
Cost investigation: [HEATING_COST_REVIEW.md](HEATING_COST_REVIEW.md).

## Implemented adaptations

1. **HA temperature compatibility** (Hugo `a2bc341`): select native Celsius
   unit/current/target members when supported, otherwise expose the legacy HA
   2026.10 interface. The underlying values, OFF target suppression, locks,
   journal invalidation, cloud commands and mode mappings are retained.
2. **Privacy** (`01f308b`): remove raw dashboard/technical/consumption API debug
   dumps and reauth-input logging. Coordinator logs report health/counts only.
   Existing conservative diagnostics are retained rather than replaced.
3. **Account protection** (`57f96bb`): abort ordinary duplicate setup before
   login, update only the requesting entry during reauth and reject a different
   known JWT subject. No credentials, real entries or unique IDs were changed.
   Decoding subject metadata does not replace OAuth authentication. If legacy
   tokens have no usable subject, that additional comparison is unavailable.
4. **Producer fail-closed filtering** (`76421ea`, `2de00a3`): incomplete device
   placeholders and colliding legacy instance IDs do not register or overwrite
   another source's measurement. Existing sensors become unknown and recover
   under their original IDs when data are unambiguous. Raw gas conversion is
   still applied even to incomplete records; it is separate from registration.
   No persisted identity allocator or migration was introduced.
5. **Testing/CI**: add real HA account-flow tests adapted from Hugo, log privacy,
   native/legacy interface contracts and malformed/hybrid recovery regressions.
   Configure independent test-plugin pins 0.13.372 and 0.13.375 in CI; do not
   force an incompatible HA wheel over a plugin dependency.

## Deliberately deferred or skipped

- Energy history: daily, indicative, response-only estimates are useful, but
  do not fix intraday price allocation. Multi-device, hybrid, DST/date-bound
  semantics and quota/concurrency need verification before registration of a
  new service. No duplicate sensor or Recorder backfill was added.
- Holiday commands/restoration: saved modes cannot prove ownership of a later
  manual OFF, and restoring TemporaryOverride as Scheduling loses its expiry.
  Keep existing holiday status read-only. Do not automatically activate heat.
- Producer suffix allocation: migration and historical mixed identities must
  be reviewed explicitly before persistent IDs are assigned.
- Cached-success outage behavior: would weaken target refresh-success guards;
  keep unavailable/failed state rather than bless stale control inputs.
- Climate/DHW mode, curve, boost and optional-data implementations: preserve
  target locks, explicit support/range checks and existing external automation
  contracts. Reference implementations are not wholesale replacements.

## Changed files in this task

- `api.py`, `coordinator.py`: logging and conservative producer validation.
- `climate.py`, `water_heater.py`: compatibility accessors only.
- `config_flow.py`, `translations/{en,fr,nl,pl}.json`: account guards and messages.
- `tests/test_config_flow.py`, `tests/test_hugo_adaptations.py`: regressions.
- `.github/workflows/tests.yaml`, `.gitignore`, and these three reports.

Protected `export_override.py`, `temporary.py`, `upstream.py`, `services.yaml`,
and existing config-entry/entity IDs were not changed. Generator/templates and
cost work were already dirty at task start and were preserved **outside the
new task commit**: README, generator, both templates, example bindings,
test_generate_dashboard, untracked heating_costs.py/test_heating_costs.py.
No live HA configuration, dashboard, automation, credentials or statistics was
modified. Read-only cost queries and private source snapshots are ignored under
`.audit-private/`; they are not release content.

## Executed checks and limitations

- Full current-worktree HA regression: **125 passed in 6.08 s**, including
  pre-existing cost-draft tests. Python **3.14.2**, real HA **2026.10.2**,
  pytest **9.0.3**, test plugin **0.13.373**, Windows local harness.
- Initial new flow test found a missing reauth-success message; messages were
  added. Its mocked unload lifecycle was corrected to avoid teardown invoking
  a real unload on deliberately absent runtime data.
- Initial producer filter regression found raw incomplete gas records were
  skipping numeric/unit normalization. Normalization was separated from device
  registration; the original gas conversion regression now passes.
- The environment initially crashed in OpenSSL due to an inherited optional
  TLS key-log target. Only the simulated test process clears `SSLKEYLOGFILE`;
  production settings and authentication were not touched. Windows POSIX-only
  harness operations raise rather than pretending to succeed; loopback sockets
  are permitted for asyncio, external cloud I/O remains mocked.
- Ruff lint, Ruff format and `git diff --check` were run; final results recorded
  below after validation of the exact review snapshot.
- Full HA **2026.11.0** runtime tests: **not executed**. The configured package
  index could not provide that wheel; currently discoverable [plugin 0.13.375](https://pypi.org/project/pytest-homeassistant-custom-component/0.13.375/)
  pins 2026.10.4. Both native/legacy property contracts are tested, but this is
  not equivalent to a complete 2026.11 lifecycle test.
- GitHub CI/HACS/hassfest actions: **not executed in this task**. No branch was
  published and no remote job dispatched. The actions require the remote
  checkout or a Linux action runner; no local Docker runner is available.
  Existing HACS validation explicitly skips inherited missing-license checks;
  do not report that check as passed. Review should run both workflows after
  branch publication is authorized, then add a verified 2026.11 runtime pin.
- No release was published, no main branch changed, and no external PR/issue
  was created. This is a review candidate, not an installed or released build.

## Follow-up and manual verification

Review the narrow changes first, run Linux HACS/hassfest and current HA runtime
tests, then verify multi-device identifiers in a non-operating fixture. A later
deployment should check OFF, clock programs, timed expiry and export coexistence
without enabling any new automatic heating. Review the cost-display proposal
separately; it needs a documented backfill/estimation policy and historical tariff
basis, not a guessed replacement annual total.
