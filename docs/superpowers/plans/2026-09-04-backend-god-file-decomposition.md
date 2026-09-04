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
| `services/solver/runtime.py` | create | availability, abort, heartbeat, log handlers |
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

## Task 1: `solver/periodized_costs.py` (279 lines)

Goes first because `_annuity` lives here and Task 2 needs to import it from a
module that is not `solver_service`.

The cluster is a true leaf. Across lines 3427–3705 it references only `math`,
`pandas`, `numpy`, `Callable`, `contextmanager` and its own three helpers — and
every `SolverConfig` annotation is already a string.

**Moves:** `_annuity`, `_reference_build_year`, `_pv_factor_series`,
`fill_periodized_cost_defaults`, `with_periodized_cost_defaults`,
`periodized_capital_costs`.

**External importers to keep working:** `routers/results.py`
(`_pv_factor_series`, `_reference_build_year`, `periodized_capital_costs`,
`with_periodized_cost_defaults`), `routers/compare.py`, `services/cost_totals.py`,
`services/asset_results/compute.py`, `tests/qa_cost_decomp_overnight.py`,
`tests/qa_results_summary_compare.py`, `tests/golden/fixture.py`,
`tests/test_results_range.py`, `tests/test_compare_cross_surface.py`.

- [x] **Step 1: Extend the tripwire (red)**

Add the six names to the façade test with their `services.solver.periodized_costs`
origin. It fails on `ModuleNotFoundError` — the new module does not exist yet.

- [x] **Step 2: Create the package and move the block verbatim**

`services/solver/__init__.py` re-exports nothing: the façade is
`solver_service`, and a second export surface would let call sites drift onto
the package and defeat the whole design.

Move lines 3427–3705 **unchanged** — same bodies, same order, same comments,
same signatures. Add the module docstring documenting the seam, in the house
style of `ac_pf_service.py`.

- [x] **Step 3: Wire the façade**

`from services.solver.periodized_costs import (...)` in `solver_service.py`,
with a comment saying why the import exists (re-export for callers, not use).

- [x] **Step 4: Tripwire green, then full suite**

Failing set must equal the baseline exactly.

- [x] **Step 5: Commit**

---

## Task 2: `solver/diagnostics.py` (1,158 lines)

The biggest single win in the file and the lowest-risk: it writes log lines. It
reads the solved network and formats strings onto a queue — it never mutates
the network, never touches the LP, never influences a served result.

**Moves:** `_diagnose_infeasibility`, `_log_global_constraint_shadow_prices`
(1497–1655) and the whole post-solve family (1656–2654):
`_log_curtailment_post_solve`, `_log_storage_post_solve`,
`_log_sclopf_post_solve`, `_log_capacity_summary_post_solve`,
`_log_line_post_solve`, `_log_corridor_summary_post_solve`,
`_log_bus_balance_post_solve`, `_log_cost_decomposition_post_solve`,
`_emit_core_post_solve_diagnostics`.

**Interface into the rest of `solver_service`:** four call sites only —
`_emit_core_post_solve_diagnostics`, `_log_cost_decomposition_post_solve`,
`_log_sclopf_post_solve`, `_log_global_constraint_shadow_prices`.

**Outward dependencies:** `_annuity` (now `solver.periodized_costs`) and
`_period_utils`. `_per_period_split` is nested at `:2396` and travels with its
parent.

**External importers:** `tests/test_infeasibility_diagnosis.py`.

- [ ] **Step 1: Extend the tripwire (red)**
- [ ] **Step 2: Move the two blocks verbatim**
- [ ] **Step 3: Wire the façade**
- [ ] **Step 4: Tripwire green, then full suite; failing set unchanged**
- [ ] **Step 5: Commit**

---

## Task 3: `solver/objective.py` (772 lines)

Higher risk than Tasks 1–2: these wrappers compose the `extra_functionality`
callable handed to `n.optimize()` and `_rescale_results_for_objective` rewrites
result magnitudes. A defect here moves numbers the frontend serves. Verify
against `tests/test_objective_conditioning.py` and `tests/qa_objective_scale.py`
specifically, not just the suite total.

