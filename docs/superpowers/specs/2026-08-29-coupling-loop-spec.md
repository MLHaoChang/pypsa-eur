# Coupling-loop engine spec (Phase 7) — v1, BINDING

Workers implement THIS document. Rationale and review findings live in the plan
(`docs/superpowers/plans/2026-08-29-fmea-phase7-coupling-loop.md`, v2 — the [B*]/[S*]/[N*]
tags there); where the two disagree, this spec wins and the master is told.
Amendments to this spec are recorded at the bottom, never silently.

## 1. Prerequisites (separate commits, land before the controller)

### 1.1 Solved-capacity semantics (`copt._firm_capacity`, `mc._storage_capacity`)

For a row that is **extendable** (`p_nom_extendable` / `p_nom_extendable`-equivalent
truthy) on a network whose frame HAS a finite `p_nom_opt` value for it:
`p_nom_opt` is authoritative INCLUDING 0.0. `p_nom` remains the fallback only for
non-extendable rows, missing column, or non-finite values. Same rule in both
functions; generators and storage alike.
★ tests (bite: restore the `> 0` fallthrough): extendable gen `p_nom=100`,
`p_nom_opt=0.0` → absent from `fleet_and_residual` units; extendable storage
`p_nom=50`, `p_nom_opt=0.0` → absent from `snapshot_inputs(...).storage`; and
`p_nom_opt=37.5` → capacity 37.5, not 100.

### 1.2 Superset fleet (`keep_zero_capacity`)

`fleet_and_residual(n, *, keep_zero_capacity=False)` and
`snapshot_inputs(n, *, vre_assets=(), keep_zero_capacity=False)` (kwarg threaded
through). With `True`: occurrence-bearing generators that clear every scope test
except `cap > 0` are INCLUDED as units at `capacity_mw=0.0`; storage rows likewise
at `p_nom_mw=0.0` (`e_nom_mwh=0.0`). Zero-capacity units draw their outage chains
normally (they consume their positional substream) and contribute 0 MW; zero
storage is dispatch-inert. Default `False` changes NOTHING anywhere (COPT, MC
study, ELCC, candidates — all existing tests and the pinned benchmark anchors must
pass untouched).
★ test (bite: revert to dropping cap ≤ 0 under the flag): two otherwise-identical
networks, one extendable unit at `p_nom_opt=0` vs `p_nom_opt=80`; with
`keep_zero_capacity=True` and the same seed, an untouched unit's per-draw
availability path is bit-identical across the two (compare the sampled arrays, not
the metrics).

### 1.3 Frontier restore exception-safety (`run_frontier_sweep`)

The closing base re-solve moves into `try/finally`; an exception mid-sweep still
attempts the restore, the returned/failed record carries `base_restored` truthfully
(False when the restore itself failed). ★ test: a `solve_at` that raises on point 2
→ restore attempted (call recorded), `base_restored` reflects reality.

## 2. Controller — `services/adequacy/coupling.py`

```python
MAX_LOOP_SOLVES = 8
EPS_FLOOR_PERMYRIAD = 0.01          # hard backstop only
ENERGY_FLOOR_MWH = 1.0              # the real stopping floor, from report cap_mwh

run_coupling_loop(
    solve_at,      # (eps: float) -> SolveResult (dict, below)
    evaluate,      # () -> (plan_hash: str, metrics: dict)
    *,
    target_lole_h: float,
    eps0: float,
    max_solves: int,
    stop_event=None,
    on_iteration=None,   # callable(iterate_row_dict) after each iterate completes
) -> dict
```

`SolveResult`: `{"status", "condition", "cost_eur", "ens_mwh", "cap_mwh",
"binding", "report"}` — solve failures surface as status/condition, never raise.
`evaluate` raising is a loop failure (status `"failed"`, restore still the route's
job).

Rules (normative, each with a ★ bitten test per the plan §4):
- Iterate 0 at `eps0`; met (mean lole_hours ≤ target) → status `met`, stop.
- Tightening step: `eps_next = max(min(eps/4, 0.5*eps*achieved/cap), floor_step)`
  where `achieved/cap` uses the just-returned `ens_mwh`/`cap_mwh` (guard div-by-0:
  fall back to eps/4), clamped so `eps_next > 0` ALWAYS (the ≤0 sentinel disables
  the cap entirely — assert, never solve with it).
