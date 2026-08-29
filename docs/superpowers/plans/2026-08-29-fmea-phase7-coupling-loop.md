# Phase 7 — the adequacy-coupled planning loop (plan, v2 post-review)

Realises the Phase-6 decision record's candidate (i): **solve LP → run sequential MC →
retune `ens_cap_permyriad` → re-solve, until the PLAN meets the user's target on the
MC's own LOLE** rather than the LP proxy's shed energy.

v1 was adversarially reviewed; the review produced 4 blockers, 13 should-fixes and 8
notes, ALL incorporated below and marked `[B*]`/`[S*]`/`[N*]`. The two most important
reversals of v1:

- **v1's CRN claim was false as written** `[B1]`: `sample_capacity` keys each unit's
  substream to its POSITION in the fleet tuple, and the fleet membership CHANGES
  between iterates (an extendable generator the LP declines at loose ε is dropped by
  `_membership_walk`'s `cap <= 0` test; a tighter ε builds it and it enters mid-tuple,
  shifting every downstream unit's whole outage path). Building previously-unbuilt
  firm capacity is the canonical LP response to a tighter cap — the mechanism the loop
  exists to drive. Fixed structurally in §0.
- **v1's plateau shortcut was unsound in both directions** `[B3]`: equal objective
  does not imply equal plan (degenerate optima), and with DSR configured the objective
  moves (variable cost) while the plan stands still. Replaced by a plan-hash of what
  the MC actually reads, plus the report's own `binding` as the cheap pre-test.

The Phase-6 record's rejected couplings (ii)–(iv) stay rejected.

## 0. Engine prerequisites (land FIRST, as their own bitten commits — they are
   latent Phase-6 bugs the review surfaced, valuable independent of the loop)

### 0.1 `[B2]` Unbuilt extendable assets are simulated at nameplate

`copt._firm_capacity` and `mc._storage_capacity` both scan `("p_nom_opt", "p_nom")`
and take the first finite value **> 0** — so PyPSA's `p_nom_opt = 0.0` for an
extendable asset the LP DECLINED falls through to the pre-solve nameplate, and the
MC/COPT score a plan containing capacity that does not exist. Optimistic bias,
worst for an extendable battery (simulated at full power AND energy).

Fix: for an **extendable** row on a solved network, `p_nom_opt` wins whenever it is
finite and ≥ 0 (zero means zero); `p_nom` remains the fallback for non-extendable
rows and unsolved networks (no `p_nom_opt` column / NaN). Applies to generators AND
storage. ★ Bitten tests: extendable gen `p_nom=100`, solved `p_nom_opt=0` → not in
the fleet; same for a storage unit → not in `inputs.storage`. Bite: restore the
`> 0` fallthrough.

### 0.2 `[B1]` Position-keyed CRN breaks across changing fleets — stable identity

Chosen fix: **name-stable stream keying is NOT introduced** (it would re-key every
existing sampled path and break the pinned RTS-79/RBTS anchors). Instead the loop
uses a **fixed superset fleet**: `fleet_and_residual` (and `snapshot_inputs`) gain
`keep_zero_capacity=False`; with `True`, occurrence-bearing generators that clear
every scope test EXCEPT `cap > 0` stay in the fleet at `capacity_mw=0.0` (likewise
zero-capacity storage rows). A zero-capacity unit consumes its substream and adds
0 MW — it stabilises every other unit's position at no cost, exactly the discipline
`elcc`'s exclude-but-consume already uses. Membership (the NAME SET) is then
invariant across iterates as long as the component list itself is unchanged, which
the loop's solves guarantee (they change capacities, never add components).