**Moves (2655–3426):** `_wrap_with_capex_budget`, `_wrap_with_curtailment_cost`,
`_objective_conditioning`, `_log_objective_conditioning`,
`_wrap_with_objective_scale`, `_rescale_results_for_objective`, and the
`_COND_*` constants at 3095–3097.

**External importers:** `tests/test_objective_conditioning.py`
(`_objective_conditioning`).

- [ ] **Step 1: Extend the tripwire (red)**
- [ ] **Step 2: Move verbatim, constants included**
- [ ] **Step 3: Wire the façade**
- [ ] **Step 4: Full suite; failing set unchanged**
- [ ] **Step 5: Commit**

---

## Task 4: `solver/assumptions.py` (997 lines)

**Moves (3706–4702):** `resolve_branch_outages`, `_compute_loss_atol`,
`_resolve_mip_kwargs`, `_resolve_presolve_kwargs`, `_normalise_dynamic_indexes`,
`_clear_dispatch_fix`, `_sanitise_transformer_types`,
`_apply_modelling_assumptions`, plus `_PRESOLVE_KEYS_BY_SOLVER`,
`_MIP_KEYS_BY_SOLVER`, `_DISPATCH_FIX_ACCESSORS`.

**Watch:** `services/ac_pf_service.py` imports `_DISPATCH_FIX_ACCESSORS` and
`_normalise_dynamic_indexes` **from `solver_service`**. Those imports must keep
working through the façade — do not repoint `ac_pf_service` at the new module.
Leaving it on the façade is deliberate: repointing it is a behaviour-neutral
tidy-up that would put a fourth file in this task's diff.

**External importers:** `services/ac_pf_service.py`,
`services/validation_service.py` (`resolve_branch_outages`),
`tests/test_myopic_build_period_visibility.py` (`_apply_modelling_assumptions`).

- [ ] **Step 1: Extend the tripwire (red)**
- [ ] **Step 2: Move verbatim**
- [ ] **Step 3: Wire the façade**
- [ ] **Step 4: Full suite; failing set unchanged**
- [ ] **Step 5: Commit**

---

## Task 5: `solver/myopic.py` (1,081 lines)

Highest-risk cluster in the file: it mutates capacities between iterations and
owns the frozen-vintage store. Twelve of the twenty baseline failures live in
tests of this code (all traced to the pandas-3 drift, not to the code) — read
the baseline diff carefully here rather than glancing at a count.

**Moves (4703–5783):** `_build_iteration_snapshots`, `_frozen_vintage_store`,
`_clear_myopic_build_periods`, `_record_myopic_build_period`,
`_freeze_period_capacities`, `_capture_extendable_p_nom_opt_to_frozen_store`,
`_defer_future_vintage_builds`, `_outages_active_in_period`,
`_run_myopic_foresight`, `_patch_passive_branch_holes`, plus `_NOM_TRIPLES` and
`_MYOPIC_VINTAGE_SOURCE`.

**Watch:** `services/vintage_service.py:587` imports `_frozen_vintage_store`
from `solver_service` inside a function body, with a comment naming the
`solver_service ↔ vintage_service` cycle it avoids. That lazy import stays as
it is — this task does not get to reopen it.

**External importers:** `tests/test_cost_totals_contract.py`,
`tests/test_myopic_horizon_cost.py`, `tests/test_myopic_feasibility.py`,
`tests/test_myopic_build_period_visibility.py`,
`tests/qa_myopic_sclopf.py`, `tests/qa_myopic_future_vintage_defer.py`,
`services/vintage_service.py`.

- [ ] **Step 1: Extend the tripwire (red)**
- [ ] **Step 2: Move verbatim**
- [ ] **Step 3: Wire the façade**
- [ ] **Step 4: Full suite; failing set unchanged**
- [ ] **Step 5: Commit**

---

## Task 6: `solver/runtime.py` (328 lines)

**Moves (344–671):** `check_solver_availability`, `_async_raise_in_thread`,
`_AbortWatcher`, `_SolveHeartbeat`, `SolveAborted`, `_check_stop`,
`_RollingWindowFailureCatcher`, `_ThreadScopedQueueHandler`, and `_safe_log`
from the head of the file.

