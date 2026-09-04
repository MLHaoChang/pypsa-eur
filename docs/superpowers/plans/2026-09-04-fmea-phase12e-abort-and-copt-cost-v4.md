# Phase 12e — every study can be stopped, and `/copt` costs less (plan, v4)

Supersedes v3 (**rejected**: a tolerance its own inputs refute, and a sort key
that breaks a shipped surface), v2 and v1. All three are kept with their
reviews. **The v3 review found the design buildable** — it implemented both of
§2's changes independently and reproduced their exactness and their speed-up —
and rejected the plan's *numbers, switch rule and tests*. Those are what v4
rewrites; the mechanism is unchanged from v3.

## 0. What v3 got wrong

**Blocker 1 — one tolerance for two different changes.** v3 set F2 at `1e-8`
for both the binning and the `deconvolve` deletion. They are not comparable:

* **Binning is exact.** Measured worst rel **4.2e-11** at 300 u / 8 profiled /
  8760 h, 2.2e-12 at benchmark scale. (v3's stated 3.8e-13 was itself ~100×
  understated at production scale.)
* **Deleting `deconvolve` changes numbers, and the bound is the shipped mass
  guard.** `deconvolve` accepts any table whose mass lands in
  `0.999 ≤ total ≤ 1.001` (`copt.py:674`), so a deconvolved table can carry up
  to ~1e-3 of mass error by construction; the `2^k` mixture then probes cells
  the plain path never reaches and that error lands on ΔEUE. Measured 2.2e-13
  on my fixtures and **1.04e-4** on the review's (n = 20, q = 0.01, k = 8 —
  a textbook FOR and the shipped `K_EXACT`), the difference being how far that
  fixture's mass drifted *inside* the guard (1.000016779 there, 1.0000000005
  here). **Any tolerance below the guard's own 1e-3 is unsound while the
  reference contains the call.** The rebuild is the accurate side, so this
  change improves the numbers — but it changes them visibly (59.8798 →
  59.8860 MWh on the review's row), and "four orders below any physical claim"
  was false.

**Blocker 2 — the third sort site ranks on a different quantity.**
`/fmea_modes` sorts on `criticality_eur_per_year` (`results.py:2961`), not
ΔEUE, and `tests/test_adequacy_stress.py:210-211` asserts exactly that. The
list mixes class A (criticality = ΔEUE × VoLL, monotone in ΔEUE) with class B
(`q × ΔEUE × VoLL`) and class C (`freq × ΔEUE × VoLL`), which are **not**
monotone in ΔEUE. v3's "the key goes on all three sites" would have re-ranked
the FMEA worksheet's source list and failed a shipped test.

| v3 finding | v4 |
|---|---|
| **1** one tolerance for two changes | §2 "What this costs": the two claims split, with their own ★s and tolerances |
| **2** third sort site | §2 "Determinism": that site's key is `(-criticality_eur_per_year, mode_id)` |
| **3** crossover constant does not reproduce | §2: `binned iff k ≥ 2`, no constant, evaluated PER BLOCK, with the measured give-up |
| **4** F2's fixtures routed to the direct path | §4: F2/F2b call the binned path directly; a separate ★ pins the switch |
| **5** portfolio PERIOD union missed | §1: `ElccPortfolioPeriodStatus` + `PORTFOLIO_PERIOD_LABEL` + `PORTFOLIO_STATUS_LABEL` |
| **6** "rebuild always wins" over-claimed | §0 below: scoped to `L/n ≳ 6`, with the counterexample regime named |
| **7** three `elcc.py` citations wrong | §1 |
| **8** `test_golden_coverage.py` allowlist | §1 item 5 |
| **9** F2d has no constructible bite | §4: a unit test of the sort key alone; §2 restates what the key fixes |
| **10** F2c contradicts §2 | §4: no timing claim; the asserted path is named |
| **11** the Routes paragraph was dropped | §1 "Routes" |
| **12** F1d asserts wall time | §4: a boundary counter |
| **13** the restore guard has no bite | §4 F1b2 |
| **14** boundary table omits the portfolio | §1 |
| **15** citations / target / S27 / nine-key row / F1f | §1, §2, §4 |

**And a claim of v3's own, corrected.** "The rebuild is faster even when the
deconvolution succeeds" is not unconditional: the deconvolve path wins by up
to 3.9× when `L/n ≲ 3` *and* `q ≳ 0.4` together. It is true on every fleet
`/copt` can produce — `delta_mw=1.0` and MW-scale capacities give `L/n ≳ 6`
(measured across n = 2…300, q = 0.02…0.499, rebuild 3.3–5× faster) — and in
that counterexample regime the deconvolved table is also the *less accurate*
of the two, and the whole fleet costs under 0.1 s either way. The conclusion
stands; the sentence is scoped.

## 1. Part A — the three studies get a stop event

**Cooperative, a `break`, never an exception** — `run_contingency_sweep`'s
closing base re-solve sits outside its `try/finally` (`sweep.py:219-231`),
unlike the frontier's (`frontier.py:228-230`), so an exception would skip the
restore and leave the network on the last contingency.

| study | boundary | worst case after the click |
|---|---|---|
| `mc` | baseline: bottom of `mc_adequacy`'s batch loop; then between ELCC assets, inside the bisection `while` (`elcc.py:489-495`), and **between portfolio periods** (`portfolio.py:311-318`) | one batch (`draws`) in the baseline; one probe at `n_fixed` thereafter |
| `frontier` | between ε points | in-flight solve **+ the closing restore solve** |
| `fmea_sweep` | between contingencies **and between class B and class C** | in-flight solve + restore, per sweep that runs |

*Bottom* of `mc_adequacy`'s loop: a check before the first `_simulate_blocks`
leaves `parts[lab][0]` empty and `np.concatenate` raises. *Inside* the
bisection `while` (`elcc.py:489-495`), never before it — both bracket ends are
probed first (the Δ = 0 probe at `elcc.py:466`, `lole_at(nameplate)` at
`elcc.py:473`), and an abort between them would leave `hi = nameplate`
unprobed.

**The `fmea_sweep` worker runs TWO sweeps** (`results.py:3059-3066`), so the
flag is checked between them; otherwise class C runs in full after class B was
stopped. When class C is skipped, class B ran with `final_state_update=None`
and its closing re-solve wrote a private sink, so the foreground `_state`
results are the pre-study ones — correct, and stated.

**The CRN contract.** `mc_adequacy(..., stop_event=None)` is honoured **only**
for the `/mc` worker's baseline call; `elcc_of_removal`, `elcc_of_portfolio`
and both loops pass `None` explicitly, with a comment at each site. Otherwise
a truncated candidate is compared against a full-budget baseline, CRN breaks,
and a wrong `elcc_mw` ships as `status="ok"` — which `baseline_key` cannot
catch, because it hashes the arguments and never the result.

**What an aborted study serves.** The headline metrics (self-describing
through `n_samples`, `converged=False`, `resolution_floor_h`) plus the rows
completed before the flag. A stopped bisection returns `status="aborted"` with
`elcc_mw=None` and the bracket `[lo, hi]` **inside `reason`** — the row's nine
keys are a closed set (`test_adequacy_mc_endpoint.py:38-41`, asserted at `:384`
and `:550`), so a tenth field would fail two shipped assertions. A stopped
portfolio sets a boolean **`truncated`** on the block, not a new block status:
`block["status"]` is already load-bearing on the success path
(`portfolio.py:466-469` sets `margin_unavailable` and still computes periods),
so overwriting it would erase a real disclosure, and `PortfolioSection`
renders `block.periods` independently of the status (`McPanel.tsx:737`).
`run_class_b_sweep` and `run_class_c_sweep` skip ids the partial dict does not
carry (today: `KeyError`).

**Routes.** `POST /results/{mc,frontier,fmea_sweep}/abort`, each the shipped
loop route's contract verbatim (`results.py:3616-3644`): body
`{"status": …, "aborting": status == "running"}` (which `LoopPanel.test.tsx:200`
depends on), 200 and idempotent even when the run is finishing, 404 only when
no run was ever recorded, and deliberately not folded into
`/simulation/abort`.

**Five things that are part of this change, not adjacent to it.**
1. **The GET filters** (`results.py:3000, 3100, 3210`) drop only `"thread"`;
   adding the event without widening them is a **500 on every poll**.
2. **`base_restored` reaches the wire.** The frontier computes it and the route
   discards it (`results.py:3151-3167`), and its `except` ignores
   `exc.frontier_result`. Onto the record, the GET and `FrontierPayload`. The
   sweep's closing re-solve moves into a `_restore_base`-shaped guard first —
   today unguarded, so a failing restore destroys the partial rows the abort
   exists to keep. It carries the **solver status**, not a bool:
   `run_simulation` returns `(status, condition)` and `_restore_base` throws it
   away (`frontier.py:138-142`), so `True` today means only "did not raise" and
   an `infeasible` restore reports success while the foreground holds a
   non-optimal plan.
3. **`/fmea_modes` gates on `status == "done"`** (`results.py:2954`); widened
   to `("done", "aborted")`.
4. **The workers need the loops' context pattern**: closed-over record +
   `copy_context()` + publish-and-start under `get_solver_state_lock()`
   (`results.py:4119-4129`). `post_frontier` and `post_fmea_sweep` write
   through `_state[key].update` — the anti-pattern the loops' comment names
   (`results.py:4117-4118`); `post_mc` closes over its record
   (`results.py:3374-3378`) but lacks the other two.
5. **`tests/test_golden_coverage.py`'s `ROUTE_SURFACES`** asserts the handler
   set EQUALS the allowlist (`:228-238`; the two loop aborts are at `:153,
   156`), so three entries are added; and `tests/fixtures/route_inventory_phase0.txt`
   is regenerated (`test_chat_tools_endpoint_map.py:15-17`).

**The copy.** `ABORTABLE_STUDIES` becomes all five; the route-equality test
(`test_adequacy_study_swap_guard.py:236-253`) asserts set equality both ways
and **goes green by itself**; the parametrized refusal test's `else` branch
becomes unreachable and is rewritten. `study_state.blocking_study_detail` has
offered an abort for all five **since Phase 7** (`study_state.py:83-86`) — a
pre-existing copy defect that becomes true here.

**Frontend.** The shipped abort button verbatim (`LoopPanel.tsx:367-371,
428-437`). Unions and label maps: `McStatus.status`, `ElccRow['status']` +
`STATUS_LABEL` (`McPanel.tsx:195`), `ElccPortfolioPeriodStatus` +
`PORTFOLIO_PERIOD_LABEL` (`McPanel.tsx:188`, rendered at `:769`) and
`PORTFOLIO_STATUS_LABEL` (`:180`) — a missing entry renders `undefined`
rather than failing `tsc`. `test_adequacy_mc_endpoint.py:551`'s closed set
widens.

## 2. Part B — `/copt` costs less

**Where the time goes**, measured at 300 units / 8760 h:

| term | 0 profiled | 8 profiled |
|---|---|---|
| `deconvolve` attempts (all fail at this size) | 6.5–6.8 s | 6.7–7.0 s |
| rebuild + shift | **4.2–6.7 s** | **3.8–6.2 s** |
| mixture inner loop | 0.1 s | **24.9–27.6 s** |

**Change 1 — delete the `deconvolve` call** from `attribute_criticality`
(`copt.py:740-745`). No predicate: on every fleet `/copt` can produce
(`delta_mw=1.0`, MW capacities, so `L/n ≳ 6`) the rebuild is 3.3–5× faster,
measured across n = 2…300 and q = 0.02…0.499. `deconvolve` stays public for
its round-trip test (`test_adequacy_copt.py:275-282`, its only other caller).

**Change 2 — bin the mixture inner loop.** `ES_d(x) = x·F_d[j] − Δ·G_d[j]`,
`j = clip(ceil(x/Δ − ε), 0, n_d)`, and the `x` cells depend only on the
residual and the mixed units. Bin once (`A[j] = Σ P[s]·w_h·x`,
`B[j] = Σ P[s]·w_h`); each counterfactual is
`EUE_d = Σ_j (A[j]·F_d[j] − Δ·B[j]·G_d[j])`, cells beyond a shorter table
folded into its last index exactly as `clip` does. The `k+1` histograms
accumulate in one pass over the `2^k` states.

**What this costs — two claims, two tolerances.**
* **Binning is exact**: worst rel **4.2e-11** at production scale, 2.2e-12 at
  benchmark scale. F2 pins it at **1e-8** against a reference that is the
  shipped mixture path **with the `deconvolve` call already removed**.
* **The deletion changes numbers**, bounded by the shipped mass guard's own
  1e-3 band (`copt.py:674`): measured 2.2e-13 here, 1.04e-4 on the review's
  n = 20 / q = 0.01 / k = 8 fixture. Its own ★ (F2e) pins the delta at **1e-3**
  with the guard named as the mechanism, and records that the rebuild is the
  accurate side. No claim of bit-identity anywhere.

**The switch: `binned iff k ≥ 2`, evaluated per period block.** v3's
machine-measured constant does not exist — the implied crossover ranges from
~10 (k = 0) to >210 (k = 3) on one machine, because the direct path costs
`α + β·H` per state with a large fixed `α` that a `2^k·H` term ignores, and
v3's rule mispredicted by up to 3.3×. The constant-free rule is wrong on only
two measured points (n = 300, k = 2, H ≤ 168: direct wins 1.3–1.6×, ≤35 ms)
and gives up at most **0.14 s** anywhere in a 60-point sweep — 2 % of this
phase's own floor. It is evaluated **per block**, because binned cost is
`n·KMAX_block` per block while direct is `n·2^k·H_block`: at 30 × 24 h blocks
with k = 0 the global rule would pay 3.0 s binned against 0.36 s direct.

**The target.** After both changes the cost is the rebuild, which this phase
does not touch, and it is flat across regimes: measured end to end
**300 u / 0 p: 13.4 → 6.4 s; 300 u / 8 p: 40.3 → 6.4 s; 100 u / 0 p:
1.04 → 0.28 s; 100 u / 8 p: 9.9 → 0.43 s** on the review's machine, and
~5 s at 300 units on mine. **State it as 5–6.5 s at 300 units, profiled or
not** — so S28's 10 s live gate carries ~1.5× headroom, not 2×.

**The floor, not fixed here.** The O(n²·L) rebuild. A divide-and-conquer
leave-one-out is the known answer: 0.408 s for all 300 tables in the v1
review's hands, 2.31 s against ~3.8 s (1.7×) in mine. Recorded as the next
cost item with both measurements, not promised.

**Regimes.** Netted units keep the direct path (their counterfactual shifts
the residual, a different cell set per unit, and the ceiling in `j(x)` leaves
no low-rank structure), so a profile-heavy fleet sees little of this.

**Determinism, scoped.** `(-delta_eue_mwh, name)` goes on
`attribute_criticality` (`copt.py:781`) and the 12d block merge
(`copt.py:879`); `/fmea_modes` (`results.py:2961`) gets
`(-criticality_eur_per_year, mode_id)`, because that is the quantity it ranks
on and a shipped test asserts it. The key fixes **constructed exact ties
only**: after change 1 two identical units may differ at ~1e-12 (each rebuild
convolves a different subset in a different order) and are then not tied at
all, and two distinct units within the binning error can still flip. Said
plainly rather than implied away.

**Also paying this cost:** `/fmea_modes` calls `get_copt()` directly
(`results.py:2949-2952`) under a different react-query key from the four
panels that share one. Noted, not changed. `/copt` releases the mutation lock
before the expensive part (`results.py:5075-5091`).

## 3. What does NOT change (stated)

- The `/copt` contract: a synchronous GET, same payload.
- `mixture_hourly`, `build_copt`, `_shift_deterministic`, `deconvolve` itself,
  12d's per-block screening, `lolp_max`, the netted rows' semantics.
- The mesh and its 409s (only the abort remedy changes); the two loops' code.
- SSE/log-queue plumbing; and both restores call `run_simulation` with a
  **fresh** `threading.Event`, so the study's flag cannot truncate a restore.

## 4. Tests

`tests/test_adequacy_abort.py`
- **F1 ★** per engine: the flag stops it at the second boundary; the record
  reaches `aborted` and keeps what completed. Bite: ignore it.
- **F1b ★** the restore runs: bite = **raise instead of break in
  `run_contingency_sweep`** (the frontier cannot fail this — a `break` inside
  its `try` still runs the `finally`).
- **F1b2 ★** a *failing* restore is survived: make `run_simulation` raise
  inside the sweep's restore; the partial rows survive and `base_restored`
  reports the failure. Bite: the unguarded re-solve.
- **F1c ★** idempotence: 200 finished, 404 no record, 200 twice; body carries
  `status` and `aborting`.
- **F1d ★** bounded work, **counted not timed**: the engine's boundary counter
  advances at most N after the flag. (Asserting the mesh reopens is vacuous —
  `record_is_running` reopens on thread death alone.)
- **F1e ★** the three GETs answer 200 with a serialisable body **while a run
  is live**. Bite: leave `stop_event` in the filter.
- **F1f ★** the CRN contract, by instrumenting `mc_adequacy` (the row's nine
  keys carry no `n_samples`): every ELCC evaluation of an aborted study ran at
  the baseline's `n_samples`. Bite: honour the flag inside `mc_adequacy` on
  the ELCC path.
- **F1g ★** a partial sweep reaches the worksheet and does not `KeyError`.
- **F1h ★** class C does not run after class B was aborted.
- **F1i ★** the refusal offers an abort for every key.

`tests/test_adequacy_copt_cost.py`
- **F2 ★** binning exactness **by name**, rel **1e-8**, against the shipped
  mixture path with `deconvolve` already removed — calling the binned
  implementation **directly**, not through the switch (v3's fixtures were all
  routed to the direct path, which made the test and both its bites vacuous):
  RTS-79, RBTS, an **off-grid** residual, a fold fixture, an empty table,
  Δ ≠ 1, mixed+netted. Bites: bin with `floor`; drop the fold.
- **F2b ★** the fold alone: folded `H·q·cap` against 0.0 unfolded.
- **F2c ★** the operation count on a **named** fixture whose path is stated:
  zero `deconvolve` calls, and one `mixture_hourly` call per netted unit
  (zero when none) on a `k ≥ 2` fixture. No timing assertion anywhere.
- **F2d ★** the switch itself: a `k = 1`, `H = 24` fixture takes the direct
  path and a `k = 3` fixture the binned one. Bite: invert the rule.
- **F2e ★** the deconvolve deletion's delta, rel **1e-3**, with the mass guard
  named; asserts the rebuild side is the one kept.
- **F2f** the sort key alone: a shuffled list with exactly equal
  `delta_eue_mwh` emerges name-ascending. Bite: `-ΔEUE` alone.

## 5. Docs and gates

MC spec **v1.8**; QA plan **S28**; the shipped record. Gates: adequacy suite;
full tree diffed against `base_fails_sorted.txt` **both ways**; frontend
vitest + `tsc`; live S15–S28 from an explicit `cd`, the server confirmed by
`Application startup complete` with no bind error.

**S28 (live):** start a ten-asset MC study, abort mid-run, and assert the POST
returns 200, the record reaches `aborted`, the next frontier POST is accepted
rather than 409, the three GETs answered 200 throughout, and `/results/copt`
on the 300-unit fixture returns in under 10 s. Bitten live by ignoring the
flag.

## 6. Out of scope, stated

- The O(n²·L) rebuild; making `/copt` asynchronous; aborting mid-solve.
- The static-CF flag and the derate NaN rule; the `validate` TOCTOU.
- `/fmea_modes`' duplicate `/copt` cost and its separate query key.

## 12e SHIPPED (2026-09-04)

Built as planned. **Part A**: `stop_event` on all three records with the
loops' pattern (closed-over record, `copy_context()`, publish-and-start under
one lock hold); three `/abort` routes with the shipped contract; the three GET
filters widened; `run_frontier_sweep` gains `aborted`, `run_contingency_sweep`
gains `aborted` + a guarded `_restore_base_guarded` carrying the solver status;
both row assemblers skip ids a partial dict lacks; `/fmea_modes` accepts
`("done", "aborted")`; the sweep worker checks the flag between class B and
class C; `mc_adequacy` checks at the bottom of its batch loop and every replay
call site passes `stop_event=None`; the bisection checks inside its `while`
and returns `aborted` with the bracket in `reason`; the portfolio stops
between periods and sets `truncated`; `ABORTABLE_STUDIES` is all five and the
A3 test is rewritten; three entries added to `ROUTE_SURFACES`. Frontend: abort
buttons on the MC, frontier and sweep panels (the shipped pattern verbatim),
`McStatus`/`ElccRow`/`ElccPortfolioPeriodStatus` widened with their label-map
entries, `truncated` and `base_restored` typed, and a warning when the
frontier's closing re-solve did not run.

