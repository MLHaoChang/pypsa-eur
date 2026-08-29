# Phase 7 — the adequacy-coupled planning loop (plan, v1)

Realises the Phase-6 decision record's candidate (i): **solve LP → run sequential MC →
retune `ens_cap_permyriad` → re-solve, until the PLAN meets the user's target on the
MC's own LOLE** rather than the LP proxy's shed energy. Today the plan only ever meets
a deterministic standard and the MC then reports the truth about it; after this phase
the truth is the standard.

Everything the Phase-6 record pre-recorded about this loop is normative here:

- the map ε (`ens_cap_permyriad`) → MC-LOLE is **piecewise-constant** (plans change
  only at LP breakpoints) and **noisy** unless the noise is removed;
- **CRN across outer iterations** is therefore mandatory: one seed, one draw budget,
  fixed for the whole loop, so the map becomes deterministic and search is sound;
- the stopping rule **accepts a band**, never chases an exact hit on a step function;
- the target may be **unreachable by tightening the proxy cap at all** (the LP meets
  tighter caps with foresight-dependent storage the MC discounts) — a first-class
  outcome, not an error;
- couplings (ii)–(iv) stay rejected (ELCC-PRM later at most; derating never a default;
  Benders never — greedy recourse has no valid duals).

## 1. The controller (`services/adequacy/coupling.py`)

New module, engine-pure and injectable for tests:

```
run_coupling_loop(
    solve_at,        # (eps: float) -> {"status", "cost_eur", "ens_mwh", "report"}
    evaluate,        # () -> mc metrics dict (reads the network the solve left)
    *,
    target_lole_h,   # in the HORIZON'S OWN BASIS (same unit lole_hours reports)
    eps0,            # starting cap, permyriad (the user's current target, or a default)
    max_solves,      # hard budget, each iterate is a full capacity-expansion solve
) -> {"status", "iterations": [...], "final": {...}, ...}
```

The route later binds `solve_at` to `sweep._solve_once` with
`dataclasses.replace(cfg, ens_cap_permyriad=eps)` (exactly the frontier's move) and
`evaluate` to `snapshot_inputs` + `mc_adequacy` at the loop's fixed seed/draws.
Injection is not a testing nicety: the controller's failure modes (plateau, budget,
unreachable, non-monotone step) are all cheap to exercise against a fake map and
ruinously slow against HiGHS.

### Search discipline

1. **Iterate 0 at `eps0`.** If MC-LOLE mean ≤ target: DONE, status `met`, zero extra
   solves. The cheap case must stay cheap.
2. **Tightening phase.** While the target is missed: `eps ← eps / 4` (geometric — the
   map is a step function, so fine steps waste solves crossing nothing) until either a
   met iterate appears (go to 3), `eps` reaches `EPS_FLOOR` (= 0.01 permyriad; below
   that the cap is numerically indistinguishable from zero shed) — evaluate the floor
   itself, and if it still misses, status `unreachable` — or the budget is spent
   (status `budget_exhausted`, best iterate reported).
3. **Refinement phase (bisection on the bracket).** Between the tightest MISS and the
   loosest MET, bisect in log-ε until the bracket ratio ≤ 2 or the budget is spent.
   The point: the loosest cap that meets the target is the CHEAPEST plan that meets
   it — refinement is buying cost, not correctness. `final` = the loosest met iterate.
4. **Plateau shortcut.** If a solve returns a plan with the same objective as the
   previous iterate (|Δcost| ≤ 1e-6·cost), the plan did not change, and under CRN the
   MC result CANNOT change: reuse the previous metrics, spend no MC, and record the
   iterate with `plateau: true`. (This is why CRN is load-bearing and not a
   preference.)
5. **Non-optimal solves.** An infeasible/non-optimal solve at some ε is recorded with
   its status (frontier convention: a real answer, not a gap) and treated as a MISS
   boundary for bracketing — the tightest *feasible* cap bounds what the proxy can do,
   and if the loop cannot meet the target within feasibility, that is `unreachable`.

### The stopping band (recorded requirement, made concrete)

`met` means **mean MC-LOLE ≤ target**. The result additionally carries
`confident: bool` — true when the 95% CI upper bound is ≤ target. The loop never
iterates to turn `confident` on (that is a draws decision, not a cap decision); it
reports the band and lets the user raise draws. Chasing CI-tightness with more solves
is exactly the oscillation the record warns about.

### Budgets

`MAX_LOOP_SOLVES = 8` module constant (each iterate is a full solve; the frontier's
budget thinking applies). Loop draws default to the engine's 500 / CoV 0.05, but
`cov_target` is IGNORED inside the loop in favour of a fixed draw count: adaptive
batching would break batch-sequence CRN across iterations for nothing (the plateau
shortcut needs bit-identical metrics for identical plans).

## 2. API

- `GET /results/mc_loop` → 204 / stored record (thread stripped) — the mc/frontier
  pattern verbatim.
- `POST /results/mc_loop` body `{target_lole_h (required, > 0), draws?, seed?,
  eps0?, max_solves? (≤ MAX_LOOP_SOLVES)}` →
  409 mesh (solve/sweep/frontier/mc/loop, both directions — the loop SOLVES, so it is
  the most exclusive member yet); 422 without VoLL (the solves need slack, frontier
  rule); 422 with nothing to sample (mc rule); worker thread mandatory (minutes to
  tens of minutes: up to 8 capacity-expansion solves).
