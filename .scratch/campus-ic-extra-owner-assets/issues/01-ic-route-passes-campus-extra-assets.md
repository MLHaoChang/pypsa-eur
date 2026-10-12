# The investment-case route passes the campus equipment, refuses a stale study, and goes stale with it

Status: ready-for-agent
Type: task
Blocked by: none (the producer and `build_finance_case(extra_assets=)` are on master)

Read [the spec](../spec.md) first, especially "Open ownership question": this edits IC-owned files
(`pypsa-gui/backend/routers/results.py` IC P4 section, `services/finance/investment_case_runner.py`). If you are
not the IC session, confirm with the owner before you start.

## What to build

In `POST /results/investment_case` (`routers/results.py::post_investment_case`), **in the request, before
`start_investment_case`** (the same place the `not_solved` 409 is raised, so a refusal never starts a worker):

1. **Find the project.** The session's network belongs to `PyPSAService.get_loaded_project()` (a name, or None
   when unbound). Resolve it to its `db.models.Project` row under the acting user's access (look at how
   `routers/campus_electrical.py::_row` and the project routes resolve and authorize a project; do not bypass
   the org ACL). Unbound, not found, or not a capacity-expansion project (`services.gridspine_service.kind_of`):
   no campus assets; carry on exactly as today.
2. **Read the campus side once**: `body = campus_electrical_service.owner_assets_response(project)`. An
   `HTTPException(503)` (a build without gridspine) means no campus assets. Never call it for a project of another
   kind (it answers 409).
3. **No assets** (`body["assets"] == []`): carry on exactly as today (`extra_assets=()`, same assumptions hash as
   before this change).
4. **Refuse a stale study** (owner default B): when `body["stale"]` is true, `409` with
   `{"code": "campus_study_stale", "message": "the campus electrical study changed since it ran (campus file, network,
   asset library or cost basis): run it again in the campus electrical panel, then build the case"}`.
   409, not 422: the inputs are valid, the state is not current (the route's `not_solved` is a 409 for the same
   reason).
5. **Money.** Each entry's `meta.currency` / `meta.price_year` is the campus library's; the case's are
   `FinanceInputs.currency` / `currency_year` (`models.finance`, from `cfg.finance`). When the currency differs, or
   `currency_year` is stated and differs from `price_year`: `422` with `{"code": "campus_money_mismatch", "message":
   ..., "campus": {"currency", "price_year"}, "case": {"currency", "currency_year"}}`. **Refuse, do not convert**:
   there is no escalation index for these costs, and the campus side deliberately applies none (findings Known limit
   2 allows either; refusing adds no new assumption; say so in the docstring). When `currency_year` is not stated,
   pass the assets through: `build_finance_case` already flags `extra_asset_money_year_unstated` and softens the
   report text.
6. **Build** `extras = tuple(ExtraOwnerAsset(**{k: d[k] for k in fields}) for d in body["assets"])` (fields of
   `services.finance.case.ExtraOwnerAsset`; `meta` is dropped). A `ValueError("extra_asset_invalid:...")` is a 422
   naming it. Pass `extra_assets=extras` through `_ic_build_case(n, cfg, owner=..., lost_load=...)` (add a keyword)
   into `build_finance_case`.
7. **Staleness of the report.** Extend `_ic_assumptions(cfg, n)` and
   `services.finance.investment_case_runner.assumptions_digest` with a `campus` part, **present only when there are
   campus assets** (so every existing report keeps its hash, as `FinanceCase.extra_assets` is hashed only when
   non-empty): `{"source_hash": body["source_hash"], "study_method": body["study_method"], "stale": body["stale"]}`.
   `_ic_current_assumptions` (used by `GET /results/investment_case`) must compute the same part, so a report goes
   stale (with `"campus"` in `changed`) when the study is rerun, its library or rate changes, it goes stale, or the
   source switches between least cost and MILP. The GET route has no DB session today; add what is needed to resolve
   the project there too, or record the project id with the run and re-read it.

Optional, recommended: `GET /results/investment_case` returns `campus: {"study_method", "source_hash", "assets":
count}` beside `report`, so the Investment tab (issue 02) can say what the case took without opening the report.

## Files

- IC-owned, edited here: `pypsa-gui/backend/routers/results.py` (IC P4 section only),
  `pypsa-gui/backend/services/finance/investment_case_runner.py` (`assumptions_digest`), their tests.
- Campus-owned, **read only** here: `pypsa-gui/backend/services/campus_electrical_service.py`. Need a change there?
  Ask the campus session; do not edit it.
- Do not change `services/finance/case.py` or `services/results/finance_case.py` unless a test below shows a need.

## Acceptance tests (write them first)

Use the campus test fixtures: `tests/test_campus_electrical_service.py::fake_investment`, `inv_row`,
`purchase_rows`, `hub` (a hub project with a hand-made investment run, real files in a tmp project dir), and for
the MILP `tests/test_campus_electrical_ic_source.py::MilpEngine` with `run_milp` (a fake joint optimisation that
writes its own table). Patch the solve with the existing IC route fixtures rather than solving for real where
possible.

1. A loaded hub project with a fresh campus investment run: the case is built with one `ExtraOwnerAsset` per
   entry (names `campus:<id>#<k>`), the report's `extra_assets` block lists them, and capex rises by Σ quantity ×
   upfront_per_unit (× contingency if the inputs set one).
2. No loaded project, a project of another kind, a hub project without a campus run, a run with `invest` off:
   `extra_assets` is empty and the assumptions hash equals the hash before this change (pin one).
3. A stale campus study (save a changed campus file, or change the project's discount rate in its solver config):
   409 `campus_study_stale`, no worker started, no record left (`GET /results/investment_case` unchanged).
4. Currency or price year differing from the case's: 422 `campus_money_mismatch` naming both; `currency_year`
   unset: built, flagged `extra_asset_money_year_unstated`.
5. After a case is built: rerun the campus study on another library → `GET /results/investment_case` reports
   `stale: true` with `"campus"` in `changed`. Same for a fresh MILP finishing (study_method switches to `milp`,
   the hash changes), and for the campus going stale.
6. With a fresh MILP: the case's extra assets are the MILP's rows (`meta.study_method == "milp"` on the producer
   side) and the digest part carries `study_method: "milp"`.
7. The campus side is called once per request (patch `owner_assets_response` with a counter) and never for a
   project of another kind.

## Done when

The tests above pass, the route docstrings state the refusals and the digest part, the IC plan/findings get an
as-built note, and the gates of the IC session pass (backend `tests/test_finance_*`, `tests/test_investment_case*`,
`tests/test_campus_*`, the QA drivers that cover the IC route).

## Comments