★ Bitten test (the review's requested red test): two networks differing only in one
extendable unit's `p_nom_opt` (0 vs built) → with `keep_zero_capacity=True`, an
untouched unit's sampled path is BIT-IDENTICAL across the two. Bite: revert to
dropping cap ≤ 0. Also `[N8]`: the superset keeps membership from collapsing to an
empty fleet mid-loop (which would report the whole horizon as lost).

Default stays `False`: COPT tables, the MC study endpoint and ELCC are unchanged
(zero-capacity units would only pad their COPT with a point mass at 0).

### 0.3 `[S8-b]` `run_frontier_sweep` restore is not exception-safe (pre-existing)

No try/finally around the closing base re-solve: an exception mid-sweep leaves the
network on the last swept ε while the foreground results describe the pre-study
solve. Fix in frontier.py (try/finally, `base_restored=False` recorded on the
failure path) — the loop inherits the fixed pattern.

## 1. The controller (`services/adequacy/coupling.py`)

```
run_coupling_loop(
    solve_at,        # (eps) -> {"status", "condition", "cost_eur", "ens_mwh",
                     #           "cap_mwh", "binding", "report"}
    evaluate,        # () -> (plan_hash, metrics)  — reads the solved network
    *,
    target_lole_h,   # HORIZON-basis hours (see §3 for the panel's h/yr entry)
    eps0,
    max_solves,      # route-validated ≤ MAX_LOOP_SOLVES [N7]
    stop_event=None, # [S8] checked between iterates; abort → status "aborted"
    on_iteration=None,  # [S6] called with each completed iterate row
) -> {"status", "iterations", "final", "confident", ...}
```

`evaluate` returns `(plan_hash, metrics)`: the hash is over exactly what the MC
reads — the `(name, capacity_mw)` vector of `inputs.units`, the
`(name, p_nom_mw, e_nom_mwh)` vector of `inputs.storage`, and the residual bytes
`[B3]`. Identical hash ⇒ identical MC result is EXACT under §0.2 + §1-CRN (same
inputs, same seed, same draws — bit-identical), so metrics reuse is sound where
cost equality never was.

### CRN discipline `[S10]` — the exact call is normative

```
mc_adequacy(inputs, draws=N, seed=S, max_draws=N)
```

One batch of N, always: `max_draws=N` is what pins the sample count (merely
ignoring `cov_target` leaves the 2000-draw adaptive cap in place and n_samples
drifts between iterates — the CRN-hostile aggregator failure spec §3 documents).
Loop default `N = 500`, `S = seed` from the request.

### Search discipline

1. **Iterate 0 at `eps0`.** Met (mean ≤ target) → done, status `met`, zero extra
   solves.
2. **Tightening.** `[B4]` The step uses the information each solve returns rather
   than blind geometry:
   `eps_next = min(eps/4, 0.5 · eps · achieved_ens_mwh / cap_mwh)` — when the cap is
   slack (high VoLL: `binding == "voll"`, achieved ≪ cap `[N8]`), this crosses the
   whole slack region in one solve where ÷4 alone burns five. ÷4 stays as the
   step's floor so a binding cap still moves geometrically. (§6-v1 decision 1 is
   thereby settled: the factor is 4 AND informed, never 2 — factor 2 cannot even
   reach the floor inside the budget: ⌈log₂(1000)⌉ = 10 > 8.)
