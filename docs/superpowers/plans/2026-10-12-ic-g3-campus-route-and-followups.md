# IC G3: campus equipment through the investment-case route, and the G1/G2 follow-ups

**Date:** 2026-10-12. **Session:** the IC session. **Base:** master at db78307 (G1/G2, #101, merged).
**Owner decision 2026-10-12:** build the route on the IC side together with the review follow-ups, one PR.
**Shapes it implements:** U1 landing plan §8, default **B**: the investment-case route takes the extra owner assets
from the latest solved campus study of the same project, pinned by `source_hash`; a stale study is flagged and not
used. (The campus session never said who builds the route; the IC builds it and tells the campus session.)

## 1. Scope

| WP | What |
|---|---|
| **R1** | `POST /results/investment_case` passes the campus study's owner assets as `extra_assets` |
| **R2** | The staleness key gains an `extra_assets` part; a changed campus study marks the report stale |
| **F1** | Review follow-ups: numeric part fields and `quantity` cast to float; a strict bool for `derived_from_capital_cost`; a non-empty `str` part name |
| **F2** | The staleness key covers the owner assets' own investment columns (S0b findings limits 4 and 6) |

Out of scope: a frontend control (the Investment tab shows the `extra_assets` block the report already carries),
staged builds (P5), the campus producer (on master, #97; not edited).

## 2. Facts (master db78307)

- **The run route.** `routers/results.py::post_investment_case` (≈ line 1551) builds the case through the closure
  `_ic_build_case(n, cfg, *, owner, lost_load)` (≈ 1455), which calls
  `build_finance_case(n, cfg, fin, result_df=…, lost_load=…, owner=…)` under the network lock in the runner's worker;
  the staleness key is `_ic_assumptions(cfg, n)` (≈ 1476) → `investment_case_runner.assumptions_digest(finance,
  commercial, dispatch, packs, solver)`; `GET /results/investment_case` recomputes it in
  `_ic_current_assumptions()` and `staleness()` names the changed parts.
- **The campus producer** (`services/campus_electrical_service.py`, #97):
  `owner_assets_response(project) -> {"assets": [...], "source_hash": str | None, "stale": bool}`; `assets` are
  dicts in `ExtraOwnerAsset`'s field names plus `meta`; it calls `require_capacity_expansion(project)` first, which
  raises `HTTPException(409)` for a project that is not a capacity-expansion (hub) project, and `require_engine()`
  (the gridspine engine) before that. `project` is the DB `Project` row (the campus router resolves it with
  `_row(proj, db)`).
- **The results router has no project dependency today**: it works on the session's active context
  (`PyPSAService.get_active_context()`, `_state`). How the active project's DB row is resolved there is to be
  confirmed by the plan review (candidates: `services/active_project.py`, `project_context`, the projects router).
- **G2 accepts the producer's dicts** via `ExtraOwnerAsset(**{k: d[k] for k in fields(ExtraOwnerAsset)})` (tested,
  #101).

## 3. Conventions

- **R-1. Source.** At run start, the route reads `owner_assets_response(project)` for the active project:
  - not a hub project, the gridspine engine absent, or no investment run (`assets == []`, `source_hash None`) → no
    extras, no flag (the case is what it is today);
  - `stale: true` → no extras, the report flagged `campus_study_stale` (default B: flagged, not used);
  - otherwise the assets become `ExtraOwnerAsset`s (dropping `meta`) and are passed as `extra_assets`;
  - a producer refusal (422, e.g. an unknown kind) or a G2 refusal is the run's refusal, typed
    (`campus_assets_invalid` with the producer's or G2's message), never a silent drop.
- **R-2. Read once.** The assets are read once in the request (not in the worker), so the case and its staleness
  key see the same study; the worker's `build_case` closes over them.
- **R-3. Money year.** The producer's assets are in the campus library's currency and `price_year` (`meta`). When
  they differ from `fin.currency` / `fin.currency_year` the run is refused `campus_assets_money_year_mismatch` (the
  caller converts, G2 limit 2); when the case states no currency year, G2's `extra_asset_money_year_unstated` flag
  applies.
- **R-4. Staleness part.** `assumptions_digest` gains `extra_assets`: a digest of `{source_hash, stale, the
  assets without meta}` (None when no campus study applies). A new or re-run campus study changes the report's key
  and `changed` names `extra_assets`.
- **F-1.** In `ExtraAssetPart` normalisation: `upfront_per_unit`, `lifetime`, `fom_share` cast to float, and
  `quantity` to float, before validation (an int and a float of the same value hash alike);
  `derived_from_capital_cost` must be a real `bool` (`extra_asset_invalid:<name>:derived_from_capital_cost`
  otherwise); a part `name` must be a non-empty `str` (`extra_asset_invalid:<name>:parts`).
- **F-2. Investment columns.** `assumptions_digest` gains `owner_assets`: per owner asset of the value-flow config
  (component, name), the investment columns the adapter reads (the S0 part columns, `overnight_cost`,
  `capital_cost`, `lifetime`, `build_year`, `discount_rate`, the capacity and its `_opt`), digested by value. A
  changed `build_year` or part cost then marks the report stale.
- **R-5. Hash stability.** Adding the two parts changes every stored report's key once (each reads stale until
  re-run). Stated in the findings note; no migration.
- **R-6. Tell the campus session** (one-shot message, after merge) that the IC route consumes
  `owner_assets_response`, and that its shape is now a contract pinned by an IC test.

## 4. Work packages, tests first

### WP-R (R-1 … R-5)
Tests (`tests/test_investment_case_campus_route.py`):
- A hub project with a fake investment run (#97's fixtures `hub`, `fake_investment`, `inv_row`): `POST
  /results/investment_case` builds a case whose report carries the `extra_assets` block with the producer's names and
  `source_hash`; capex includes them.
- Stale study → no extras, flag `campus_study_stale`; non-hub project or no run → no extras, no flag.
- A producer 422 → the run refused `campus_assets_invalid`; currency / price-year mismatch →
  `campus_assets_money_year_mismatch`.
- Staleness: after a run, re-running the campus study (a new `source_hash`) makes `GET /results/investment_case`
  stale with `changed` containing `extra_assets`.

### WP-F (F-1, F-2)
Tests (in `tests/test_finance_case_extra_assets.py` and `tests/test_investment_case_*`):
- int vs float values hash alike; `derived_from_capital_cost="false"` refused; an empty or non-str part name refused.
- Staleness: changing an owner asset's `build_year`, a part cost or its `lifetime` makes the report stale with
  `changed` containing `owner_assets`; changing a non-owner asset's columns does not.

## 5. Process

Plan review loop; tests first; an independent code reviewer until PASS; the gate (full backend suite, QA drivers,
vitest, tsc, a findings note, an independent assessor); one PR. The owner merges.

## 6. Review record

(empty)
