# Phase 12e — every study can be stopped, and `/copt` costs less (plan, v2)

Supersedes v1 (`2026-09-04-fmea-phase12e-abort-and-copt-cost-v1.md`,
**rejected**: two blockers — a wrong cost model whose headline target was
refuted by the plan's own timing table, and an abort design that would have
silently mis-priced every ELCC credit). v1 is kept with its review. Every
amendment is answered by number in §0.

## 0. What v1 got wrong, and what each blocker gets

**Blocker 1 — the cost model named the wrong term.** v1 said `/copt`'s 31 s is
`300 × 2^k × H` mixture evaluations. Measured (re-verified here, not taken
from the review): at 300 units / 8760 h / 8 profiled the split is **13.6 s
building counterfactual tables + 24.9 s mixture**; with **zero** profiled
units — where `2^k = 1` and v1's mechanism contributes nothing — the whole
8.1 s is still the table half. The cause is `deconvolve`: it amplifies by
`1/q` per grid step and its mass check fails on any long table, so above
~30–40 units it fails for **every unit** (measured **300/300** at n=300,
**4.28 s** spent on attempts that cannot succeed) and each falls through to
`build_copt([v for v in units if v.name != u.name])` — an O(n²·L) rebuild.
v1's "31.18 s → under 2 s" was unreachable while §3 promised `deconvolve`
and `build_copt` would not change.

**Blocker 2 — `mc_adequacy` is not the MC study's function.** It is the shared
evaluator: `elcc.metrics_at` calls it with `cov_target=_NEVER_CONVERGE,
max_draws=n_fixed` precisely so every candidate replays the baseline's batch
sequence bit for bit, and `portfolio.elcc_of_portfolio` and both loops call it
the same way. A stop flag inside its batching loop truncates candidate
evaluations against a full-budget baseline, breaks CRN, and returns a **wrong
`elcc_mw` with `status="ok"`** — undetectable downstream, because
`baseline_key` hashes the *arguments*, never the result. In a phase whose
premise is "neither changes a single number the engines report", that is the
one thing that must not ship.

| v1 finding | v2 |
|---|---|
| **1** cost model / unreachable target | §2 rewritten on the measured split; the target is what the change actually buys, stated per regime |
| **2** stop event in `mc_adequacy` | §1: the flag reaches `mc_adequacy` ONLY from the `/mc` worker's own baseline; every other call site passes `None` explicitly, and a ★ pins that an aborted study's ELCC rows all ran at the baseline's `n_samples` |
| **3** `stop_event` breaks the three GETs | §1: the three GET filters are part of the change, with a ★ that polls a LIVE run (v1 would have shipped a 500 on every poll) |
| **4** sweep's restore is outside its `finally` | §1: the abort is a `break`, never an exception, and §1 says why; the engines gain `aborted` and the row assemblers skip missing ids |
| **5** `base_restored` never reaches the wire | §1: onto the frontier record, the GET and `FrontierPayload`; `post_frontier`'s `except` reads `exc.frontier_result` |
| **6** exactness tolerance / row order | §4 F2: rel 1e-9, compared BY NAME; and `attribute_criticality` sorts on `(-ΔEUE, name)` so ties are deterministic and "unchanged field for field" becomes true |
| **7** `/fmea_modes` drops aborted rows | §1: the gate widens to `("done", "aborted")` |
| **8** truncated baseline honest/poison | §1: headline metrics + rows completed before the flag; never a truncated baseline into a new asset |
| **9** `ABORTABLE_STUDIES` mis-described | §1: the route-equality test needs NO change and goes green by itself; the dying `else` branch is rewritten; `blocking_study_detail` is a pre-existing copy defect this phase closes |
| **10** latency table understated | §1: frontier/sweep = in-flight solve **+ restore solve**; mc = one ELCC asset unless probes are checked, and they are |
| **11** binned slower with no profiled units | §2: the direct path is kept when `mixed` is empty; the crossover is stated |
| **12** netted units keep the direct path | §2: stated as a regime, with Q1's answer (no shared histogram exists) |
| **13** `k+1` histograms in one pass | §2 |
| **14** Q4/Q5 | §4 F2c is an operation-count assertion, not a print-only timer |
| **15** floor bite vacuous on-grid | §4 F2's bite fixture is off the Δ grid |
| **16** F3 describes a pattern that does not exist | §1: the shipped button verbatim; `McStatus` gains `'aborted'` |
| **17** omissions / citations | §2, §3, §5 |

