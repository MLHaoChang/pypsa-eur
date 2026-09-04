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

## Where solver code goes
`services/solver_service.py` is now `run_simulation`, `SolverConfig`, and a
re-export façade — the solver layer lives in `services/solver/`:

| module | owns |
|---|---|
| `runtime.py` | availability, abort watcher, heartbeat, log handlers, `_safe_log` |
| `periodized_costs.py` | annuity, PV factors, periodized cost defaults |
| `vintage_store.py` | the per-thread freeze store |
| `assumptions.py` | outages, MIP/presolve kwargs, transient modelling assumptions |
| `diagnostics.py` | infeasibility analysis, post-solve logging |
| `objective.py` | `extra_functionality` wrappers, objective scaling |
| `myopic.py` | the limited-foresight driver |

Two rules hold this together:
- **Keep importing from `services.solver_service`.** It is the single import
  surface; 40-plus call sites use it and `tests/test_solver_facade_surface.py`
  fails if a name stops being re-exported. Do not repoint call sites at
  `services/solver/` and do not add exports to its `__init__.py`.
- **Never import `solver_service` from inside `services/solver/`.** Dependencies
  run one way down a DAG (`runtime`/`periodized_costs`/`vintage_store` →
  `assumptions`/`diagnostics`/`objective` → `myopic`). If new code seems to need
  a back-import, it is in the wrong module. The same test enforces this.

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
3. Avoid drive-by refactors of `solver_service.py` / god routers — decompose
   them deliberately, with a spec and a plan, not inside a feature change.
   `docs/superpowers/specs/2026-09-04-backend-god-file-decomposition-design.md`
   is the worked example; `routers/results.py`, `compare.py` and `network.py`
   are still on the list.
4. Run the undefined-name guard after moving code between modules:
   `python -m ruff check --isolated --select F821 services routers`. Python
   binds globals at call time, so a caller left behind by a move still imports
   and still boots — it only breaks mid-solve.

## Commit
- Only commit when the user asks. Prefer small PRs (helpers → compare → results errors → CI).
