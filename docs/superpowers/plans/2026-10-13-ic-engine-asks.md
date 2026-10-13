# IC: the decision study's engine asks 1–6 and the S3 seeds

**Date:** 2026-10-13. **Session:** the IC engine session (`session_01LZWhm5TC6dtSTqM6PpehNw`).
**Base:** master at 47d9dda2 (#154, U2, merged). **Commission:** the owner, 13 Oct (decision S3a and engine asks
1–6; findings `2026-10-05-guided-study-u2.md` "Engine asks still open" and "Open items").
**Reports to:** the coordinator (`session_013NDrjK8eU7A4n1bPW9m6Xj`); the gatekeeper
(`session_01Fz66v1SpJnjGHky1RbUuqC`) hears of every facade, `engine_adapter` or campus-route change first.

One PR per item unless two are trivially small. The owner, or the gatekeeper on their instruction, merges.

| # | Item | Retires | Status |
|---|---|---|---|
| 1 | `series_store.delete_series` / `pinned_by` (+ `list_series`) in the facade; the study deletes through them | C3e (3 `ALLOWED_EXCEPTIONS`) | PR open |
| 2 | Per-part `finance.case.scale_capex(case, f, *, asset=, part=)`; U2 switches; tornado figures pinned | the C5 "Per-part CAPEX bounds" workaround | next |
| 3 | Freeze every engine field the study reads (`test_engine_facade_frozen.py`) | the WP7 freeze and N15 exceptions | |
| 4 | Unrounded FOM / CAPEX / OPEX statistics | the `fom_rounded_by_pypsa_statistics` rows of `tests/fixtures/u2_deltas.json` | |
| 5 | A public "record a solve" helper | `tests/study_s4_support.record_engine_solve` writing `lp_bindings` internals | |
| 6 | A hand-typed price on a grid Link the tariff also prices: warn, show both, numbers unchanged | | |
| 7 | S3 seeds: templates and Energy Hub placeholders from the generic defaults pack (before U4) | hard-coded template costs | |

## 1. `delete_series` / `pinned_by`

- `pinned_by(name, *, project_dirs) -> list[SeriesPin]`: per directory, at most one entry, in the given order:
  `unreadable` (a `library_refs.json` or `solver_config.json` it cannot read), else `sidecar` (a **series** pin of
  that name; an item pin of the same name is not one), else `config` (a `PriceSeriesRef` of that name anywhere in
  `solver_config.json`). A missing directory pins nothing.
- `delete_series(db, org_id, name, *, project_dirs, root=None) -> SeriesDeleted(deleted_versions, kept,
  pinned_by)`: refuses whole (`series_pinned`, `series_pins_unreadable`) when pinned; else deletes every version of
  the org's series and each payload file no remaining row of the org points at. Commits.
- **Deviations from the ask** (told to the gatekeeper): `project_dirs` is required, so the Library never enumerates
  projects (no `project_registry` import) and a delete never skips the pin check; `pinned_by` takes no `db` /
  `org_id` (it reads only directories); no `refuse_if_pinned` flag (no consumer needs a forced delete).
- The study's `compile._delete_series` maps `series_pinned` → `export_series_kept_in_use`, `series_pins_unreadable`
  → `export_series_pins_unreadable`; its callers and their tests are unchanged. One behaviour tightens: a pin of
  another kind with the export series' name no longer keeps it.
- Engine and study switch ship in one PR (both small).

## Survey for items 4–7 (2026-10-13)

- **4.** `results/value_flows._asset_costs` calls `n.statistics.capex/fom/opex(groupby=False)` without `round=`;
  PyPSA 1.1.2 rounds to 5 decimals by default. The `bess_2h` FOM row (rel 1.3e-9) and its siblings carry cause
  `fom_rounded_by_pypsa_statistics`; read by `test_engine_adapter_case_golden.py:118`, `u2_targets.py:113`,
  `qa_decision_study.py:738`.
- **5.** `tests/study_s4_support.record_engine_solve` (used by `test_study_findings.py`, `test_study_runner.py`)
  calls `materialise_poc_prices`, then writes `META_DEMAND`, `META_DEMAND_INFO`, `META_CAPACITY` by hand.
- **6.** `lp_bindings` adds the tariff price on top of the Link's static or time-varying `marginal_cost`
  (lp_bindings.py ~2122). The warning belongs in `commercial/preflight._warnings`.
- **7.** The defaults pack (`services/library/defaults_pack/versions/2026-10-07`) holds battery, solar and campus
  equipment; `project_templates/eh_templates.py` hard-codes annualised costs (genset, BESS, fuel cell, electrolyser)
  and `_build.py` marginal costs. No test ties them to the pack. Gas genset, H2 and fuel cell have no pack rows.
