# Backend god-file decomposition — design

**Date:** 2026-09-04
**Status:** approved, ready for planning
**Scope:** decompose `pypsa-gui/backend`'s four largest modules — `services/solver_service.py`, `routers/results.py`, `routers/network.py`, `routers/compare.py` — into cohesive modules behind unchanged import surfaces. Strictly behaviour-preserving. `gui_streamlit/` and the upstream `scripts/` tree are out of scope.

## Problem

Four backend modules carry 16,839 lines between them:

| module | LOC | shape of the problem |
|---|---|---|
| `services/solver_service.py` | 5,783 | 57 top-level definitions; six unrelated concerns in one file |
| `routers/network.py` | 4,169 | ~90 CRUD routes plus geometry/serialisation helpers |
| `routers/results.py` | 4,106 | 33 route handlers, several of them 300–530 lines of computation |
| `routers/compare.py` | 2,781 | nine `_compute_*_summary` functions behind two routes |

The cost is not aesthetic. `solver_service.py` mixes the solve orchestrator with
~1,000 lines of post-solve *logging*, ~1,000 lines of myopic foresight, ~1,000
lines of modelling-assumption mutation, and ~770 lines of `extra_functionality`
wrappers. A reader chasing a dispatch bug pages through curtailment log
formatting to get there, and every one of those concerns is edited by a
different class of change.

The repository already knows this. `.cursor/skills/gui-backend-change/SKILL.md`
instructs: *"Prefer `services/` over growing `routers/network.py`, `results.py`,
`compare.py`."* Its closing line — *"Avoid drive-by refactors of
`solver_service.py` / god routers"* — is a guard against **unplanned** churn
inside a feature change, not a permanent freeze. This document is the planned
alternative that line points at.

## The constraint that defines the design

**Forty-plus call sites import private names out of `solver_service`.**

```
tests/test_qa_step0a.py:247            _ThreadScopedQueueHandler
tests/test_solve_queue.py:296,341      _SolveHeartbeat, _AbortWatcher
tests/test_infeasibility_diagnosis.py  _diagnose_infeasibility, _log_global_constraint_shadow_prices
tests/test_myopic_feasibility.py       _RollingWindowFailureCatcher, _run_myopic_foresight
tests/qa_myopic_future_vintage_defer.py _defer_future_vintage_builds, _freeze_period_capacities
tests/qa_myopic_sclopf.py              _outages_active_in_period
tests/test_objective_conditioning.py   _objective_conditioning
tests/qa_cost_decomp_overnight.py      _annuity
services/ac_pf_service.py              _DISPATCH_FIX_ACCESSORS, _normalise_dynamic_indexes
routers/results.py                     _pv_factor_series, _reference_build_year
```

Thirty-nine test modules import from `services.solver_service`. Any decomposition
that relocates these names breaks the very suite that is supposed to prove the
decomposition safe — the refactor would be verified by a suite it had just
rewritten. That is the failure mode this design exists to avoid.

**So `services/solver_service.py` stays as the single import surface.** It
becomes a thin orchestrator plus an explicit re-export façade over a new
`services/solver/` package. No test, router, or sibling service changes a single
import line. Behaviour preservation stops being an assertion and becomes
structural: if the façade is complete, callers cannot observe the move.

### Precedent

This is the third carve-out in this backend, and it follows the two that
already landed:

- `services/ac_pf_service.py` — *"Carved out of `services/solver_service.py`"*,
  documenting its one-way import discipline in the module docstring.
- `routers/results.py` — *"the ~33 read-only /results/\* serializers now live in
  `routers/results.py`"*, split out of `routers/simulation.py`.
- `services/asset_results/` — a package split into
  `compute` / `export` / `registry` / `applicability` / `service`.

The house style is therefore established: **carve into a module or package,
document the seam in the docstring, keep imports flowing one way.**

## Improving on the precedent

`ac_pf_service.py` imports three names *back* from `solver_service`, and
`solver_service` imports `run_ac_pf_stage` from it **lazily, inside a function
body**, with a docstring explaining that this is what averts an import cycle.
That works, but a lazy import is a cycle deferred, not a cycle removed.