## 1. Part A — the three studies get a stop event

**The flag is cooperative and it is a `break`, never an exception.**
`run_contingency_sweep`'s closing base re-solve sits *outside* its
`try/finally` (`sweep.py:219-231`: the `finally` only calls `unfreeze()`),
unlike the frontier's (`frontier.py:228-230`). An abort raised as an
exception would therefore skip the sweep's restore and leave the network's
result frames on the last contingency while `_state` still holds the
pre-study results. So: a checked flag, a `break`, and the closing re-solve
runs on every path.

**Where the check goes, and what it costs.**

| study | boundary | worst case after the click |
|---|---|---|
| `mc` | between ELCC assets **and** between bisection probes; between portfolio periods; inside `mc_adequacy` ONLY for the worker's own baseline, at the BOTTOM of the batch loop | one probe (a full evaluation at `n_fixed`) |
| `frontier` | between ε points | the in-flight solve **+ the closing restore solve** |
| `fmea_sweep` | between contingencies | the in-flight solve **+ the closing restore solve** |

The bottom of `mc_adequacy`'s loop, not the top: a check before the first
`_simulate_blocks` leaves `parts[lab][0]` empty and
`np.concatenate` raises `need at least one array to concatenate`, so the
study would report `failed` with a numpy message instead of `aborted`.

**A stopped bisection does not return `ok`.** Stopping between probes is safe
— every probe is a complete evaluation and `hi` is by the loop's own invariant
"the smallest Δ *known* to restore the baseline" — but the answer is then an
upper bound on a bracket that was never closed. It is returned as
`status="aborted"` with a reason naming `[lo, hi]`, never as a credit.

**The CRN contract is not negotiable.** `mc_adequacy(..., stop_event=None)`
is honoured only for the `/mc` worker's baseline call. `elcc_of_removal`,
`elcc_of_portfolio` and both loops pass `None` explicitly, and a comment at
each site says why. ★ pinned: every ELCC row of an aborted study ran at
`n_samples == baseline["n_samples"]`.

**What an aborted study serves.** The headline metrics (they describe
themselves through `n_samples`, `converged=False` and `resolution_floor_h`,
all defined from what actually ran) plus the rows that completed before the
flag; a new asset is never started, and a truncated baseline is never fed to
one. The frontier's completed points and the sweep's completed rows likewise
— which requires `run_class_b_sweep` / `run_class_c_sweep` to **skip ids the
partial dict does not carry** (today: `KeyError`, reported as an opaque
`failed`).

**Routes.** `POST /results/{mc,frontier,fmea_sweep}/abort`, each the shipped
loop route's contract verbatim (`results.py:3616-3644`): 200 and idempotent
even when the run is finishing, 404 only when no run was ever recorded.

**Three things v1 missed that are part of this change.**
1. **The GET filters.** The loops' GETs drop `("thread", "stop_event")`; the
   three new ones drop only `"thread"` (`results.py:2999, 3103-3105, 3212`).
   Adding the event without widening them is a **500 on every poll**.
2. **`base_restored` reaches the wire.** The frontier engine computes it and
   the route discards it (`results.py:3151-3167`), and the `except` branch
   ignores `exc.frontier_result` — so an aborted run would lose its completed
   points too. Onto the record, the GET and `FrontierPayload`; the sweep gains
   the same field.
3. **`/fmea_modes` gates on `status == "done"`** (`results.py:2954`), so an
   aborted sweep's rows never reach the worksheet the FMEA tab renders. The
   gate widens to `("done", "aborted")`.

**The copy.** `ABORTABLE_STUDIES` becomes all five keys. The route-equality
test (`test_adequacy_study_swap_guard.py:236-253`) already asserts set
equality **both ways** and needs no change — it goes green by itself. The
parametrized refusal test's `else` branch ("cannot be aborted") becomes
unreachable for every key and is rewritten to assert the single sentence.
And `study_state.blocking_study_detail` has said "*Wait for it to finish, or
abort it*" **unconditionally for all five studies since Phase 7** — a
pre-existing copy defect, true for the first time after this phase, and
recorded as such rather than claimed as new work.

