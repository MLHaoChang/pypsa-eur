<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-36 — Behaviour coverage for the six remaining LC-D13 variants

Status: done
Type: task

Owner decision LC33-ROWS-1: one Opus package covers the six `_pending` LC-D13 rows that
LC-33 (#945) added and owned `UNASSIGNED` in `OWNERS` (`smoke/release_evidence.py`). GK A
reviews.

| Variant | Annex clause (domain-coverage.md:74) |
|---|---|
| `LC-D13.multi_energy_heat` | Heat attribution |
| `LC-D13.finalist_certification` | Finalist certification |
| `LC-D13.unsupported_scope` | Unsupported scope |
| `LC-D13.study_swap` | Study swap |
| `LC-D13.stale_source` | Stale source |
| `LC-D13.incomplete_campaign` | Aborted / incomplete campaigns |

`LC-D13.multi_energy_dedicated_rollup` stays `_unavailable` (unreachable from the seam).

Same bar as LC-33 (`33-unassigned-variants-d06-d13.md`) and its `_covered` rows: a test
drives the real engine through the chat tools (`seam` from `test_lc06_d05_foresight.py`,
`chat_service._dispatch_real_tool_call`), pins a hand-computed number or outcome, and a
mutation moves it by a hand delta. No engine stubs. A variant the seam cannot reach is
recorded as `_unavailable` with the reason, as GK A ruled on #945.

Plan first: post each variant's oracle and mutation in this file for GK A's GO before
writing tests.

## Acceptance

- [x] Each of the six rows is `_covered` (or `_unavailable` with GK A's OK) in
      `smoke/family_manifest.py`, and `OWNERS` names LC-36.
- [x] test_family_manifest, test_release_evidence and the new tests pass.
- [x] GK A OK on the final head.

## PRs

- #954 (merged): the six rows, tests `pypsa-gui/backend/tests/test_lc36_d13_*.py`. GK A OK @1c89435bf025dc7944412dd279a4b0504c3547ff.
- Follow-up: tickets 37 (`37-adequacy-readers-stale-source.md`) and 38 (`38-typed-scope-refusal-kinds.md`).

## Comments

### Plan (sent to GK A before #948 landed; this folder was out of bounds until then)

| Variant | Oracle | Mutation |
|---|---|---|
| `multi_energy_heat` | Dedicated heat bus: a 10 MW heat pump (efficiency 3) runs first, so heat unmet 20 MWh and electrical 10 MWh, by carrier and by Load; the ENS target is 10. Shared bus: heat at least the forced floor, readers agree Load by Load | heat +5 MW: heat 30, electrical exactly 10 |
| `finalist_certification` | The engine certifies the ENS plan only and redundancy.py:675 discloses `finalists_mc_certified` False | — |
| `incomplete_campaign` | (a) budget 3 refuses a 4-solve frontier (`campaign_budget_exhausted`, spent 0); 2 targets spend 3; `end_campaign`. (b) EH redundancy at `budget_solves` 2: base only, `budget_exhausted`, no selection | budget 4 runs all three; budget_solves 10 selects n1_generation |
| `unsupported_scope` | frontier at VoLL 0 and margin loop under 'rolling' refused, no solve charged, reader byte-identical | the in-scope run gives the hand numbers |
| `study_swap` | a live frontier blocks `activate_project` (`study_in_flight`) and `load_project`; after finish or abort the switch goes through, B reads `no_data` | the finished run gives the hand points |
| `stale_source` | EH `edited_since_study` False → True after `update_component` → cleared by a re-run | AC Load 50 → 55 MW: electrical unmet +10 MWh |

### GK A's conditions (GO with conditions, 30 Oct)

- General: each mutation runs in the test; each file runs under 590 s alone with -x; a defective value is never pinned (a `_pending` row with a ticket, no xfail or skip).
- multi_energy_heat: GO. Add a shared-bus heat floor with Load-by-Load agreement, and show that the bus-carrier mutant on `ens_by_carrier_mwh` (multi_energy.py:143) fails the test. Done: the shared-bus test fails under it.
- finalist_certification: split. `LC-D13.finalist_certification` is `_unavailable` (not built). The tests go in a new `_covered` row, `LC-D13.mc_certify_ens_plan`: the disclosure, the certification fleet excluding the spare (the CI holds the ENS plan's 1270.2 h COPT LOLE and excludes the finalist's 479.6 h), and the verdict by hand, including the inconclusive band.
- incomplete_campaign: GO. Pin the refusal kind exactly and spent/entries unchanged. Derive solves_attempted = budget − 1 from the code in the docstring.
- unsupported_scope: GO with option A. The refusals are untyped (`tool_error`), so the test pins the kind, the guard's own text (unique to its raise site), no charge and a byte-identical reader. A guard-bypass mutant must fail at the refusal assertion; both do. The row names the untyped refusals, and the typed kinds are ticket 38.
- study_swap: GO, not timed by size. A `threading.Event` gate sits before the real `run_frontier_sweep` (#936 pattern), with a timeout on every wait. Covers finish→swap and abort→swap.
- stale_source: option A. The row covers the EH marker. LC-36 confirmed that the margin_loop, frontier, mc and campaign_status outputs carry no freshness key or text. `LC-D13.stale_source_unflagged_readers` is `_pending` with ticket 37.