This design does not repeat it. The carved modules **never import from
`solver_service`**. Dependencies flow strictly one way, down a DAG:

```
solver/runtime.py         (log/abort/heartbeat plumbing)   ← leaf
solver/periodized_costs.py (annuity, PV factors, defaults) ← leaf
        ↑
solver/diagnostics.py     (post-solve logging)  → periodized_costs, period_utils
solver/objective.py       (extra_functionality wrappers)   → runtime
solver/assumptions.py     (pre-solve network mutation)     → runtime
solver/myopic.py          (limited-foresight loop)  → diagnostics, assumptions
        ↑
services/solver_service.py  (run_simulation + SolverConfig + façade)
```

Every arrow points up exactly once. There is no cycle to defer, so no carved
module needs a function-body import.

`SolverConfig` **stays in `solver_service.py`**. Every function that takes one
annotates it as the *string* `"SolverConfig"`:

```python
def periodized_capital_costs(n, cfg: "SolverConfig") -> dict[...]:
```

String annotations are never evaluated at runtime, so the carved modules type
against `SolverConfig` without importing it. Moving the dataclass would buy
nothing and would put the most widely imported name in the backend into the
blast radius of every task.

## Cluster map — `solver_service.py`

Line ranges are against `1120635`, the current tip of `master`.

| lines | LOC | cluster | destination | interface into the rest |
|---|---|---|---|---|
| 1–80 | 80 | imports, load-carrier canonicalisation | stays | — |
| 81–343 | 263 | `SolverConfig` | **stays** | the config object |
| 344–671 | 328 | availability, abort, heartbeat, log handlers | `solver/runtime.py` | 8 names |
| 672–1443 | 772 | `run_simulation` | **stays** — the orchestrator | — |
| 1444–1496 | 53 | log tail, user-code gate | stays | — |
| 1497–1655 | 159 | infeasibility diagnosis, shadow prices | `solver/diagnostics.py` | 2 names |
| 1656–2654 | 999 | post-solve diagnostic logging | `solver/diagnostics.py` | 4 names |
| 2655–3426 | 772 | `extra_functionality` wrappers, objective scaling | `solver/objective.py` | 6 names |
| 3427–3705 | 279 | annuity, PV factors, periodized cost defaults | `solver/periodized_costs.py` | 6 names |
| 3706–4702 | 997 | outages, MIP/presolve kwargs, modelling assumptions | `solver/assumptions.py` | 8 names |
| 4703–5783 | 1,081 | myopic foresight loop | `solver/myopic.py` | 9 names |

Projected result: **5,783 → ~1,370 lines** in `solver_service.py`, six focused
modules averaging 570 lines, and not one changed import at any call site.

### Why the post-solve diagnostics go first

The `_log_*_post_solve` family is the cleanest seam in the file. Across its 999
lines it reaches outside itself exactly twice — `_annuity` and
`_period_utils` — and the rest of `solver_service` calls into it through only
four entry points:

```
_emit_core_post_solve_diagnostics(network, sns, current_period, phase)
_log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase)
_log_sclopf_post_solve(network, sns, current_period, iter_outages, phase)
_log_global_constraint_shadow_prices(network, log_queue)
```

`_per_period_split`, the one helper the block shares internally, is a *nested*
function at `:2396` — it moves with its parent and constrains nothing.

It is also the lowest-risk cluster in the file: it writes log lines. It reads
the solved network and formats strings onto a queue; it never mutates the
network, never touches the LP, and never influences a result the frontend
serves. A defect introduced here is visible in the solver log and nowhere else.
Extracting the largest, most isolated, least dangerous cluster first is what
makes the second extraction cheap — `_annuity` has to leave first, which is why
`periodized_costs` is Task 1 and diagnostics is Task 2.

## Routers — the different problem

The routers are not "too many helpers". They are **computation living inside
route handlers**:

| handler | lines | LOC |
|---|---|---|
| `results.get_cost_breakdown` | 207–734 | 527 |
| `results.get_emissions` | 1781–2136 | 355 |
| `results.get_lcoh` | 977–1319 | 342 |
| `results.get_carrier_kpis` | 1515–1780 | 265 |
| `compare._compute_economics_summary` | 1554–2161 | 607 |
| `compare._compute_capacity_summary` | 521–794 | 273 |

None of it is reachable except through HTTP. A 527-line cost-breakdown
computation can only be tested by standing up a project, solving it, and
issuing a GET — so in practice it is tested that way or not at all.

Moving these bodies into `services/results/` and `services/compare/` makes the
arithmetic callable directly, which is what lets the next person write a unit
test for a cost bug instead of a fixture and a solve. The route handler keeps
its decorator, its status codes, its `_not_solved()` / `_dispatch_ready()`
guards and its error contract — **the HTTP layer does not move**, only the
arithmetic underneath it.

`routers/network.py` is the weakest candidate of the four and is sequenced
last. Its 4,169 lines are ~90 near-identical CRUD routes that are individually
short; the file is long but not deep, and the win is smaller per line moved.

## Verification

**The canonical gate is `pixi run gui-tests`.** It cannot run in the cloud
session this work started in: there is no `pixi` binary and no solved
environment. The substitute is a pip venv built from
`pypsa-gui/backend/requirements.txt` **plus the repo's own pins** —
`pypsa==1.1.2`, `linopy==0.8.0`, `pandas<3` — because an unpinned resolve takes
`pypsa 1.3.0` and `pandas 3.0.5`, and pandas 3 breaks PyPSA's
`assign_solution` with `TypeError: Must pass list-like as names`, reddening
twelve myopic tests that have nothing wrong with them.

Any run in that venv is an **approximation of the gate, not the gate**, and
must be reported as such. Results from it are quoted with the venv named.

Two invariants gate every task:

1. **The failing-test set does not change.** Not "the suite is green" — it is
   not green in this environment, and pretending otherwise would hide a
   regression inside a pre-existing failure. The baseline set is captured
   before the first edit and diffed after every task. A test that changes state
   in *either* direction stops the task.
2. **The façade stays complete.** A dedicated test asserts every name listed in
   §"The constraint that defines the design" is importable from
   `services.solver_service`, so a forgotten re-export fails loudly at the seam
   instead of quietly in whichever router imports it next.

## What this design refuses to do

- **No behaviour changes.** Same responses, same numbers, same log strings,
  same error codes. Defects found while reading are written to
  `docs/superpowers/findings/` and left alone.
- **No test rewrites.** If a task requires editing an existing test, the task
  is wrong. The only new tests are the façade tripwire and characterisation
  tests, both of which are additive.
- **No signature changes**, including private ones. `_log_line_post_solve` keeps
  its argument order even where a better one exists.
- **No `SolverConfig` relocation** — see above.
- **No touching `gui_streamlit/` or `scripts/`.** The Streamlit tree is frozen
  by policy; `scripts/` is upstream PyPSA-Eur, where a fork-local refactor buys
  a conflict on every future upstream merge.
- **No lazy function-body imports** to paper over a cycle. If a task needs one,
  the DAG is wrong and the task gets re-cut.

---

## Phase 2 addendum — lifting `routers/results.py` (written 2026-09-04, before the cut)

Phase 1 moved code between modules and changed no call site. Phase 2 cannot
make that claim: the body of every lifted handler changes, because the body
today reads live router state from inside the arithmetic. These are the
decisions that keep the change mechanical and auditable anyway.

### The handler keeps the HTTP layer; the service gets the arithmetic

Every computation handler has the same skeleton:

```python
n = PyPSAService.get_network()
if not _dispatch_ready(n):
    return _not_solved()            # 204
cfg = _state["solver_config"]
... several hundred lines ...
```

The first three statements are the HTTP layer — state acquisition and the
freshness gate — and they stay in the handler, verbatim, with their comments.
Everything else moves to `services/results/<name>.py::compute_<name>(...)`.
The handler becomes:

```python
n = PyPSAService.get_network()
if not _dispatch_ready(n):
    return _not_solved()
payload = compute_cost_breakdown(n, _state["solver_config"])
return _not_solved() if payload is None else payload
```

### `None` is the "no content" sentinel

Handler bodies return `_not_solved()` — a 204 `Response` — from the middle of
the computation, sometimes inside nested `try` blocks. A service must not
return an HTTP response, so each mid-body `return _not_solved()` becomes
`return None` and the handler maps `None` back to 204.

`None` rather than an exception, deliberately: several of those returns sit
inside `try: ... except Exception: return _not_solved()`. Raising a sentinel
exception from there would be caught by the enclosing handler and take a
different branch. A `return` is a `return` whatever encloses it, so the
control flow is identical. No handler today returns bare `None` as a payload,
which is what makes the sentinel unambiguous — verified by AST over every
`Return` node, nested defs excluded.

### `_result_df` is injected, not imported

`_result_df` prefers the LP-stage snapshot in `routers.simulation._state` over
the live network. A service function must not reach into router state, and
`services/asset_results/compute.py` already importing `_result_df` lazily from
`routers.results` is a layering violation this phase does not add to.

So every service function that needs it takes `result_df` as a keyword-only
parameter and the handler passes `_result_df`. In the moved body the only
change is the name: `_result_df(...)` → `result_df(...)`. A test can pass
`lambda n, acc, attr, src: getattr(getattr(n, acc), attr)` and exercise the
arithmetic on any network with no router, no state and no solve.

The two shared helpers `lp_scaled_load_frame` and `corrected_marginal_prices`
move to `services/results/load_frames.py` with the same `result_df` keyword.
`routers/results.py` keeps same-signature wrappers that pass `_result_df`, so
`routers/compare.py` and `services/asset_results/compute.py` — which import
them by name from the router — are untouched.

### Other `_state` reads become arguments

`get_lcoh` reads `_state.get("solver_config") or SolverConfig()` mid-body;
`get_load_results` passes `_state.get("solver_config")` inline into a call.
Both become a `cfg` parameter evaluated in the handler with the identical
expression. `_state.get` has no side effects and nothing between the gate and
the read depends on `cfg`, so hoisting is semantics-preserving.

### The logger keeps its name

`logger.exception("results endpoint failed; returning 204 …")` lives inside
several moved bodies. Service modules create
`logging.getLogger("pypsa_gui.results")` — the same logger, not a child — so
log records are byte-identical.

### `_wants_slice` moves to `services/serialization.py`

It is the `Query`-sentinel-aware companion of `slice_ts`, which already lives
there. The router imports it back under its old alias.

### What Phase 2 does NOT lift, and why

- `get_objective_decomposition` calls the `get_cost_breakdown` *handler* and
  inspects its return for a 204 `Response`. Lifting it means either a service
  calling a router or a rewrite of that check. Deferred.
- `get_losses_summary` imports `_build_snapshot_weights` from
  `routers/compare.py`. That helper moves to `services/compare/` in Phase 3;
  the lift waits for it.
- `get_economics_by_carrier` delegates entirely to
  `routers.compare._compute_economics_summary`. Phase 3.
- `get_lost_load` and `get_ac_pf_status` are state readers with no arithmetic.
- The eleven `_serve_ts` wrappers are already six lines each.

### How the cut is proven

The lesson from Phase 1 is applied: **boundaries by AST, never by line range.**
The lift tool locates the three HTTP-layer statements as AST nodes, removes
them by their exact line spans, applies the four rewrites above as text
edits, then **re-parses the result and asserts its AST equals the AST produced
by applying the same four rewrites as `NodeTransformer` passes over the
original function.** Comments and formatting survive because the edit is
textual; correctness is proven because the check is structural.

On top of that, `tests/test_results_seam.py` calls each handler and its
`compute_*` on the solved golden network and asserts JSON-identical output —
the seam is transparent or the test says where it is not.

### Layering rule, stricter than Phase 1

Nothing under `services/results/` imports anything under `routers/`.
`tests/test_results_facade_surface.py` enforces it.
