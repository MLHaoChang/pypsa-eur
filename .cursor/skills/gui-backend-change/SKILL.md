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

## Where results arithmetic goes
The `/results/*` handlers in `routers/results.py` are thin: network lookup,
`_dispatch_ready` gate, `_state` reads, then a call into
`services/results/<name>.py::compute_<name>(...)`, which returns the payload or
`None` for 204. New results computation goes in `services/results/`, takes the
network and plain arguments, gets result frames through the `result_df`
keyword (never from router state), and gains a case in
`tests/test_results_seam.py`. Handler names and parameter lists are API —
`services/chat_tools.py` resolves them reflectively — and
`tests/test_results_facade_surface.py` fails if one changes. Nothing under
`services/results/` imports a router.

## Where compare arithmetic goes
The two `/compare-state` + `/results-summary` routes in `routers/compare.py`
are thin; the nine `_compute_*_summary` functions and their helpers live in
`services/compare/`. They take `(n, periods, is_multi, has_solve)` and return
pydantic models; the solver config and the LP-stage result lookup arrive as
keyword-only `cfg=` / `result_df=` where a function needs them, never from
router state. `routers.compare` re-exports the pure ones under their old names
and wraps the four that used to resolve state inline, so the nine tests that
call them positionally are untouched — `tests/test_compare_facade_surface.py`
fails if a name or a positional parameter list changes, and
`tests/test_compare_seam.py` fails if the wrapper and the service disagree.
Nothing under `services/compare/` imports a router.

## Where network helpers go
`routers/network.py` keeps its ~80 CRUD routes, the generic CRUD factory and
`_xlsx_response`. The pure helpers live in services and are re-exported from
the router, so existing imports are unchanged:

| module | owns |
|---|---|
| `services/user_timeseries.py` | the `_user_ts` store, its lock, and everything reading or writing it |
| `services/profile_shapes.py` | synthetic load / generator / link profile shapes, carrier classification |
| `services/network_geometry.py` | haversine, bus coordinates, impedance preview, length recompute |
| `services/transformer_rules.py` | transformer voltage validation / enrichment / type sanitisation |
| `services/snapshot_index.py` | `_build_period_multiindex` |

**Never rebind `_user_ts` or `_user_ts_lock`.** `services/chat_tools.py` imports
them by value inside a function and mutates in place; reassigning either would
leave the router and every importer holding different objects, with writes
going to different stores. `tests/test_network_facade_surface.py` fails on any
rebinding, and also fails if a CRUD helper is moved out — this phase's scope is
pinned, not conventional.

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
   and still boots — it only breaks mid-solve. It has caught a left-behind
   helper (`_safe_log`) and a left-behind annotated constant (`_CLS_TO_ATTR`)
   that a whole-suite run would have taken 35 minutes to reach.
5. Then run `python -m ruff check services routers` (the repo config, which
   enforces F401 here). Moving a body out of a module usually strands the
   imports it took with it.
6. A re-export façade belongs at the TOP of the module with the other imports.
   Module bodies execute top to bottom, so names re-exported at the bottom are
   not bound yet for any module-level dict or constant that references them.

## Commit
- Only commit when the user asks. Prefer small PRs (helpers → compare → results errors → CI).