**Part B**: the `deconvolve` call deleted from `attribute_criticality`;
`_eue_cells` / `_eue_binned` with `BIN_MIN_MIXED = 2`; `(-ΔEUE, name)` at both
COPT sort sites (`/fmea_modes` keeps its own criticality key).

**Measured, end to end, 8760 h** (`screening_analysis`, this machine):

| fleet | before | after |
|---|---|---|
| 300 units, 0 profiled | 12.2 s | **4.24 s** |
| 300 units, 8 profiled | 37.8 s | **4.32 s** |
| 100 units, 0 profiled | 1.04 s | **0.25 s** |
| 100 units, 8 profiled | 9.9 s | **0.58 s** |

Flat across regimes, as §2 predicted, and below the 5–6.5 s the plan stated.
Binning exactness re-measured against a deconvolve-free reference: worst rel
**6.2e-13** over off-grid, Δ = 2.5, mixed+netted, empty-table and fold
fixtures.

**Tests.** `test_adequacy_abort.py` 15, `test_adequacy_copt_cost.py` 12.

**Bites — 17, all bite.** Part B: floor instead of the grid rule; drop the
fold; restore the per-unit mixture loop; invert the switch; `-ΔEUE` alone at
the row sort; `-ΔEUE` alone at the block merge. Part A: forward the flag into
`mc_adequacy`; return `ok` from a stopped bisection; check at the TOP of the
batch loop; portfolio ignores the flag; sweep raises instead of breaking;
unguarded restore; index instead of `.get`; frontier ignores the flag; 409 on
a finished run; `stop_event` left in the GET filters (all three 500); the
`("done",)` worksheet gate.