**Watch:** `SolveAborted` is an exception type caught by `except SolveAborted`
in `run_simulation`. Re-export identity matters — a second class object would
make the handler stop catching. The tripwire's `is` assertions cover this,
which is why they are `is` and not `hasattr`.

**External importers:** `tests/test_solve_queue.py`, `tests/test_qa_step0a.py`,
`tests/test_myopic_feasibility.py`, `routers/simulation.py`
(`check_solver_availability`).

- [ ] **Step 1: Extend the tripwire (red)**
- [ ] **Step 2: Move verbatim**
- [ ] **Step 3: Wire the façade**
- [ ] **Step 4: Full suite; failing set unchanged**
- [ ] **Step 5: Commit**

---

## Task 7: Verify the shape

- [ ] `wc -l services/solver_service.py` reports ~1,370, down from 5,783.
- [ ] No carved module imports `solver_service` — `grep -rn "solver_service" services/solver/` returns only docstring prose.
- [ ] No new function-body import was added to break a cycle.
- [ ] `git diff master --stat` shows zero changed lines in `tests/` other than the added `test_solver_facade_surface.py`.
- [ ] Update `.cursor/skills/gui-backend-change/SKILL.md`: its "avoid drive-by refactors of `solver_service.py`" line should now point at `services/solver/` for where new solver code belongs.

---

## Phase 2 — `routers/results.py` (4,106 lines)

Deferred behind Phase 1 deliberately: Phase 1 changes no call sites, while
Phase 2 changes the body of every handler it touches, and the two should not
share a review.

Lift computation into `services/results/`, leaving thin handlers. The HTTP layer
— decorator, status codes, `_not_solved()` / `_dispatch_ready()` guards, the
`{detail, code: "solver_in_flight"}` contract — **does not move**.

| handler | lines | destination |
|---|---|---|
| `get_cost_breakdown` | 207–734 (527) | `services/results/cost_breakdown.py` |
| `get_emissions` | 1781–2136 (355) | `services/results/emissions.py` |
| `get_lcoh` | 977–1319 (342) | `services/results/lcoh.py` |
| `get_carrier_kpis` | 1515–1780 (265) | `services/results/carrier_kpis.py` |
| `get_prices`, `get_price_drivers` | 2452–2800 (348) | `services/results/prices.py` |
| `get_curtailment`, `get_lost_load` | 2801–2984 (183) | `services/results/curtailment.py` |
| `get_losses_summary` | 1364–1514 (150) | `services/results/losses.py` |

Unlike Phase 1 this genuinely enables new tests: a 527-line cost computation
reachable only through a solve-and-GET becomes callable directly. Those tests
are **not** part of the refactor commits — the refactor proves itself against
the existing suite, and new unit tests land afterwards.

## Phase 3 — `routers/compare.py` (2,781 lines)

Nine `_compute_*_summary` functions behind two routes, into `services/compare/`
— one module per summary. `_compute_economics_summary` alone is 607 lines.
Shared helpers (`_bucket_add`, `_to_pv`, `_build_snapshot_weights`,
`_safe_capital_cost`, `_periodized_lookup`) go to `services/compare/support.py`.

The weighting discipline in `.cursor/skills/gui-backend-change/SKILL.md` is
load-bearing here: energy KPIs use column `"generators"`, cost KPIs use
`"objective"`. Moving code must not silently re-inline
`investment_period_weightings["years"]`.

## Phase 4 — `routers/network.py` (4,169 lines)

Weakest candidate, sequenced last. ~90 near-identical CRUD routes that are
individually short — long but not deep, so the win per line moved is smaller.
The extractable parts are the non-CRUD helpers: `_haversine_km`, `_bus_coord`,
`_line_haversine_km`, `_impedance_preview`, `_recompute_lengths_for_bus`
→ `services/geometry.py`; `_xlsx_response`, `_build_period_multiindex`
→ `services/serialization.py` (which already exists); the transformer
voltage/type helpers → `services/transformer_rules.py`.

Reassess after Phases 1–3 land whether this earns its diff at all.
