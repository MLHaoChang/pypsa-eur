# LC-08 — source-pinned whole-family and live release evidence

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: Applicable LC-01–07 implementation

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C10; LC-T16/17/18/19.

Require the [LC-D01–24 operation/variant manifest](../domain-coverage.md), with
all 14 families, audited unmapped controls, independent numerical oracles,
applicable negative/recovery cases and explicit source-pinned dispositions.
One successful representative journey cannot close an entire family.

Exercise integrated real native/browser journeys for every released capability, prolonged/restart/provider cases and separately authorized real speech/device probes. Record denominators, source, skips and limitations. Keep implementation, fixture testing, vendor readiness, domain review, calibration and production enablement distinct.

Expected ownership: family journey/evaluation fixtures, release evidence/readiness and rollback documentation.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Comments

GK B GO for the plan (2026-10-29, base `52709a0b`), with seven conditions:
the output file is git-ignored; `evidenced` needs at least one covered variant
and no pending one; `--check` fails on a missing mapped file or a node id that
does not collect (reusing `family_manifest.uncollected_nodes`); the JSON never
carries a run result; the five uncomputed statuses are pinned to
`not_established`; families, denominators and owners are read at run time
(one `OWNERS` table, a pending variant without an owner fails); the partial
rule is shown by mutation.

## Slice log

**Slice 1 (`claude/lc08-release-evidence`).** `smoke/release_evidence.py`
`--check` / `--write` (output `.scratch/live-copilot/release-evidence.json`,
git-ignored) over `family_manifest.build_manifest()`.

- RED: `tests/test_release_evidence.py` failed at collection (no runner); green
  after the runner: 9 passed.
- Mutation: treating `pending` like `unavailable` in `_fixture_testing` fails
  `test_a_family_with_one_pending_variant_is_partial_and_names_it`; returning
  `evidenced` with nothing covered fails `test_an_all_unavailable_family_is_not_evidenced`.
- `--check` on `52709a0b` + this slice: 14 families, 74 variants (53 covered,
  18 pending, 3 unavailable), tools 45 / 367 listed by a covered variant, 0
  unmapped. `fixture_testing`: `evidenced` for gridspine-campus,
  economics-investment and experiments (named variants only; their tool
  denominators stay in the JSON); `partial` for the other eleven.
- Pending owners: D01/D02/D03/D20–D24 → LC-32 (LC32-OPEN-1, no tests written
  here); D11.fmea_criticality → LC-30; D06.sclopf, D06.ac_stage and seven
  D13 variants → unassigned (the coordinator ranks them). The runner re-reads
  the manifest each run, so LC-32's merges show on the next `--check`.

Local runs (container, 2026-10-29; journeys and tests unchanged from master
`52709a0b`):

| Evidence | Command | Result |
| --- | --- | --- |
| Browser OJ-01, OJ-05, OJ-21, LC-03 historical | `npm run build`, then `PYPSA_COMPOSER_PYTHON=python3 CHROMIUM_BIN=/opt/pw-browsers/chromium node scripts/smoke-copilot-<id>.mjs` | 4 / 4 exit 0, `"passed": true` (loopback model wire; real backend and Chromium) |
| Backend OJ-01/05/06/07/09/46, prolonged, restart, shutdown | `python -m pytest <module>` (15 modules, 4 batches) | all passed |
| Provider parity | `python -m pytest tests/test_*provider_parity.py` | 5 failed in `test_harness_provider_parity.py` (`requests` not installed here) and 2 `[*-anthropic]` cases in `test_workflow_provider_parity.py` (connection error; this venv has anthropic 2.3.0, `pixi.lock` pins 0.117.0). Local environment only; the PR's "GUI backend" CI in the pixi test env is the run of record |

Not run, by design: Tier B live journeys (paid calls are outside this
package); real speech/device probes (VOICE-ORDER-2 holds LC-07 and
live-voice 09). Vendor readiness, domain review, calibration and production
enablement stay `not_established`.
