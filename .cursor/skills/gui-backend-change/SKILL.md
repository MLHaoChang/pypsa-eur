---
name: gui-backend-change
description: >-
  Checklist for safe changes to pypsa-gui FastAPI backend (routers, services,
  weighting, results errors, tests). Use when editing pypsa-gui/backend,
  adding endpoints, changing multi-period weighting, results serializers,
  or carrier catalog.
---

# GUI backend change checklist

## Before editing
1. Confirm scope is `pypsa-gui/backend/` (not `gui_streamlit/` unless asked).
2. Prefer `services/` over growing `routers/network.py`, `results.py`, `compare.py`.
3. Read existing helpers first: `period_utils.py`, `serialization.py`, `dispatch_status.py`.

## Weighting (multi-period)
- Use `snapshot_weights(n, column, sns=None)`, `period_years_map`, `years_for_period`.
- Energy KPIs → column `"generators"`; cost KPIs → `"objective"`.
- Do **not** re-inline `investment_period_weightings["years"]` in routers.

## Results / errors
- Unsolved or stale dispatch → 204 via `_not_solved()` / `_dispatch_ready()`.
- Unexpected exceptions → log + **500**, never mask as 204.
- Mutating-during-solve: structured `{ detail, code: "solver_in_flight" }`.

## Carriers
- If changing `services/carrier_catalog.py`, update `frontend/src/utils/carrierCatalog.ts` (or shared source).

## After editing
1. Run: `pixi run gui-tests` (or `python -m pytest` in `pypsa-gui/backend`).
2. For weighting changes: assert compare “Total energy” matches Results on a multi-period fixture if one exists.
3. Avoid drive-by refactors of `solver_service.py` / god routers.

## Commit
- Only commit when the user asks. Prefer small PRs (helpers → compare → results errors → CI).
