# Backend God-File Decomposition Implementation Plan

**Goal:** Cut `services/solver_service.py` from 5,783 lines to ~1,370 by carving six cohesive modules into a new `services/solver/` package, then lift the computation out of the three god routers into `services/` — without changing one line of behaviour or one import at any call site.

**Architecture:** `services/solver_service.py` stays the single import surface and becomes an orchestrator (`run_simulation`) plus `SolverConfig` plus an explicit re-export façade. The carved modules form a DAG that never imports back from `solver_service`, so no lazy function-body imports are needed. Routers keep their decorators, guards and error contracts; only the arithmetic underneath moves.

**Tech Stack:** Python 3.11 / FastAPI / PyPSA 1.1.2 / pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-backend-god-file-decomposition-design.md`

## Global Constraints

- **Branch is `claude/master-refactor-tdd-zga39h`.** Re-run `git branch --show-current` before every commit.
- **Strictly behaviour-preserving.** Same responses, same numbers, same log strings, same error codes. Defects found while reading go to `docs/superpowers/findings/` — never fixed in a refactor commit.
- **Never edit an existing test.** If a task appears to require it, the task is cut wrong. New tests are additive only.
- **Never change a signature**, including private ones, including where a better one is obvious.
- **`SolverConfig` does not move.** Carved modules annotate it as the string `"SolverConfig"`, which is never evaluated, so they need no import for it.
- **Carved modules never import from `services.solver_service`.** Dependencies flow one way. A task that seems to need a function-body import to break a cycle has the DAG wrong — re-cut it.
- **The canonical gate is `pixi run gui-tests` and it does NOT run here.** No pixi binary, no solved env. The substitute venv is `/tmp/claude-0/venv`, built from `requirements.txt` plus `pypsa==1.1.2 linopy==0.8.0 'pandas<3'`. Unpinned, pip takes pypsa 1.3.0 / pandas 3.0.5 and pandas 3 breaks PyPSA's `assign_solution` (`TypeError: Must pass list-like as names`), reddening twelve myopic tests spuriously. **Every reported run must name the venv and state it approximates the gate.**
- **The gate is "the failing set is unchanged", not "green".** The suite is not green in this environment. Diff the failing set against `/tmp/claude-0/baseline_failures.txt` after every task; a test moving in *either* direction stops the task.
- **Never pipe a test run into `tail`/`head`** — a pipeline reports only its last stage's exit status. Redirect to a file, echo `$?`, then read the file.
- **Path-limited `git commit <paths>`, never `git add -A`.** `resources/`, `logs/` and `benchmarks/` collect junk.
- **Never write to `pypsa-gui/backend/projects/`.**
- **One commit per task**, message naming the cluster and the line count moved.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `services/solver/__init__.py` | create | package marker; deliberately re-exports nothing |
| `services/solver/periodized_costs.py` | create | annuity, PV factors, periodized cost defaults |
| `services/solver/diagnostics.py` | create | infeasibility diagnosis + post-solve logging |
| `services/solver/objective.py` | create | `extra_functionality` wrappers, objective scaling |
| `services/solver/assumptions.py` | create | outages, MIP/presolve kwargs, modelling assumptions |
| `services/solver/myopic.py` | create | limited-foresight loop |
| `services/solver/runtime.py` | create | availability, abort, heartbeat, log handlers, `_safe_log` |
| `services/solver/vintage_store.py` | create | the per-thread freeze store both assumptions and myopic read |
| `services/solver_service.py` | modify | orchestrator + `SolverConfig` + re-export façade |
| `tests/test_solver_facade_surface.py` | create | the tripwire — every re-exported name |
| `services/results/` | create | Phase 2 — computation lifted from `routers/results.py` |
| `services/compare/` | create | Phase 3 — computation lifted from `routers/compare.py` |

---

## Task 0: The façade tripwire

Written first, before any code moves, because every later task depends on it to
fail loudly. It is the one test that can catch a forgotten re-export at the
seam rather than in whichever router imports the name next.

**Files:**
- Create: `pypsa-gui/backend/tests/test_solver_facade_surface.py`

- [x] **Step 1: Write the test**

Assert that every name the codebase imports out of `services.solver_service`
is still importable from it, and that each is the *same object* as the one in
its carved module once that module exists. Enumerate the names from the actual
call sites — 39 test modules, `routers/simulation.py`, `routers/results.py`,
`services/ac_pf_service.py`, `services/cost_totals.py`,
`services/validation_service.py`, `services/vintage_service.py`,
`services/asset_results/compute.py`, `routers/compare.py`.

- [x] **Step 2: Run it — it must pass on unmodified `master`**

This test is a tripwire, not a red-green driver: on `master` every name is
already there, so it passes. Its job starts at Task 1. A tripwire that is red
before the work begins cannot tell you when the work broke something.

- [x] **Step 3: Commit**

---

## The cut, verified before any code moved

The task order below is not the one this plan was first written with. Before
touching `solver_service.py` the cluster boundaries were checked by walking the
module's AST, mapping every top-level name (functions, classes **and** module
constants) to its cluster, and reporting every cross-cluster reference. That
found **three cycles** in the original cut:

| cycle | via | fix |
|---|---|---|
| `assumptions` ↔ `myopic` | `_frozen_vintage_store` (`:4791`), written by assumptions at `:4114,:4668` and by myopic at `:5007,:5049` | own leaf module `solver/vintage_store.py`, carrying `_frozen_vintage_local`, `_frozen_vintage_store` and `_MYOPIC_VINTAGE_SOURCE` |
| `objective` → `solver_service` | `_safe_log` (`:40`), used only by objective at `:2705–:3364` | goes to `solver/runtime.py`, where the log-queue plumbing lives |
| `assumptions` → `solver_service` | `_canonical_load_carrier_key` (`:55`) + its three `_LOAD_*` constants, used only at `:4340` | folded into `solver/assumptions.py`; `routers/results.py` keeps importing it from the façade |

A first pass missed the third because it walked only `FunctionDef`/`ClassDef`
and ignored module-level `Assign` nodes — which is how `_NOM_TRIPLES`,
`_MYOPIC_VINTAGE_SOURCE` and the `_LOAD_*` frozensets stayed invisible. Constants
create the same cycles functions do.

The corrected cut is acyclic, and its topological sort **is** the task order:

```
periodized_costs → vintage_store → assumptions → diagnostics
                                 → runtime → objective → myopic → solver_service