**Frontend.** The shipped abort button **verbatim** (`LoopPanel.tsx:367-371,
428-437`): rendered only while running, no disabled state, no "aborting…"
label, invalidate on success — because that is what exists, and adding three
states to five panels is a different change. `McStatus.status` gains
`'aborted'`.

## 2. Part B — `/copt` costs less, on the measured terms

**Where the time actually goes**, re-measured here (`scratchpad/proto12e`,
`review12e`), 300 units / 8760 h:

| term | 0 profiled | 8 profiled |
|---|---|---|
| counterfactual tables (incl. doomed `deconvolve`) | **8.1 s** | 13.6 s |
| — of which `deconvolve` attempts that cannot succeed | 4.3 s | 4.3 s |
| mixture inner loop | 0.05 s | **24.9 s** |

**Two changes, both measured, neither touching an engine's answer.**

1. **Stop attempting a `deconvolve` that cannot succeed.** A cheap
   precondition on table length and `q` (the amplification `1/q` per grid step
   overflows the mass check above a computable length) skips straight to the
   rebuild. Pure waste removed: **4.3 s at n = 300**, on every fleet, profiled
   or not. The rebuild path is unchanged, so every number is bit-identical.
2. **Bin the mixture inner loop, when there is one.** `ES_d(x) = x·F_d[j] −
   Δ·G_d[j]` with `j = clip(ceil(x/Δ − ε), 0, n_d)`, and the `x` cells depend
   only on the residual and the mixed units — not on the distribution. So bin
   once and every counterfactual is a dot product:
   `EUE_d = Σ_j (A[j]·F_d[j] − Δ·B[j]·G_d[j])`, with cells beyond a shorter
   table folded into its last index exactly as `clip` does. Verified exact on
   20 fixtures including RTS-79 and RBTS (worst rel **3.0e-11** at 300 units;
   1e-13 at benchmark scale). **Applied only when `mixed` is non-empty**: with
   no profiled units the binned path is measurably *slower* (0.102 s vs
   0.070 s at 300 u — `KMAX ≈ peak/Δ` bins against `H` hours), so the
   crossover `2^k·H` vs `KMAX` is the switch, and every network without
   profiled units keeps today's path byte for byte.

**Scope, honestly.** These two remove **the whole mixture term and the pure
waste**: 300 u / 8 profiled / annual **38.5 s → ~9.3 s**; 300 u / 0 profiled
**8.1 s → ~3.8 s**; 100 units (the ordinary case) already under 1 s and
unchanged. What remains is the O(n²·L) **rebuild**, and this phase does not
fix it. A divide-and-conquer leave-one-out is the known answer — the review
measured 0.408 s for all 300 tables — but my own prototype of it reached only
**2.31 s against ~3.8 s (1.7×)**, so the 36× version needs an implementation
this plan has not got. **Recorded as the next cost item, with both
measurements, rather than promised here.** `/copt` therefore stays
synchronous: at 100 units it is under a second, and the pathological case is
a 300-unit fleet, now 4–9 s rather than 31 s.