3. **Skip-evaluation rule `[B3]`.** After each solve, if `binding != "system_cap"`
   the cap did nothing this iterate (report's own computation); and if the plan hash
   equals a previously evaluated iterate's, reuse those metrics with
   `plateau: true`. Otherwise ALWAYS evaluate — an MC evaluation is tens of seconds
   against minutes per solve; the correctness hole of guessing is not worth the
   cheap side of the ledger.
4. **Termination of tightening:**
   - a met iterate → refinement (5);
   - **infeasible** solve `[S1]`: the ε-feasible sets are nested (F(ε′) ⊆ F(ε) for
     ε′ < ε), so the FIRST infeasible iterate proves every tighter ε infeasible —
     stop tightening immediately; if no met iterate exists, status `unreachable`.
     "Infeasible" is detected from `condition` (`"infeasible" in condition.lower()`);
     a time-limit / numerical / `validation_failed` outcome is NOT monotone in ε and
     is recorded as `solve_failed` for that iterate WITHOUT concluding
     unreachability. A failed/infeasible iterate is NEVER evaluated (`mc: null`) —
     `p_nom_opt` still holds the previous plan and evaluating would silently score
     the wrong iterate against this ε.
   - **floor** `[S3]`: the real hazard at the bottom is the `permyriad <= 0` sentinel
     (`_wrap_with_ens_cap` treats ≤ 0 as NO TARGET and returns the loosest plan
     while the loop believes it produced the tightest) — assert `eps > 0` at every
     step. The stopping floor is expressed in ENERGY: stop when the report's
     `cap_mwh < 1.0` (at that point the constrained plan equals the zero-shed plan);
     `EPS_FLOOR = 0.01` permyriad remains only as a hard backstop. Floor evaluated
     and still missed → `unreachable`.
   - **budget** (`max_solves` spent) → `budget_exhausted`, best verified iterate as
     `final`.
5. **Refinement `[S4]`.** Bisect log-ε between the tightest evaluated MISS and the
   loosest MET (this bracket is infeasibility-free by the same nesting `[S1]`).
   Stop when the midpoint's plan hash equals the met endpoint's — the met endpoint
   is then already the loosest cap producing that plan and no further solve can
   lower cost. (Replaces v1's meaningless "bracket ratio ≤ 2".)
6. **`final` = the cheapest VERIFIED met iterate** `[S5]` — verified means its own
   MC evaluation met. No continuum claim, no monotonicity assumption: ε→MC-LOLE can
   genuinely rise as ε tightens (storage-for-thermal substitution under foresight),
   which is one of the unreachability mechanisms, so the bracket is a search
   heuristic and only evaluated iterates are answers. A fixed budget + verified-only
   finals means a broken bracket invariant degrades optimality, never validity
   `[N8]`.

### The stopping band

`met` = mean MC-LOLE ≤ target. `confident` = 95% CI upper bound ≤ target, reported,
never iterated for (a draws decision, not a cap decision). `[S11]` A target below
the evaluation's `resolution_floor_h` is UNDECIDABLE at these draws: refused at the
route (422 naming the draw count that would resolve it) when checkable up front
(floor = min positive weight / N is known before any solve), and any all-clear
verdict renders `< floor h`, never `0.00 h`.

## 2. API

- `GET /results/coupling_loop` → 204 / stored record, thread stripped. `[N2]` The
  name is NOT `mc_loop`: it sits outside `mc`'s namespace (no
  `_study_running("mc")` copy-paste hazard), and its iterates are LP solves — the
  MC label belongs to the per-iterate metrics. `[N1]` State key
  `_state["coupling_loop"]`, guarded by `_study_running("coupling_loop")` — NEVER
  the `"mc"` record (a loop writing there would make an MC study appear running and
  trip the mesh from the wrong side, and McPanel would present pinned-draw
  intermediates as the user's converged study).
- `POST /results/coupling_loop` body
  `{target_lole_h (required, > 0), draws?, seed?, eps0?, max_solves?, restore?}`:
  - 409 mesh registered in EVERY study guard both directions, and `[S7]` the mesh's
    two pre-existing holes are FIXED in this phase (budgeted, cross-module):
    `post_fmea_sweep` gains the missing frontier guard, and `POST /simulation/run`
    gains study guards (today a foreground solve can interleave between any study's
    iterates and silently corrupt what `evaluate` reads — the exact failure the
    mesh exists to prevent, and worst for the longest-running study, this one).
  - 422: no VoLL (solves need slack); nothing to sample; `target_lole_h <= 0`;
    target below the up-front resolution floor `[S11]`; `max_solves` outside
    `[1, MAX_LOOP_SOLVES]` route-side `[N7]`; `[S2]`
    `solve_strategy in {rolling, myopic}` — `_check_ens_cap_coherence` fails every
    capped solve under those strategies, and without this guard the loop would run
    8 failed iterates and report `unreachable` when the true answer is "unsupported
    solve strategy".
  - Worker thread (minutes to tens of minutes; wall-time budget is
    `max_solves + 1` solves — the closing restore is OUTSIDE `max_solves` `[N7]`).
  - `[S8]` A stop event is stored on the record and checked between iterates;
    `/simulation/abort`'s inability to reach study workers is noted, and the loop's
    record carries its own abort affordance (POST `/results/coupling_loop/abort` or
    equivalent flag — worker decides mechanics, contract: abort between iterates →
    status `"aborted"`, restore still runs).
  - `[S9]` `restore: "base" | "final"` (default `"base"`): the closing re-solve
    uses the user's original config, or — on an explicit `"final"` with a met
    verdict — ε*, so the user is left HOLDING the certified plan. The verdict copy
    always states which, and on `"base"` says exactly how to keep the plan ("set
    ens_cap_permyriad = ε* and re-solve"). `base_restored` reports what happened,
    `false` on the failure path (restore wrapped in try/finally per §0.3).
- Payload `[N4]`: NO top-level `engine`/`fidelity` (the top-level product is a cap
  and a verdict, not a metric; labelling it "mc" would misuse the sibling
  convention). Shape:
  `{study: "coupling_loop", status: "met"|"unreachable"|"budget_exhausted"|
    "aborted"|"failed", target_lole_h, basis, confident, eps_star, restore,
    base_restored,
    iterations: [{eps_permyriad, solve_status, condition, cost_eur, ens_mwh,
                  cap_mwh, binding, plateau,
                  mc: {engine: "mc", fidelity: "sequential_mc", lole_hours,
                       lole_ci, eue_mwh, eue_ci, n_samples, by_period} | null}],
    final: {…iterate…} | null, warning}`.
  `by_period` rides on every evaluated iterate `[N4]/[N5]`: on a multi-period
  network it is the only way to see WHICH period drives a miss.
- `[N5]` Multi-period caveat, stated in the payload warning and the docs: the cap is
  enforced PER PERIOD against each period's own demand while the MC target is a
  horizon SUM — a single scalar ε cannot express "fix period 3 only", so
  `unreachable`/`budget_exhausted` are structurally likelier on multi-period
  networks; the per-iterate `by_period` is the diagnostic.
- `[N6]` The `unreachable` verdict copy names all three mechanisms, because the
  user's next action differs: (a) foresight-dependent storage the MC discounts;
  (b) DSR serving the LP's cap while the MC excludes it as a resource (plan
  unchanged, cost rising); (c) storage-for-thermal substitution raising MC-LOLE as
  ε tightens.
- `[N3]` `TargetBlock.basis` does NOT gain `"mc_lole"` — the review ratified v1's
  refusal (the LP still enforces an energy cap; a basis the solve cannot honour
  would assert a standard the run met). What IS added: **provenance** — the loop's
  result carries `eps_star` and the applied-target affordance (`restore: "final"`),
  and the reservation comment on `TargetBlock.basis` is updated to point at
  `/results/coupling_loop` as the seam's realisation.
- `warning` = MC_WARNING_V1 + the loop clause (step-function map; per-iterate
  optimality; verified-only answers) + the multi-period clause when applicable.
- Registry: BOTH test registries (`ROUTE_SURFACES` and the series/aggregate census)
  — spec v1.3's two-registry note.

## 3. Frontend — the recorded IA split, done honestly `[S13]`

The Phase-6 revisit condition fires verbatim. Scope, counted honestly at SIX coupled
edits (`ResultsTab` union, `VALID_TABS`, `TABS`, `RESULTS_TO_COMPARE_TAB` — typed
`Record<ResultsTab, CompareTab>` so TS forces the entry — the tab body switch, and
`LostLoadTab.tsx`):

- **Results → Adequacy tab** hosting: AdequacyChips block, CoptChips, FrontierPanel,
  McPanel (+ comparison table), LoopPanel. **The tab has NO early return gated on
  lost load or on a solve existing** — the invariant the ★-bitten LostLoadTab mount
  tests encoded ("a reliable system is exactly where the surfaces must still
  render") MOVES with the surfaces: equivalent ★ mount tests on the Adequacy tab,
  both the no-data and data states. LostLoadTab keeps the lost-load evidence, drops
  the moved mounts AND fixes its no-lost-load copy (it currently says "the
  reliability target ABOVE reports what bound" — "above" moves; replace with the
  cross-link line).
- **LoopPanel**: `[S12]` dual target entry — the user types **h/yr** (the unit every
  standard is written in), the panel converts through `horizon_years` and echoes
  both live ("3 h/yr = 0.058 h / 168 h horizon"); the WIRE field stays
  horizon-basis so the comparison is unit-safe. (On a weighted representative-week
  network `nyears ≈ 1` and the two coincide — the echo shows it.) Run/poll with
  per-iteration progress (the record's `iterations` list grows mid-run: `[S6]` the
  controller's `on_iteration` hook appends by REBINDING the list under the solver
  state lock, never mutating in place — a shallow-copied GET must never see a torn
  list; the worker closes over its record, `post_mc`'s pattern, NOT
  `post_frontier`'s `_state`-from-thread anti-pattern). Iteration table (ε, solve
  status, binding, cost, MC-LOLE range, plateau marker), verdict line per `[N6]`,
  `confident` badge, `< floor` rendering, the warning, abort button.
- Blocked-button names the blocker; literal hex in any SVG.

## 4. Testing contract (red-first, bitten)

§0 prerequisites: the two ★ bitten tests stated there, plus frontier try/finally
(inject a raising iterate → base_restored False, restore attempted).

Controller unit tests (fake `solve_at`/`evaluate`):
- ★ cheap case: eps0 met → 1 solve, 1 evaluate. Bite: always tighten once.
- ★ informed jump: slack-cap fake (achieved ≪ cap) crosses the slack region in one
  step. Bite: blind ÷4.
- ★ plan-hash reuse: identical hash → metrics reused, `evaluate` call count pinned,
  `plateau: true`; DIFFERENT hash at equal cost → evaluated (the B3 false-positive
  case). Bite: key reuse on cost.
- ★ infeasible monotonicity: first infeasible stops tightening; `solve_failed`
  (time-limit) does NOT conclude unreachable; failed iterates carry `mc: null`
  and are never evaluated (fake `evaluate` raises if called). Bite: keep tightening
  past infeasible.
- ★ unreachable at the energy floor; ★ budget_exhausted with best-verified final;
  ★ refinement stops on hash-equal midpoint; ★ band (met + confident=false);
  ★ abort between iterates → "aborted", restore ran.
- ★ eps>0 sentinel: a step that would reach ≤ 0 clamps and the loop never solves
  with the no-target sentinel. Bite: allow 0.
Integration (slow): tiny live-HiGHS network, iterate 0 misses, loop lands met with
`cost_final > cost_0` (strict — the verdict flipped via a genuinely different plan).

Endpoint tests: TestClient; the full 422 set incl. rolling/myopic and
below-floor targets; 409 mesh BOTH directions incl. the two fixed holes
(solve-during-loop refused, loop-during-solve refused, sweep↔frontier);
payload shape incl. per-iterate `by_period`; thread stripped; mid-run GET sees a
consistent, growing iterations list; both registries.

Frontend: LoopPanel red-first + bitten (h/yr echo conversion, verdict copies,
iteration table, abort, blocked-button); Adequacy tab ★ mount tests both states;
LostLoadTab copy fix tests.

Live QA: S17 (miss → loop → met; cost rose; base restored; restore="final" leaves
ε* applied; abort mid-run) + a browser round on the new tab — run by the master.

## 5. Non-goals (v1 of this phase)

- Per-period MC-LOLE targets (system-level only; per-iterate `by_period` is the
  diagnostic, not a target).
- EUE targets.
- Any solve-time `mc_lole` basis (`[N3]` ratified).
- Multi-weather realisations and correlated outages (separate phases).
- Auto-raising draws to force `confident`.
- Name-keyed CRN streams (rejected here: would re-key every existing path and break
  the pinned benchmark anchors; the superset fleet achieves iterate-stability
  without touching them).

## 6. Review disposition

All v1 §6 open decisions are settled by the review: (1) factor 4 + informed jump
`[B4]`; (2) energy-expressed floor with the ≤0-sentinel guard `[S3]`; (3)
hash-equality refinement stop `[S4]`; (4) separate `_state["coupling_loop"]`
`[N1]`; (5) `/results/coupling_loop` `[N2]`.