- Evaluation skip: if `binding != "system_cap"` AND the plan hash equals a
  previously evaluated iterate's → reuse that iterate's metrics, `plateau: True`.
  A differing hash is ALWAYS evaluated regardless of cost. (Hash source: §3.)
- Infeasible (`"infeasible" in str(condition).lower()`): stop tightening (nested
  feasibility); never evaluate; if no met iterate exists by then → `unreachable`.
  Any other non-optimal outcome: record `solve_status`/`condition`, `mc: null`,
  do NOT conclude unreachability, continue tightening if budget remains.
- Floor: after an evaluated iterate whose `cap_mwh < ENERGY_FLOOR_MWH` still
  misses → `unreachable`. `eps` never goes below `EPS_FLOOR_PERMYRIAD`.
- Budget: solves (not evaluations) count against `max_solves`; exhausted →
  `budget_exhausted` with best VERIFIED met iterate as `final` (None if none met).
- Refinement: log-ε bisection between tightest evaluated miss and loosest met;
  stop when the midpoint's plan hash equals the met endpoint's, or budget spent.
  `final` = cheapest VERIFIED met iterate overall.
- `confident` = final's `lole_ci[1] <= target`.
- `stop_event.is_set()` checked before each solve → status `"aborted"` (iterates
  so far kept; `final` = best verified met so far or None).
- `on_iteration(row)` called after each iterate row completes (evaluated, reused,
  or failed). The controller never touches `_state` — the route owns storage.

Iterate row: `{"eps_permyriad", "solve_status", "condition", "cost_eur",
"ens_mwh", "cap_mwh", "binding", "plateau": bool,
"mc": {engine: "mc", fidelity: "sequential_mc", lole_hours, lole_ci, eue_mwh,
       eue_ci, n_samples, by_period} | None}`.

Result: `{"status": "met"|"unreachable"|"budget_exhausted"|"aborted"|"failed",
"iterations": [rows], "final": row|None, "confident": bool,
"eps_star": float|None, "solves_used": int}`.

## 3. The route's bindings (routers/results.py)

- `solve_at(eps)` = `dataclasses.replace(cfg, ens_cap_permyriad=eps)` +
  `sweep._solve_once` into a sink; extract cost/ens/cap/binding from the sink's
  adequacy report exactly as `run_frontier_sweep` does.
- `evaluate()` = `snapshot_inputs(n, keep_zero_capacity=True)` under the
  PyPSAService lock, then **`mc_adequacy(inputs, draws=N, seed=S, max_draws=N)`**
  (the exact call; `max_draws=N` is what pins the sample count — merely ignoring
  cov_target leaves the adaptive 2000 cap in play and n_samples drifts, breaking
  CRN comparability). Plan hash = sha256 over the sorted
  `(name, capacity_mw)` unit vector, the `(name, p_nom_mw, e_nom_mwh)` storage
  vector, and `inputs.residual.tobytes()`.
- `GET /results/coupling_loop`: 204 before any run; else the record, thread and
  stop-event stripped. Mid-run GETs see a CONSISTENT, growing `iterations` list:
  `on_iteration` REBINDS (`record["iterations"] = record["iterations"] + [row]`)
  under the solver state lock — never in-place append (shallow-copied GET must not
  see a torn list). Worker closes over its record (post_mc's pattern; post_frontier's
  in-thread `_state` access is the anti-pattern — do not copy it).