**Regimes, stated.**
- **Netted units** (only when more than `K_EXACT = 8` units carry a profile)
  keep the direct path: their counterfactual changes the *residual*
  (`r − q_j·a_j`), a different cell set per unit, and the ceiling in `j(x)`
  destroys any low-rank structure (Q1 — v1's "rank-1" intuition was wrong).
  A fleet where most units are profiled sees little of this speedup.
- **Mixed units** need one histogram each (their counterfactual fixes
  `s_i = 1`, changing the cells), but all `k+1` accumulate in ONE pass over
  the `2^k` states.
- **Per period block** (12d): the histogram is rebuilt per block; the
  arithmetic is unchanged (blocks partition hours, ΔEUE merges by name) and
  the total cell count is the same, but the *table* term multiplies by the
  block count. The targets above are single-block.

**Determinism.** `attribute_criticality` sorts on `(-delta_eue_mwh, name)`
instead of `-delta_eue_mwh` alone, so exactly-tied rows (identical unit types
tie to the last bit) order identically on both paths and "the payload is
unchanged field for field" is true rather than nearly true.

**Also paying this cost:** `/fmea_modes` calls `get_copt()` directly
(`results.py:2949-2952`) under a *different* react-query key from the three
panels that share one, so opening the FMEA tab pays it a second time. Noted,
not changed. And `/copt` releases the mutation lock before the expensive part
(`results.py:5074-5090`), which is why this is an operability defect and not
a mesh defect.

## 3. What does NOT change (stated)

- The `/copt` contract: a synchronous GET, same payload.
- `mixture_hourly`, `build_copt`, `_shift_deterministic`, the per-block
  screening of 12d, `lolp_max`, the netted rows' semantics and note.
- The mutual-exclusion mesh and its 409s (only the abort remedy changes).
- The two loops' code.
- SSE/log-queue plumbing: `post_frontier` and `post_fmea_sweep` pass no
  `log_queue`, so the restores fall back to a private `SimpleQueue`. Nothing
  to plumb.
- Abort during a restore: both restores call `run_simulation` with a **fresh**
  `threading.Event`, so the study's stop event cannot truncate the restore and
  no partial result is written over a good one.

## 4. Tests

`tests/test_adequacy_abort.py`:
- **F1 ★** per engine: a flag set before the second boundary stops it there;
  the record reaches `aborted` and keeps what completed. Bite: ignore the flag
  in the loop. (For `mc` the bite is at the *caller's* loop — see F1f.)
- **F1b ★** the restore: an aborted frontier still restores the base plan and
  says so on the wire. Bite: check the flag *before* the `try` (a `break`
  inside it still runs the `finally`, so that variant is not constructible).
- **F1c ★** idempotence: 200 on a finished run, 404 with no record, 200 twice.
- **F1d ★** the mesh reopens: after the worker is joined, the next study POST
  is accepted, not 409.
- **F1e ★** the GETs while a run is LIVE: `/results/{mc,frontier,fmea_sweep}`
  each return 200 with a serialisable body. Bite: drop `stop_event` from the
  filter — the poll 500s.
- **F1f ★** the CRN contract: every ELCC row of an aborted study ran at
  `n_samples == baseline["n_samples"]`. Bite: honour the flag inside
  `mc_adequacy` on the ELCC path.
- **F1g ★** a partial sweep reaches the worksheet: `/fmea_modes` includes the
  aborted sweep's rows, and `run_class_b_sweep` does not `KeyError` on a
  partial dict. Bite: the `("done",)` gate.
- **F1h ★** the copy: the refusal offers an abort for every key. Bite: leave a
  key out of `ABORTABLE_STUDIES` (the route-equality test then fails too,
  which is the point).

`tests/test_adequacy_copt_cost.py`:
- **F2 ★** exactness: every criticality row equals the shipped implementation
  (kept as `_attribute_criticality_direct`) **by name**, rel **1e-9**, on
  RTS-79, RBTS, an off-grid-residual fixture, a fold fixture, an empty table,
  Δ ≠ 1, and a mixed+netted fleet. Bites: bin with `floor` (fixture residual
  **off the Δ grid**, else it is vacuous); drop the beyond-table fold.
- **F2b ★** the fold alone: a residual far beyond the shortest table —
  folded 8256.0 against 0.0 unfolded.
- **F2c ★** the claim, machine-independently: on the 300-unit / 8-profiled
  fixture `attribute_criticality` performs **O(k)** `mixture_hourly` calls,
  not O(n) (counted by monkeypatch), and **zero** `deconvolve` attempts on a
  fleet where the precondition says it cannot succeed. The wall time is
  printed beside it as information, never asserted. Bite: restore the O(n)
  loop.
- **F2d ★** determinism: tied rows order identically on both paths. Bite:
  sort on `-ΔEUE` alone.

## 5. Docs and gates

MC spec **v1.8** (the abort contract and where the flag may not go; the
attribution's binned form and its regime); the QA plan gains **S28**; this
plan's shipped record. Gates on the final commit: adequacy suite; full tree
diffed against `base_fails_sorted.txt` **in both directions** (branch-minus-
master EMPTY); frontend vitest + `tsc`; live S15–S28 on the uvicorn server,
started from an explicit `cd` and confirmed by `Application startup complete`
with no bind error.

**S28 (live):** start a ten-asset MC study, abort it mid-run, and assert the
POST returns 200, the record reaches `aborted`, the subsequent frontier POST
is accepted rather than 409, the three GETs answered 200 throughout, and
`/results/copt` on the 300-unit fixture returns in under 10 s. Bitten live by
ignoring the stop event.

## 6. Out of scope, stated

- The O(n²·L) counterfactual rebuild (§2: measured, recorded, not promised).
- Making `/copt` asynchronous (§2: the cost is reduced, not relocated).
- Aborting mid-solve: the boundary is between solves, so an abort during an
  eight-minute HiGHS call waits for it.
- The static-CF per-asset flag and the margin derate's NaN rule; the
  `validate` route's TOCTOU (both backlog).
- `/fmea_modes`' duplicate `/copt` cost and its separate query key (noted).

## 7. Open questions for the review

1. **The `deconvolve` precondition.** Is a length/`q` bound the right test, or
   should the attempt be dropped entirely in favour of the rebuild (the mass
   check exists for a reason — which fleets does it still save)?
2. **Is `status="aborted"` on a stopped bisection enough**, or should the
   partial bracket not be served at all?
3. **The `aborted` flag on the sweep engines** — is adding a key to
   `run_contingency_sweep`'s return dict safe for every consumer (class B, C,
   and the route), or does it need a new shape?
4. **Do the three study records need the loops' closed-over pattern** (they
   write through `_state[key].update` inside the worker, which the loops'
   comment calls "the anti-pattern this deliberately does not copy"), or does
   `study_swap_refusal` make that moot?


## v2 REVIEW (2026-09-04, adversarial subagent) — REJECT, return for a v3

Two blockers, both verified against the code before being recorded, and both
the same class v1 was rejected for.

1. **BLOCKER — the `deconvolve` precondition cannot exist, and it was the
   wrong question.** The recursion steps back by `k1 = floor(cap/Δ)` per grid
   index, so the amplification count is `L/k1` and the UNIT'S OWN CAPACITY is
   in the predicate: at one table and one `q`, units below ~102 MW fail and
   units above succeed. Two fleets identical in `L` and `q` measured 0/104
   against 26/26. A `(length, q)` rule must skip successes or keep failures.
   And the attempt is never worth making: `deconvolve` is a Python loop over
   `L` while `build_copt` is `n−1` vectorised convolutions, so the rebuild is
   faster EVEN WHEN THE DECONVOLUTION SUCCEEDS (re-measured here: 0.64 vs
   0.14 ms/unit at n = 10 with 8/8 succeeding; 10.34 vs 5.15 at n = 200 with
   8/8). v2's "every number is bit-identical" was also false — swapping a
   succeeding deconvolve for the rebuild drifts ΔEUE by up to 9.4e-10 rel on
   the review's fixture (2.2e-14 on mine), inside v2's own 1e-9 tolerance.
2. **BLOCKER — the cost table was internally inconsistent.** Subtracting v2's
   own rows leaves a residual rebuild term of 3.8 s at 0 profiled and 9.3 s at
   8 profiled — the same O(n²·L) rebuild over the same table, which cannot
   cost 2.4× more because eight units moved into the mixture. Re-measured
   here: **4.16 s against 3.79 s**, flat. v2's "8.1 s → ~3.8 s" was BELOW the
   floor it froze, and end to end both regimes land at ~5.2 s.

Eleven further findings, each reproduced: the `fmea_sweep` abort would stop
one sweep but not the study (the worker runs class B then class C, so class C
would run in full); `ElccRow['status']` is a closed frontend union with a
`Record` label map, so an `aborted` row renders `undefined` and v2 widened
only `McStatus`; the bisection check as phrased could land before the bracket
probes, leaving `hi` unprobed; the sweep's closing re-solve has no
`_restore_base`-shaped guard, so a failing restore destroys the partial rows
the abort exists to keep; F1d is vacuous (`record_is_running` reopens the mesh
on thread death alone, so it passes with the flag ignored), F1b bites the one
engine that cannot fail, and F2d's bite cannot fire (`list.sort` is stable and
both paths build `todo` in the same order); the determinism key was applied at
one of three sort sites; an aborted portfolio reports `status: "ok"` with
periods missing; `post_frontier` and `post_fmea_sweep` write through
`_state[key].update` without `copy_context()`, so "the record reaches
`aborted`" is not a claim the poller can see; and the binned/direct switch
stated two contradictory rules, of which "`mixed` non-empty" makes the
common one-profiled-unit short-horizon case — 12d's own per-block regime —
**1.5–1.9× slower**.

Superseded by **v3** (`...-v3.md`), which deletes the `deconvolve` call
outright rather than predicating it, states ONE cost target from the measured
rebuild floor (~5 s at 300 units, profiled or not), switches on the measured
crossover with the losing regime named, and answers the remaining eleven by
number.