- Payload: `{engine: "mc", fidelity: "sequential_mc", status, target_lole_h, basis,
  iterations: [{eps_permyriad, solve_status, cost_eur, ens_mwh, plateau,
  mc: {lole_hours, lole_ci, eue_mwh, eue_ci, n_samples} | null}],
  final: {…the chosen iterate…} | null, confident, base_restored,
  warning}` — a sibling payload; `warning` = MC_WARNING_V1 plus one loop clause
  ("the loop tunes the proxy cap; the plan's optimality is per-iterate, and the map
  from cap to MC-LOLE is a step function — the reported cap is the loosest tested
  that met the target, not a continuum optimum").
- **Base restore**: closing re-solve with the user's original config, frontier
  convention, `base_restored` in the payload.
- `TargetBlock.basis` finally gains `"mc_lole"`? **No — explicitly not.** The loop
  does not make `mc_lole` a solve-time basis: the LP still cannot honour it directly,
  and Phase 6's comment ("a basis a solve cannot honour would read as a standard the
  run met") still holds. The loop is a STUDY that finds the proxy cap realising an
  MC-LOLE target; the reservation comment is updated to say the seam is now exercised
  by `/results/mc_loop` without becoming a report basis. (Adversarial reviewers:
  challenge this.)

## 3. Frontend — and the recorded IA split

The Phase-6 IA revisit condition, recorded verbatim in McPanel's header, fires now:
*"when the Phase-7 coupling loop or a fourth study lands, this tab has tipped and the
adequacy surfaces split into a dedicated Results→Adequacy tab."* So this phase:

- adds a **Results → Adequacy tab** and moves the adequacy surfaces there: the
  achieved-vs-target chips block, CoptChips, FrontierPanel, McPanel (+ comparison
  table), and the new LoopPanel. The Lost load tab keeps the lost-load evidence
  (KPIs, series, per-period breakdown) and gains one cross-link line ("adequacy
  studies moved to the Adequacy tab").
- **LoopPanel**: target input (in the horizon's own basis, `basisSuffix` — the same
  guard against reading 20 h/168 h as 20 h/yr), run/poll (the run is LONG — surface
  per-iteration progress from the record as iterations append), an iteration table
  (ε, solve status, cost, MC-LOLE as a range, plateau marker), the final verdict line
  ("met at ε=…, cost …, LOLE …–… h/horizon" / "unreachable by proxy cap — the LP
  meets tighter caps with foresight the MC discounts" verbatim-ish), `confident`
  badge, the warning.
- Blocked-button names the blocker (mesh convention); literal hex in any SVG.

## 4. Testing contract (red-first, bitten, per session discipline)

Controller unit tests (fake `solve_at`/`evaluate`):
- ★ cheap case: eps0 already met → 1 solve, 1 evaluate, status met. Bite: always
  tighten once.
- ★ monotone step map: converges to the loosest met step within budget; final is
  loosest-met not tightest-met. Bite: return tightest.
- ★ plateau: identical-cost iterate reuses metrics (evaluate NOT called — assert call
  count) and marks `plateau`. Bite: re-evaluate every iterate.
- ★ unreachable: map floor > target at EPS_FLOOR → `unreachable`, iterations carry
  the floor evaluation. Bite: report met at floor.
- ★ budget: `budget_exhausted` with best-so-far final. Bite: overrun the budget.
- ★ infeasible-at-tight-eps: recorded with status, used as bracket bound. Bite: treat
  infeasible as met.
- ★ band: mean ≤ target with CI-hi > target → met + confident=false. Bite: require
  CI-hi.
- Integration (slow-marked): tiny live-HiGHS network where the loop demonstrably
  moves ε at least once and lands met — asserting the LP cost rose from iterate 0 to
  final (reliability costs money; a free improvement means the test fixture is
  vacuous).

Endpoint tests: TestClient layer, 409 mesh both directions incl. loop↔mc, 422s,
payload shape, thread stripped, golden-coverage + series-census registry entries
(the spec v1.3 two-registry note).

Frontend tests: LoopPanel red-first + bitten (blocked-button, range rendering,
verdict lines, iteration table); Adequacy tab mount tests; LostLoadTab keeps its
lost-load tests and drops the moved-surface mounts.

Live QA: S17 (build fixture where iterate 0 misses; loop runs; verdict met; cost
increased; base restored) + a browser round on the new tab — per session precedent,
run by the master.

## 5. Non-goals (v1 of this phase)

- Per-period MC-LOLE targets (system-level only; `by_period` is reported, not
  targeted).
- EUE targets (LOLE first; the machinery generalises later).
- Any solve-time `mc_lole` basis (see §2).
- Multi-weather realisations and correlated outages (separate phases; the loop
  inherits MC_WARNING_V1's caveats and says so).
- Auto-raising draws to force `confident` (a draws decision belongs to the user).

## 6. Open decisions for review

1. Tightening factor 4 vs 2 (fewer solves crossing plateaus vs finer first bracket).
2. `EPS_FLOOR = 0.01` permyriad — right floor?
3. Refinement stop at bracket ratio ≤ 2 — enough cost resolution?
4. Should the loop reuse the mc study's `_state["mc"]` record for its per-iterate MC
   results (so McPanel shows the last iterate), or stay fully separate? (Plan says
   separate; the loop's evaluations are CRN-pinned draws, not the user's study.)
5. Endpoint name `/results/mc_loop` vs `/results/coupling_loop`.