**Three bites did not bite on the first attempt and were replaced, not
counted.** (1) F2d asserted the FIXTURE (`k >= BIN_MIN_MIXED`) rather than
which path ran, and both paths agree by construction — rewritten to count
`mixture_hourly` calls. (2) F2f sorted a list in the test body, so it tested
Python's `sorted` and no mutation of the engine could reach it — rewritten to
drive `attribute_criticality` on a `q = 0` fleet, where every ΔEUE is exactly
0.0 and the tie is real. (3) F1f needed TWO fixture corrections: a one-batch
replay exits on `n_total >= max_draws` before any stop check, and an event
armed BETWEEN evaluations lets the bisection return before another probe
runs — so neither could observe a forwarded flag. With a multi-batch baseline
and the event armed from inside `_simulate_blocks`, the broken variant is
visible as sample counts `[64, 32, 8]` against `[64, 64, 64]`.

**A fourth bite that did not bite, recorded.** The first LIVE bite removed the
stop check from `mc_adequacy`'s batch loop and S28 still passed — the
fixture's study carries an ELCC asset, so the *between-assets* check stops it
even with the batch-loop check gone. Defence in depth working as designed
rather than a test failing to test; but a bite must target the boundary its
fixture reaches, and the recorded one (the flag made inert in the `/mc`
worker) does: S28.1 then reads `status = "done"`.

**A harness lapse, recorded.** A bare `git checkout` (no arguments) was typed
into one bite command line. It takes no arguments, so it only printed status
and changed nothing — but it is the command this session bans outright after
the 12c incident where `git checkout -- routers/results.py` discarded two
uncommitted fixes, and it should not have been typed at all. The restore in
that same command was by saved copy and sha256, as the rule requires.

**The live suite caught a consequence the unit tests could not.** S20.2 —
shipped in Phase 11 — asserted that the swap refusal names the study and
"offers only a REAL remedy", which it encoded as *"cannot be aborted" present,
"or abort it" absent*, because for the MC that was the honest copy while the
control did not exist. Phase 12e inverts it: the MC now has an abort route, so
the refusal offers one and the assertion had to flip with it. The rule it
tests is unchanged — never name a control the user does not have — and the
check is what noticed that the copy had moved underneath it. The unit-side
twin (A3 in `test_adequacy_study_swap_guard.py`) was rewritten in the same
way, for the same reason.