```

Tasks are sequenced so that every module's dependencies already exist when it
is written. `tests/test_solver_facade_surface.py::test_carved_modules_never_import_back_from_solver_service`
enforces the property from here on.

---

## Task 1: `solver/periodized_costs.py` (279 lines) — lines 3427–3705

Goes first: it is the only cluster with **no** outgoing dependency at all.
Across its 279 lines it references only `math`, `pandas`, `numpy`, `Callable`,
`contextmanager` and its own three helpers, and every `SolverConfig` annotation
is already a string. `_annuity` living here is also what unblocks diagnostics.

**Moves:** `_annuity`, `_reference_build_year`, `_pv_factor_series`,
`fill_periodized_cost_defaults`, `with_periodized_cost_defaults`,
`periodized_capital_costs`.

**External importers:** `routers/results.py`, `routers/compare.py`,
`services/cost_totals.py`, `services/asset_results/compute.py`,
`tests/qa_cost_decomp_overnight.py`, `tests/qa_results_summary_compare.py`,
`tests/golden/fixture.py`, `tests/test_results_range.py`,
`tests/test_compare_cross_surface.py`.

- [x] **Step 1: Flip the six names in `_FACADE_ORIGINS` (red)** — fails on `ModuleNotFoundError`.
- [x] **Step 2: Create the package; move lines 3427–3705 verbatim.** `services/solver/__init__.py` re-exports nothing: the façade is `solver_service`, and a second export surface would let call sites drift onto the package.
- [x] **Step 3: Wire the façade.**
- [x] **Step 4: Tripwire green, then full suite; failing set unchanged.**
- [x] **Step 5: Commit.**

---

## Task 2: `solver/vintage_store.py` (42 lines) — lines 4769–4810

Tiny, and it exists only to break the `assumptions` ↔ `myopic` cycle. Both
clusters read the same per-thread freeze store, so it belongs to neither.

**Moves:** `_frozen_vintage_local`, `_frozen_vintage_store`,
`_MYOPIC_VINTAGE_SOURCE`, with the comment block explaining why the store is
thread-local (B4's per-context solver locks let two `run_simulation` calls run
concurrently; a process-wide dict raced and produced silent wrong vintage
capacities). Needs `import threading`.

**Watch:** `services/vintage_service.py:587` imports `_frozen_vintage_store`
from `solver_service` **inside a function body**, with a comment naming the
`solver_service ↔ vintage_service` cycle it avoids. That lazy import stays
exactly as it is — this task does not get to reopen it, and the façade keeps
it working.

- [x] **Steps 1–5** as Task 1.

---

## Task 3: `solver/assumptions.py` (1,050 lines) — lines 3706–4702, plus 28–37 and 55–80

**Moves:** `resolve_branch_outages`, `_compute_loss_atol`,
`_resolve_mip_kwargs`, `_resolve_presolve_kwargs`, `_normalise_dynamic_indexes`,
`_clear_dispatch_fix`, `_sanitise_transformer_types`,
`_apply_modelling_assumptions`, plus `_PRESOLVE_KEYS_BY_SOLVER`,
`_MIP_KEYS_BY_SOLVER`, `_DISPATCH_FIX_ACCESSORS`, `_NOM_TRIPLES` — and the
load-carrier canonicaliser (`_LOAD_ELECTRICAL_ALIASES`, `_LOAD_HEAT_TOKENS`,
`_LOAD_HYDROGEN_TOKENS`, `_canonical_load_carrier_key`), whose only in-module
consumer is `_apply_modelling_assumptions` at `:4340`.

**Watch:** `services/ac_pf_service.py` imports `_DISPATCH_FIX_ACCESSORS` and
`_normalise_dynamic_indexes` **from `solver_service`**. Leave it pointing at the
façade — repointing it is a behaviour-neutral tidy-up that would put a fourth
file in this task's diff.

**External importers:** `services/ac_pf_service.py`,
`services/validation_service.py`, `routers/results.py`
(`_canonical_load_carrier_key`), `tests/test_myopic_build_period_visibility.py`.

- [x] **Steps 1–5** as Task 1.

---

## Task 4: `solver/diagnostics.py` (1,158 lines) — lines 1497–2654

The biggest single win in the file and the lowest-risk: it writes log lines. It
reads the solved network and formats strings onto a queue — it never mutates the
network, never touches the LP, never influences a served result. A defect here
is visible in the solver log and nowhere else.

**Moves:** `_diagnose_infeasibility`, `_log_global_constraint_shadow_prices`,
`_log_curtailment_post_solve`, `_log_storage_post_solve`,
`_log_sclopf_post_solve`, `_log_capacity_summary_post_solve`,
`_log_line_post_solve`, `_log_corridor_summary_post_solve`,
`_log_bus_balance_post_solve`, `_log_cost_decomposition_post_solve`,
`_emit_core_post_solve_diagnostics`.

**Interface:** five entry points only — the four post-solve emitters plus
`_diagnose_infeasibility`. Outward it needs `_annuity` (Task 1) and
`_period_utils`. `_per_period_split` is nested at `:2396` and travels with its
parent.

**External importers:** `tests/test_infeasibility_diagnosis.py`.

- [x] **Steps 1–5** as Task 1.

---

## Task 5: `solver/runtime.py` (341 lines) — lines 344–671, plus 40–52

**Moves:** `check_solver_availability`, `_async_raise_in_thread`,
`_AbortWatcher`, `_SolveHeartbeat`, `SolveAborted`, `_check_stop`,
`_RollingWindowFailureCatcher`, `_ThreadScopedQueueHandler`, and `_safe_log`
from the head of the file.

**Watch:** `SolveAborted` is caught by `except SolveAborted` in
`run_simulation`. Re-export identity matters — a second class object would make
that handler stop catching. The tripwire asserts `is`, not `hasattr`, for
exactly this.

**External importers:** `tests/test_solve_queue.py`, `tests/test_qa_step0a.py`,
`tests/test_myopic_feasibility.py`, `routers/simulation.py`.

- [x] **Steps 1–5** as Task 1.

---

## Task 6: `solver/objective.py` (772 lines) — lines 2655–3426

Higher risk than the rest: these wrappers compose the `extra_functionality`
callable handed to `n.optimize()`, and `_rescale_results_for_objective` rewrites
result magnitudes. A defect here moves numbers the frontend serves. Check
`tests/test_objective_conditioning.py` and `tests/qa_objective_scale.py`
specifically, not just the suite total.

**Moves:** `_wrap_with_capex_budget`, `_wrap_with_curtailment_cost`,
`_objective_conditioning`, `_log_objective_conditioning`,
`_wrap_with_objective_scale`, `_rescale_results_for_objective`, and the
`_COND_*` constants at `:3095–3097`. Depends on `_safe_log` (Task 5).

- [x] **Steps 1–5** as Task 1.

---

## Task 7: `solver/myopic.py` (1,146 lines) — lines 4703–4768 and 4811–5783

Last, because it sits at the top of the DAG: it depends on runtime,
diagnostics, objective, assumptions and vintage_store. Also the highest-risk
cluster — it mutates capacities between iterations.

Twelve of the twenty baseline failures are in tests of this code, all traced to
the pandas-3 drift rather than to the code itself. Read the baseline diff here
carefully rather than glancing at a count.

**Moves:** `_build_iteration_snapshots`, `_clear_myopic_build_periods`,
`_record_myopic_build_period`, `_freeze_period_capacities`,
`_capture_extendable_p_nom_opt_to_frozen_store`, `_defer_future_vintage_builds`,
`_outages_active_in_period`, `_run_myopic_foresight`,
`_patch_passive_branch_holes`.

**External importers:** `tests/test_cost_totals_contract.py`,
`tests/test_myopic_horizon_cost.py`, `tests/test_myopic_feasibility.py`,
`tests/test_myopic_build_period_visibility.py`, `tests/qa_myopic_sclopf.py`,
`tests/qa_myopic_future_vintage_defer.py`.

- [x] **Steps 1–5** as Task 1.

---

## Task 8: Verify the shape — done

Checked against `master`, not asserted:

- [x] `solver_service.py` is **1,191 lines, down from 5,783** (target was ~1,370).
- [x] **No carved module imports `solver_service`** — `grep` over `services/solver/*.py` returns nothing but docstring prose.
- [x] **Zero new function-body imports.** An AST count of imports inside function bodies gives 28 before and 28 after, and the multiset difference is empty: the DAG removed the cycles rather than deferring them the way `ac_pf_service.py` had to.
- [x] **Nothing lost.** All 70 top-level definitions in `master`'s `solver_service.py` are still reachable; the set difference is empty in both directions once `master`'s own function-body imports are accounted for.
- [x] **No call site changed.** `git diff master --name-status` lists 12 files: 2 docs, 8 new modules, `solver_service.py`, and the one added test. Not a single existing test, router, or sibling service.
- [x] `.cursor/skills/gui-backend-change/SKILL.md` updated — it now names the seven modules, states the two rules that hold the layout together, and points its "avoid drive-by refactors" line at this spec as the deliberate alternative.

### Final shape

| module | lines |
|---|---|
| `services/solver_service.py` | 1,191 |
| `services/solver/diagnostics.py` | 1,190 |
| `services/solver/myopic.py` | 1,105 |
| `services/solver/assumptions.py` | 1,050 |
| `services/solver/objective.py` | 785 |
| `services/solver/runtime.py` | 363 |
| `services/solver/periodized_costs.py` | 306 |
| `services/solver/vintage_store.py` | 63 |
| `services/solver/__init__.py` | 15 |

### Verification runs

All in `/tmp/claude-0/venv` (pinned `pypsa==1.1.2`, `linopy==0.8.0`, `pandas<3`),
which **approximates** `pixi run gui-tests` — this session has no pixi.

| point | collected | passed | failed | skipped |
|---|---|---|---|---|
| baseline (`master`) | 2,306 | 2,282 | 2 | 22 |
| after Task 1 | 2,341 | 2,317 | 2 | 22 |
| after Tasks 2–4 | 2,350 | 2,326 | 2 | 22 |
| after Tasks 5–7 | 2,362 | 2,338 | 2 | 22 |

The failing set is byte-identical at every point: `test_app_paths.py`'s two
macOS `Library/Application Support` assertions, which cannot pass on Linux.

### What actually went wrong, and what caught it

Task 3's line range for the load-carrier block silently swallowed `_safe_log`,
sending it to `assumptions.py` and leaving five callers in `solver_service`
referring to a name that was no longer there. Python binds globals at call
time, so the module still imported, the app still booted, and the tripwire
still passed — it would have broken inside a real solve.

That is why `test_no_call_site_was_left_behind_by_a_move` exists. It runs ruff
F821 over `services/` and `routers/` and was confirmed to catch the defect by
reintroducing it and watching it fail. On the final batch it earned its place
again, flagging six `"pypsa.Network"` annotations in `objective.py` in seconds
rather than through a 35-minute suite run.

The lesson for Phases 2–4: **verify extraction boundaries by AST, never by line
range.** Every cut in this plan was validated by AST beforehand — which is how
the three cycles were found before any code moved — but the line ranges used to
*perform* the cuts were still hand-derived, and that is where the one real
defect came from.

## Phase 2 — `routers/results.py` (4,106 lines) — done

Design decisions are in the spec's "Phase 2 addendum"; this is the record of
what was done and how it was proven.

### Tasks

- [x] **Task 9: tripwire** — `tests/test_results_facade_surface.py`. Pins the
  28 handler names and their positional parameter lists (a snapshot of
  `master`), because `services/chat_tools.py` resolves handlers with `getattr`
  by name and then inspects `__code__.co_varnames` for `"source"`; the three
  helpers other modules import by name; the handler → `compute_*` map; and the
  layering rule that nothing under `services/results/` imports a router. Red
  first on `ModuleNotFoundError`.
- [x] **Task 10: seam test** — `tests/test_results_seam.py`. On the solved
  golden network, calls every lifted handler AND its `compute_*` with what the
  handler passes it, and asserts JSON-identical payloads (or 204 ↔ `None`).
  One case passes a plain `getattr` lambda as `result_df` to prove the
  arithmetic runs with no router state at all. Red first.
- [x] **Task 11: `wants_slice`** → `services/serialization.py`, beside
  `slice_ts`; router re-imports it under the old alias. Its `isinstance(int)`
  guard is what lets handlers be called directly with `Query` sentinels, so it
  had to move with the bodies.
- [x] **Task 12: the lift** — twelve handlers and the two shared load-frame
  helpers, by tool, not by hand.
- [x] **Task 13: verify.**

### What moved where

| handler | compute function | lines |
|---|---|---|
| `get_asset_economics` | `services/results/asset_economics.py` | 903 |
| `get_cost_breakdown` | `services/results/cost_breakdown.py` | 538 |
| `get_emissions` | `services/results/emissions.py` | 354 |
| `get_prices`, `get_price_drivers` | `services/results/prices.py` | 347 |
| `get_lcoh` | `services/results/lcoh.py` | 339 |
| `get_carrier_kpis` | `services/results/carrier_kpis.py` | 269 |
| `lp_scaled_load_frame`, `corrected_marginal_prices` | `services/results/load_frames.py` | 185 |
| `get_curtailment` | `services/results/curtailment.py` | 154 |
| `get_line_duals` | `services/results/line_duals.py` | 137 |
| `get_unit_commitment` | `services/results/unit_commitment.py` | 124 |
| `get_statistics` | `services/results/statistics.py` | 50 |
| `get_load_results` | `services/results/loads.py` | 48 |

`routers/results.py`: **4,106 → 1,113 lines.** Every handler is still there
with its decorator, docstring, signature, gate comments and `_state` reads;
each is now five to eight lines.

**Deferred to Phase 3**, because they compose router-level things:
`get_objective_decomposition` (calls the `get_cost_breakdown` handler and
checks its return for a 204), `get_losses_summary` (lazy-imports
`_build_snapshot_weights` from `routers.compare`), `get_economics_by_carrier`
(delegates to `routers.compare._compute_economics_summary`).

### How the cut was proven

The tool (`lift_results.py`, kept out of the repo — it ran once) locates the
three HTTP-layer statements as AST nodes, removes them by exact span with the
comment block above each carried into the regenerated handler, applies the
four rewrites as text so comments and formatting survive, then **re-parses the
result and asserts its AST equals the AST produced by the same four rewrites
as `NodeTransformer` passes over the original.** All fourteen lifts passed
that proof. Text edit for fidelity; structural check for correctness. That is
the Phase 1 lesson — boundaries by AST, never by line range — applied to the
cut itself, not only to its validation.

One deliberate hand edit after the tool: `lcoh.py` inherited a function-body
`from routers.compare import _build_snapshot_weights`, which the layering
test caught. That helper is a documented thin wrapper over
`services.period_utils.snapshot_weights`, which `lcoh` already imports, so
the call became `snapshot_weights(n, "generators")` — the identical call one
frame down.

### Verification runs

All in the pinned pip venv (`pypsa==1.1.2`, `linopy==0.8.0`, `pandas<3`),
which **approximates** `pixi run gui-tests`.

| point | collected | passed | failed | skipped |
|---|---|---|---|---|
| end of Phase 1 | 2,362 | 2,338 | 2 | 22 |
| end of Phase 2 | 2,423 | 2,399 | 2 | 22 |

Failing set byte-identical to the pre-refactor baseline: `test_app_paths.py`'s two macOS-path assertions, which cannot pass on Linux. The sixty-one new collected cases are the two Phase 2 test files.

### What the seam makes possible (and was NOT done here)

A 903-line asset-economics computation reachable only through a solve-and-GET
is now `compute_asset_economics(n, cfg, result_df=...)`. The next unit test
for a cost bug is a network and a call, not a fixture and an HTTP client. Those
tests are deliberately not part of this refactor — it proves itself against
the suite that existed before it.

## Phase 3 — `routers/compare.py` (2,781 lines) — done

Design decisions are in the spec's "Phase 3 addendum"; this is the record.

### Tasks

- [x] **Task 14: tripwire** — `tests/test_compare_facade_surface.py`. Pins the
  23 names other modules import from `routers.compare` **with their positional
  parameter lists**, because nine tests call the summaries positionally as
  `CMP._compute_x(n, periods, is_multi, has_solve)`. Distinguishes pure
  re-exports (router name IS the service object) from wrappers (router name is
  a thin function that resolves state). Red first.
- [x] **Task 15: seam test** — `tests/test_compare_seam.py`. Router-level name
  vs service-level name on the solved golden network, identical pydantic
  payloads, for all nine summaries plus `_periodized_lookup`. One case builds
  `SolverConfig` by hand and reads prices off the network to prove the engine
  runs with no router state at all. Red first.
- [x] **Task 16: the move** — 20 functions into `services/compare/`.
- [x] **Task 17: the Phase 2 follow-up** — the three `/results` handlers that
  were deferred because they reached into `routers.compare`.
- [x] **Task 18: verify.**

### What moved where

| module | lines | contents |
|---|---|---|
| `services/compare/economics.py` | 636 | `_compute_economics_summary` |
| `services/compare/capacity.py` | 388 | capacity summary + annuitised CAPEX |
| `services/compare/dispatch.py` | 242 | dispatch summary |
| `services/compare/support.py` | 237 | bucketing, PV objects, `_periodized_lookup`, `_safe_capital_cost`, `_build_snapshot_weights`, `_CLS_TO_ATTR` |
| `services/compare/prices.py` | 224 | prices summary |
| `services/compare/storage_cycling.py` | 203 | storage cycling |
| `services/compare/curtailment.py` | 198 | curtailment |
| `services/compare/lost_load.py` | 176 | lost load |
| `services/compare/loading.py` | 171 | line/transformer loading |
| `services/compare/emissions.py` | 116 | emissions |

`routers/compare.py`: **2,781 → ~500 lines** — the two routes, the
`_read_lost_load_capture` reader, and the façade.

Sixteen of the twenty functions moved **byte for byte**. Four were rewritten to
take state as keyword arguments and proven by AST equality against the same
change expressed as a `NodeTransformer`. `_periodized_lookup` is the one
hand-written body (it now takes `cfg` instead of resolving it), covered by the
seam test.

### The Phase 2 follow-up

With the engine in `services/compare/`, the three deferred handlers lifted:

| handler | destination | why it was blocked |
|---|---|---|
| `get_losses_summary` | `services/results/losses.py` | lazy-imported `_build_snapshot_weights` from `routers.compare`; now a plain service import |
| `get_economics_by_carrier` | `services/results/economics_by_carrier.py` | delegated to `routers.compare._compute_economics_summary`; now calls the service with `cfg` and `result_df` |
| `get_objective_decomposition` | `services/results/objective_decomposition.py` | called the `get_cost_breakdown` **handler** and checked for a 204 `Response`. The router still makes that call, inside the try that used to wrap it, and passes the result in as `cost_breakdown`; the `isinstance(cb, dict)` check is unchanged and covers a payload, a `Response` or `None` alike |

`get_losses_summary` also needed `_state.get("ac_pf_results") is not None`,
which is a different state key from `solver_config` — it becomes the explicit
`has_ac_pf_snapshot` parameter rather than a general mechanism.

### What the F821 guard caught, again

Two defects, both the Phase 1 bug class recurring:

1. **`_CLS_TO_ATTR` was left behind.** A module-level constant whose four call
   sites all moved. The lift tool's name scan handled `ast.Assign` but not
   `ast.AnnAssign`, and `_CLS_TO_ATTR: dict[str, str] = {...}` is annotated —
   so it was invisible, no import was generated, and no error was raised. It
   is the only annotated module-level assignment in the file. **Phase 1's
   post-mortem said constants create the same cycles functions do; the sharper
   statement is that a name scan must cover every binding form.**
2. **Three return annotations referenced models imported only inside the
   function bodies** (`CurtailmentComparison`, `LostLoadComparison`,
   `StorageCyclingComparison`). Harmless at runtime under
   `from __future__ import annotations`, but unresolvable names. Now imported
   at module level.

Both were found in seconds by `ruff --select F821`, not by a 35-minute suite.

### Dead imports

The repo's ruff config enforces F401 on `pypsa-gui/**`, and `master`'s
`compare.py`, `results.py` and `solver_service.py` all pass it clean. After the
moves, 30 imports across 6 files were dead — including six in
`services/solver_service.py` and five in the Phase 1 `services/solver/`
modules, whose generated headers had imported more than their bodies used.
All removed; nothing re-imports any of them through those modules.

One new function-body import crept into the router façade
(`SolverConfig` inside `_live_solver_config`). The spec forbids adding lazy
imports, so it was hoisted to module level: function-body imports are 35
before and 35 after, difference empty.

### Verification runs

Pinned pip venv (`pypsa==1.1.2`, `linopy==0.8.0`, `pandas<3`), which
**approximates** `pixi run gui-tests`.

| point | collected | passed | failed | skipped |
|---|---|---|---|---|
| end of Phase 1 | 2,362 | 2,338 | 2 | 22 |
| end of Phase 2 | 2,423 | 2,399 | 2 | 22 |
| end of Phase 3 | 2,481 | 2,457 | 2 | 22 |

Failing set byte-identical to the pre-refactor baseline: `test_app_paths.py`'s
two macOS-path assertions, which cannot pass on Linux. The fifty-eight new
collected cases are the two Phase 3 test files.

Nothing lost: all 25 top-level names on `master`'s `compare.py` and all 37 on
`results.py` are still reachable.

## Phase 4 — `routers/network.py` (4,169 lines) — done

Design decisions are in the spec's "Phase 4 addendum"; this is the record.

**The earlier assessment was wrong.** This plan called `network.py` the weakest
candidate and flagged it to reassess. Reassessed by AST rather than by eye, the
CRUD half is exactly as described — but underneath it sat **1,283 lines of pure
helper code in four clusters**, none touching router state. That is not a
weak candidate; the eyeball estimate simply never looked past the routes.

### Tasks

- [x] **Task 19: tripwire** — `tests/test_network_facade_surface.py`. Pins all
  49 moved names with an identity assertion, pins the 11 CRUD/HTTP helpers that
  must NOT move (so a later phase cannot quietly widen this one), enforces the
  layering rule, and statically forbids rebinding `_user_ts` / `_user_ts_lock`.
  Red first on `ModuleNotFoundError`.
- [x] **Task 20: the extraction** — four clusters plus one shared leaf.
- [x] **Task 21: verify.**

### What moved where

| module | lines | contents |
|---|---|---|
| `services/user_timeseries.py` | 874 | the `_user_ts` store, its lock, and the 14 functions that read or write it |
| `services/profile_shapes.py` | 352 | synthetic load / generator / link profile shapes + carrier classification |
| `services/network_geometry.py` | 167 | haversine, bus coordinates, impedance preview, length recompute |
| `services/transformer_rules.py` | 99 | voltage validation, voltage enrichment, type sanitisation |
| `services/snapshot_index.py` | 34 | `_build_period_multiindex` — used by the routes AND by `user_timeseries`, so it belongs to neither |

`routers/network.py`: **4,169 → 2,811 lines.** The ~80 CRUD routes, their
factory, `_meta_payload`, `_push_undo_snapshot`, `_apply_profile_upload` and
`_xlsx_response` stay, deliberately.

### The first phase with no rewrites at all

Every cluster was a **pure move**: all 49 definitions are byte-identical to
`master`, verified by comparing each block's source text rather than asserting
it, and the router re-exports the identical objects. There was nothing to
prove by AST equality because nothing was transformed.

### What the guards caught

1. **The façade was in the wrong place.** Appended at the end of the router, it
   left `_LOAD_SHAPES` — a module-level dict literal partway down the file that
   references two moved profile functions — unbound at execution time. It
   failed loudly with a `NameError` at import, and the fix is general: a
   re-export façade is an import block and belongs at the top with the others.
2. **Two dead imports** (`NamedTuple`, a function-body `threading`) stranded by
   the moves, caught by the repo's ruff config. One remaining finding,
   `D204` on `_RecomputeResult`'s class docstring, exists on `master` too and
   travelled with the byte-identical move; it is left alone rather than trading
   the move's strongest property for a blank line this phase did not introduce.

### The `_user_ts` hazard

`services/chat_tools.py` imports `_user_ts` and `_user_ts_lock` by value inside
a function and mutates the dict — guarded by a comment reading *"only fails if
routers/network refactor breaks paths"*. Re-exporting a dict is sound only
while nothing rebinds it. `master` never does, and the tripwire now enforces
that statically in both the router and the service. Same class of hazard as
Phase 1's `run_simulation` monkeypatch: a name that must stay one object.

### Verification runs

Pinned pip venv (`pypsa==1.1.2`, `linopy==0.8.0`, `pandas<3`), which
**approximates** `pixi run gui-tests`.

| point | collected | passed | failed | skipped |
|---|---|---|---|---|
| end of Phase 1 | 2,362 | 2,338 | 2 | 22 |
| end of Phase 2 | 2,423 | 2,399 | 2 | 22 |
| end of Phase 3 | 2,481 | 2,457 | 2 | 22 |
| end of Phase 4 | 2,596 | 2,572 | 2 | 22 |

Failing set byte-identical to the pre-refactor baseline: `test_app_paths.py`'s
two macOS-path assertions, which cannot pass on Linux. The 115 new collected
cases are the Phase 4 tripwire.

Nothing lost: all 146 top-level names on `master`'s `network.py` are still
reachable. Function-body imports: 32 before, 32 after, difference empty.

---

## Where the decomposition stands

| module | master | now |
|---|---|---|
| `services/solver_service.py` | 5,783 | 1,185 |
| `routers/network.py` | 4,169 | 2,811 |
| `routers/results.py` | 4,106 | 911 |
| `routers/compare.py` | 2,781 | 473 |
| **total** | **16,839** | **5,380** |

11,459 lines moved into 25 focused service modules across four phases, with the
failing set byte-identical at every gate and not one call site changed.

### What is deliberately left

`routers/network.py`'s ~80 CRUD routes and their factory. They are short,
uniform, and reached through a generic factory already; splitting them buys
file length and costs greppability. If that file is worked on again, the
question worth asking is whether the *routes* need decomposing at all, or
whether 2,811 lines of thin CRUD is simply what 80 endpoints look like.