- `POST /results/coupling_loop` body
  `{target_lole_h, draws?, seed?, eps0?, max_solves?, restore?}`. Defaults:
  draws 500, seed 0, eps0 = cfg's current `ens_cap_permyriad` or 100.0 when unset,
  max_solves = MAX_LOOP_SOLVES, restore "base".
  Synchronous 422s: `target_lole_h` missing or ≤ 0; VoLL ≤ 0 (frontier's message
  style); nothing to sample (mc's message); `draws` outside [1, MAX_DRAWS];
  `max_solves` outside [1, MAX_LOOP_SOLVES]; `restore` not in {"base","final"};
  `cfg.solve_strategy` (field name per SolverConfig — check) in
  {"rolling","myopic"} with a message naming the strategy (otherwise every iterate
  fails validation and the loop mis-reports `unreachable`);
  `target_lole_h < min positive weight / draws` (the up-front resolution floor)
  with the message naming the draws that would resolve it.
  409 mesh: refuse while solve/sweep/frontier/mc/coupling_loop runs; AND register
  `coupling_loop` in post_fmea_sweep, post_frontier, post_mc guards. **Mesh
  hole fixes (required, cross-module):** post_fmea_sweep gains the missing
  `_study_running("frontier")` guard; `POST /simulation/run` (routers/simulation.py,
  both solve entrypoints if two exist) refuses with 409 while ANY study
  (`fmea_sweep`, `frontier`, `mc`, `coupling_loop`) is running — a foreground solve
  interleaving between iterates silently corrupts what `evaluate` reads.
- Abort: `POST /results/coupling_loop/abort` → 200 sets the record's stop event
  (404/409 semantics: 200 also when already finishing — idempotent; 404 when no
  record). Worker checks between iterates.
- Restore (route's job, try/finally): `"base"` = original cfg re-solve;
  `"final"` (only meaningful on a met verdict; otherwise falls back to base) =
  re-solve at `eps_star` and LEAVE the cfg's `ens_cap_permyriad = eps_star`
  (persisted via the normal config path) so the user holds the certified plan.
  `base_restored` truthful, False when the closing solve failed.
- Payload (stored record → GET): plan v2 §2 shape verbatim — `study:
  "coupling_loop"`, NO top-level engine/fidelity, `warning` = MC_WARNING_V1 + loop
  clause + multi-period clause when `by_period` has >1 key, verdict copy for
  `unreachable` names the three mechanisms (plan [N6]).
- Registries: ROUTE_SURFACES entries for get/post/abort AND the series/aggregate
  census (two registries — spec v1.3 note).

## 4. Frontend (after backend lands)

Plan v2 §3 is binding: the six-edit Adequacy tab split with the ★ mount-invariant
tests moved (no early return on the new tab), LostLoadTab copy fix + cross-link,
LoopPanel with h/yr entry + live dual echo (wire field horizon-basis), growing
iteration table while running, verdict copies incl. the three-mechanism
unreachable text, `confident` badge, `< floor` rendering, abort button,
blocked-button naming the blocker.

## 5. Test gates

Backend: adequacy gate (`tests/test_adequacy_*.py + golden coverage + results
range`) 0 failed, incl. 4 slow benchmarks UNCHANGED (1.2's default-False must not
move a single anchor). Frontend: tsc clean + full vitest 0 failed. Every ★ red
first, bite table, no commits by workers.

## Amendments

### v1.1 — Wave A adjudications (ratified by the master)

1. **Unsolved networks read extendable rows as 0 MW.** PyPSA 1.3.0 materialises
   `p_nom_opt` at its 0.0 default on `n.add`, so an unsolved extendable row is
   indistinguishable by value from a declined one; §1.1's literal reading stands
   (the engines score a solved PLAN — unsized capacity is not capacity), and the
   consequence is documented on `solved_capacity`.
2. **Non-extendable rows keep the historic `first finite (p_nom_opt, p_nom) > 0`
   chain verbatim** — PyPSA writes `p_nom_opt = p_nom` for them post-solve, so the
   chain and `p_nom` agree wherever data is sane, and byte-identical behaviour is
   what held the benchmark anchors.
3. **§1.3 mechanics:** on a mid-sweep exception the partial record rides on the
   exception as `exc.frontier_result` (nothing is returned); a restore that ITSELF
   fails is reported (`base_restored=False`, normal return) instead of propagating —
   the only way the "False when the restore failed" clause is observable.
4. **Negative solved sizes clamp to 0** on the extendable path.
5. **`must_take_generators` always takes the default walk** so the ELCC
   `kind="vre"` candidate list can never gain an unbuilt asset (bitten).
6. **Known hazard, out of scope here:** `copt.deconvolve` is degenerate for a 0 MW
   two-state unit (raises; `attribute_criticality` falls back to a rebuild — safe
   but wasteful). No caller routes a superset fleet there today; the loop's
   `evaluate` is MC-only. Do not route one without revisiting.

### v1.2 — Wave B adjudications (ratified by the master)

1. **The MC-skip on a plateau uses a duck-typed `evaluate.plan_hash` attribute.**
   §2's binding signature (`evaluate() -> (hash, metrics)`) cannot yield a hash
   before the MC runs, so a genuine skip needs the hash separately: when the
   callable carries a `plan_hash` attribute the controller probes it first and
   skips the MC on a match; without it, the MC runs and the STORED metrics are
   reused on a hash match, with a logged cross-check that turns the CRN invariant
   into a self-report. **The route MUST implement `evaluate.plan_hash`** (the hash
   comes from the snapshot, which is cheap without sampling) — §3 is amended
   accordingly.
2. **`budget_exhausted` means "never met".** Refinement's hash-equality stop
   provably cannot fire on a well-behaved LP (a looser cap reproduces the met plan
   only when that plan is unconstrained-optimal, in which case the looser endpoint
   would itself have met), so refinement runs to the budget on essentially every
   real network; the literal reading would stamp `budget_exhausted` on nearly
   every successful study. A verified met iterate ⇒ verdict `met` (final valid,
   possibly un-refined — [S5]/[N8]: a broken bracket degrades optimality, never
   validity). `budget_exhausted` is reserved for runs with `final: None`.
3. **`resolution_floor_h`** is not in the iterate `mc` block; the ROUTE lifts it
   from the final evaluation's metrics into the study payload (top level,
   `resolution_floor_h`) so the panel can render `< floor`.
4. Defensive extensions ratified: `_is_infeasible` also reads `status`;
   raising `solve_at`/`evaluate`/unreadable metrics → recorded iterate + verdict
   `failed` (never an exception out of the worker); raising `on_iteration` logged,
   not fatal; `max_solves` clamped to `MAX_LOOP_SOLVES`, `eps0 ≤ 0` clamped to the
   floor; a non-infeasible failure at the ε backstop reports `budget_exhausted`,
   never `unreachable`.

### v1.3 — Wave C adjudications (ratified by the master)

1. **`POST /simulation/run_ac_pf` is the second guarded solve entrypoint** — not an
   LP, but it holds the lock for its whole run and rewrites result frames in place;
   the guard sits before its own preconditions so a blocked user is told "a study
   is running", not "re-run LOPF".
2. **The shared mesh predicate lives in `services/study_state.py`** — results.py
   imports from simulation.py already, so neither router can host it without a
   cycle; both alias one definition (`STUDY_KEYS`, `STUDY_LABELS`,
   `blocking_study_detail`).
3. **`resolution_floor_h` = the last evaluation's value** (refreshed per evaluate;
   up-front `min positive weight / draws` as the fallback) — identical to "the
   final's" under the pinned draws call, which is what makes the ambiguity inert.
4. **The [N6] three-mechanism text ships as `verdict`** (a sentence beside the
   machine-readable `status`); `warning` remains the caveat string. The frontend
   renders `verdict` directly.
5. **`restore="final"` persists `ens_cap_permyriad = eps_star` BEFORE the closing
   re-solve**, so a failed closing solve still leaves the user holding the ε* they
   asked for, with `base_restored=False` telling the truth about the network.
   "final" on a non-met verdict falls back to base — never applies an unverified
   cap.
6. **Record publish + thread start happen under one solver-state-lock hold**
   (claim atomicity: `_study_running` tests `is_alive()`, and a registered-but-
   unstarted thread reads as stale — a second POST in that window would put two
   loops on one network). Note: `post_mc` predates this discipline and carries the
   same tiny window; tightening it is a cheap follow-up, not done here.
7. The series/aggregate census scans GETs only; the POST and abort routes carry
   their coverage in `ROUTE_SURFACES` alone (noted in the census comment).
