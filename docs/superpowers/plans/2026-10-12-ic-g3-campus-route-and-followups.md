# IC G3: campus equipment through the investment-case route, and the G1/G2 follow-ups

**Date:** 2026-10-12. **Session:** the IC session. **Base:** master at db78307 (G1/G2, #101, merged).
**Owner decision 2026-10-12:** build the route on the IC side together with the review follow-ups, one PR.
**Shapes it implements:** U1 landing plan §8, default **B**: the investment-case route takes the extra owner assets
from the latest solved campus study of the same project, pinned by `source_hash`; a stale study is flagged and not
used. (The campus session never said who builds the route; the IC builds it and tells the campus session.)

## 1. Scope

| WP | What |
|---|---|
| **R1** | `POST /results/investment_case` (and the chat PPA solve) passes the campus study's owner assets as `extra_assets` |
| **R2** | The staleness key gains an `extra_assets` part; a changed campus study marks the report stale |
| **F1** | Review follow-ups: numeric part fields and `quantity` stored as float; a strict bool for `derived_from_capital_cost`; a non-empty `str` part name |
| **F2** | The staleness key covers the owner assets' own investment columns (S0b findings limits 4 and 6) |

Out of scope: a frontend control (the Investment tab shows the `extra_assets` block the report already carries),
staged builds (P5), the campus producer (on master, #97; not edited).

## 2. Facts (master db78307, checked by plan review round 1)

- **The run route.** `routers/results.py::post_investment_case` (1551) builds the case through the factory
  `_ic_build_case(n, cfg, *, owner, lost_load)` (1456), whose `build()` calls `build_finance_case(...)` under the
  network lock in the runner's worker. The staleness key is `_ic_assumptions(cfg, n)` (1476) →
  `investment_case_runner.assumptions_digest(finance, commercial, dispatch, packs, solver)`; the runner calls the
  `assumptions` lambda **in the worker, without the network lock**. `GET /results/investment_case` recomputes the key
  in `_ic_current_assumptions()` (1498, under the lock) and `staleness()` compares only the **current** key's parts.
- **Other callers.** `chat_tools.run_investment_case` calls `post_investment_case(body)` directly (2080-2091), the
  chat status tool calls `get_investment_case()` directly (2103), and `solve_ppa_price` builds its case through
  `_ic_build_case(n, cfg, owner=, lost_load=)` (2368-2398). The test fake's signature is
  `factory(n, cfg, *, owner, lost_load)` (`test_investment_case_routes.py:76`).
- **The active project.** `PyPSAService.get_active_context().project_uuid`, set on every save/load by
  `project_registry.bind_context` (159-168); `None` for a scratch context. `resolve_for_session` re-checks the
  pointer per request (`active_project.py:54-85`), so no ACL work is needed. The row is `db.get(Project,
  uuid.UUID(...))` in `with db.session.SessionLocal() as db:` (imported lazily: tests swap `db.session.SessionLocal`,
  `conftest.py:263`). `Depends(get_db)` is not usable (direct callers above).
- **The campus producer** (`services/campus_electrical_service.py`, #97):
  - `require_engine()` raises **503** when `CAMPUS_AVAILABLE` is False; `require_capacity_expansion(project)` calls
    it, then raises 409 for a non-hub project. `kind_of` treats a missing kind as capacity-expansion
    (`gridspine_service.py:122`).
  - `campus_dir(project)` **creates** directories (136-139).
  - `owner_assets_response(project) -> {"assets", "source_hash", "stale"}`; `assets` are dicts in
    `ExtraOwnerAsset`'s fields plus `meta` (`currency`, `price_year`, …, line 603). It can raise `HTTPException(422)`
    and, through `cs.load_asset_library` (568), `ContractError`.
  - `stale` compares the **saved** `network.nc`, the campus file and the library with the run's manifest
    (`_stale`, 334-344); it is False when no compliance results or no manifest exist.
- **The adapter's investment columns** (`finance_case.py:1014-1079`, `asset_schema/access.py:38-67`):
  `overnight_cost`, `lifetime`, `capital_cost`, `discount_rate`, `build_year`, the part columns
  `inv_*_{overnight,lifetime,fom_share}`, `max_hours` (the energy part's scale), `carrier` (incentives), and the
  static capacity column. `dispatch_digest` already covers the `_opt` capacities except Transformer's
  (`dispatch_status.py:334`).
- **F-1 site.** `ExtraOwnerAsset.__post_init__` / `_extra_part` in `services/finance/case.py` (~240, `_real`). The
  only pinned case hash is the no-extras one (`64e7a34307ea1af3`, `test_finance_case_extra_assets.py:449`); `_canon`
  omits an empty `extra_assets` (`finance_case.py:1618`). The producer already passes floats.

## 3. Conventions

- **R-1. Source and order.** A request-time helper `_ic_campus_source() -> CampusSource` (a monkeypatchable seam
  returning plain data, never an ORM row) decides, in this order:
  1. no project (`project_uuid None`), no DB row, or no `project_dir/campus_electrical/run` directory → **none**
     (no extras, no flag, digest `None`). The directory check comes before any producer call, so a run or a GET poll
     never creates `campus_electrical/`.
  2. a run directory but `CAMPUS_AVAILABLE` False → none, flag **`campus_study_unavailable`**.
  3. `kind_of(project) != CAPACITY_EXPANSION` → none.
  4. `owner_assets_response(project)` raises (`HTTPException`, `ContractError`, anything) → **refusal**
     `campus_assets_invalid` (the message text kept generic in the reason; detail logged).
  5. R-3's money-year checks fail → refusal `campus_assets_money_year_mismatch`.
  6. `stale` → no extras, flag **`campus_study_stale`**.
  7. otherwise the assets become `ExtraOwnerAsset`s (dropping `meta`) and are passed as `extra_assets`;
     `assets == []` with a `source_hash` is a valid study (all existing / operator-owned): no extras, hash digested.
  - Additionally, when the active context is dirty or `results_unsaved` and a study applies (6 or 7), flag
    **`campus_study_network_unsaved`** (the producer's `stale` compares the saved network, the IC values the
    in-memory one).
  - Refusals are raised as `FinanceRefused` **inside `build()`**, so they become refused reports like every adapter
    refusal and never precede the runner's own `finance_inputs_missing` 422. Flags are added with
    `dataclasses.replace(case, flags=case.flags + (...))` (`engine.py:161` carries case flags to the report).
- **R-2. Read once, at the seam.** The route reads the source once in the request, outside the network lock;
  `_ic_build_case(n, cfg, *, owner, lost_load, campus=None)` gains a keyword-only `campus` (default: none) that
  `build()` closes over, and the `assumptions` lambda closes over the same source's digest. `solve_ppa_price` reads
  the source the same way and passes it, so the solved PPA price and the report value the same case.
- **R-3. Money year.** Each asset's `meta.currency` / `meta.price_year` must exist and agree across assets. The
  currency must equal `fin.currency` always; the price year must equal `fin.currency_year` when that is stated
  (otherwise G2's `extra_asset_money_year_unstated` applies). Any failure → `campus_assets_money_year_mismatch`.
- **R-4. `extra_assets` staleness part.** Always present in `assumptions_digest` (keyword `extra_assets=None`):
  `None` when no study applies; otherwise a digest of `{source_hash, stale, flags, the assets without meta}`; on GET
  a source that cannot be read digests as `{"error": code}`, never raises (else the report would read
  `current_assumptions_not_established` for good). Removing a study then names `extra_assets` in `changed`.
- **F-1.** Validate first with `_real` (as today: bools and strings refused), **then** store `float(...)`:
  `quantity` via `object.__setattr__`, the part fields in `_extra_part`. An int and a float of the same value then
  hash alike. `derived_from_capital_cost` must be a real `bool` or `numpy.bool_`
  (`extra_asset_invalid:<name>:derived_from_capital_cost` otherwise); a part `name` must be a non-empty `str`
  (`extra_asset_invalid:<name>:parts`). Signatures unchanged (frozen facade test holds); the no-extras hash pin
  is unaffected.
- **F-2. `owner_assets` staleness part.** Digests **every** `value_flows.asset_owners` entry read from the raw
  commercial dict (GET does not know the POST's `owner`), without model parsing, never raising: per (component,
  name) the raw values of the §2 investment columns present on that component (`_opt` excluded except
  Transformer's), an absent asset digested as `missing`. Raw columns, not computed parts (computed parts read the
  config `discount_rate`, which would double-name `finance`). Read under `PyPSAService.get_lock()` inside the
  `assumptions` lambda (the worker does not hold it).
- **R-5. Hash stability.** The two new parts change every stored report's key once: each reads stale until re-run.
  This also closes S0b limit 4 (reports built before S0b read current). Stated in the findings note; no migration.
- **R-6. Tell the campus session** (one-shot message, after merge) that the IC route consumes
  `owner_assets_response`, that its shape is now a contract pinned by an IC test, and that unresolved campus needs
  are dropped by the producer without a flag (their call).

## 4. Work packages, tests first

### WP-R (R-1 … R-5)
Tests `tests/test_investment_case_campus_route.py` (fixtures copied or imported from
`test_campus_electrical_service.py:72-84, 814-829`: save the project through `POST /api/projects/{name}`, take the row
from `ctx.project_uuid`, then `fake_investment(row, [inv_row(...)])`):
- A hub project with a fake investment run: `POST /results/investment_case` → the report's `extra_assets` block
  carries the producer's names and `source_hash`; capex includes them; the PPA solve's case carries them too.
- Each R-1 branch: scratch context, no run dir (and `campus_electrical/` not created), engine absent with a run dir
  (`campus_study_unavailable`), non-hub, producer 422 and `ContractError` (`campus_assets_invalid`, a refused
  report), currency / year mismatch and missing `meta` (`campus_assets_money_year_mismatch`), stale (a manifest
  written, or through the seam: `campus_study_stale`, no extras), empty assets with a hash, unsaved network flag.
- Staleness: a new `source_hash` → GET stale with `extra_assets` in `changed`; the study removed → the same; a
  source error on GET → `{"error": …}` digest, no 500.
- The factory seam: `_ic_build_case` called without `campus` behaves as today.

### WP-F (F-1, F-2)
- `tests/test_finance_case_extra_assets.py`: int vs float values hash alike; `True` / `"5"` still refused;
  `derived_from_capital_cost="false"` refused, `numpy.bool_` accepted; empty or non-str part name refused.
- `tests/test_investment_case_runner.py` / `_routes.py`: changing an owner asset's `build_year`, a part cost, its
  `lifetime`, `max_hours` or `carrier` → stale with `owner_assets` in `changed`; a non-owner asset's columns → not;
  a solve change still names only `dispatch` (routes:498); a `discount_rate` config change names `finance` only
  (QA J, `qa_investment_case.py:1376`; routes:490).
- Pins to update deliberately (the parts list grows): `test_investment_case_runner.py:237, 346`,
  `test_investment_case_routes.py:437`, and routes:478-481 (a `value_flows` edit now also names `owner_assets`).
- Frontend: the fallback stale text in `InvestmentCaseView.tsx:365` names the new parts; `types.ts` unchanged.

## 5. Process

Plan review loop; tests first; an independent code reviewer until PASS; the gate (full backend suite, QA drivers,
vitest, tsc, a findings note, an independent assessor); one PR. The owner merges.

## 6. Review record

- **Plan round 1 (independent reviewer): FAIL**, 5 P1 / 4 P2 / 3 P3, all taken:
  P1-1 project resolution via the active context (§2, R-1 step 1); P1-2 the R-1 order, refusals as `FinanceRefused`
  in `build()`, every producer exception typed, `campus_study_unavailable`, empty assets with a hash; P1-3 the
  `campus` seam on `_ic_build_case` and the PPA path (R-2); P1-4 F-1 validates before casting; P1-5 F-2 over every
  `asset_owners` entry, raw columns incl. `max_hours` and `carrier`, `_opt` excluded, the pins listed; P2-6
  `extra_assets` always present, the lambda closes over the request's digest; P2-7 no directory creation, outside
  the lock, error digest on GET; P2-8 the producer's `stale` vs the in-memory network
  (`campus_study_network_unsaved`) and the test setup; P2-9 the currency always checked, `meta` missing or
  disagreeing refused; P3-10 flags via `replace`, frontend text; P3-11 the lock in the lambda; P3-12 unresolved
  needs → R-6 note, `numpy.bool_` accepted.
