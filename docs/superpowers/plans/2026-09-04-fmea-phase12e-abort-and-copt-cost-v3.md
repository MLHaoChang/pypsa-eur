# Phase 12e — every study can be stopped, and `/copt` costs less (plan, v3)

Supersedes v2 (**rejected**: two blockers — a precondition that cannot exist,
and a cost table refuted by its own arithmetic) and v1 (**rejected**: a wrong
cost model and an abort design that would have mis-priced every ELCC credit).
Both are kept with their reviews. Every v2 amendment is answered by number in
§0; v1's review is appended to v1 in full.

## 0. What v2 got wrong

**Blocker 1 — the `deconvolve` precondition cannot exist, and the question was
the wrong one.** v2 proposed skipping attempts that cannot succeed via "a
cheap precondition on table length and `q`". The recursion steps back by
`k1 = floor(cap/Δ)` per grid index, so the amplification count is `L/k1` and
the unit's own capacity is in the predicate: at one table, one `q`, units
below ~102 MW fail and units above it succeed (measured). Two fleets with
identical `L` and identical `q` differ 0/104 against 26/26. A `(L, q)` rule
must either skip successes or keep failures.

The right question is whether the attempt is worth making at all, and the
answer is **no**: `deconvolve` is a Python loop over `L` while `build_copt` is
`n−1` vectorised numpy convolutions, so the rebuild wins **even when the
deconvolution succeeds** — measured, ms per unit:

| n | q | succeeded | deconvolve | rebuild |
|---|---|---|---|---|
| 10 | 0.05 | 8/8 | 0.64 | **0.14** |
| 45 | 0.05 | 8/8 | 2.41 | **0.68** |
| 100 | 0.40 | 8/8 | 5.22 | **1.86** |
| 200 | 0.45 | 8/8 | 10.34 | **5.15** |
| 300 | 0.45 | 0/8 | 14.71 | **10.95** |

So the call goes, with no predicate to calibrate. §7 Q1's own question —
"which fleets does the mass check still save?" — is answered *none*.

**Blocker 2 — v2's cost table was internally inconsistent.** Subtracting its
own rows left a residual rebuild term of 3.8 s at 0 profiled and 9.3 s at 8
profiled — the same O(n²·L) rebuild over the same table, which cannot cost
2.4× more because eight units moved into the mixture. Measured directly, the
rebuild+shift term is **4.16 s (0 profiled) against 3.79 s (8 profiled)** —
flat. v2's "8.1 s → ~3.8 s" was *below the floor it froze*. This is the class
of defect v1 was rejected for, reintroduced in the other direction.

| v2 finding | v3 |
|---|---|
| **1** precondition cannot exist | §2: the call is deleted, no predicate; the numeric consequence is owned and bounded (§2 "What this costs") |
| **2** cost table inconsistent | §2: ONE target for both regimes, from the measured rebuild floor |
| **3** binned/direct switch | §2: the measured crossover, with the losing regime named |
| **4** sweep abort stops one sweep, not the study | §1: checked in the contingency loop AND between class B and class C |
| **5** `ElccRow` union / bracket probes | §1: the union, the label map and the backend set widen; the check sits INSIDE the bisection `while` |
| **6** sweep's restore has no guard | §1: into a `_restore_base`-shaped helper; what `base_restored` does and does not assert |
| **7** F1d vacuous, F1b bites the safe engine, F2d cannot fire | §4 rewritten |
| **8** one of three sort sites | §2: all three, or the claim is scoped |
| **9** aborted portfolio looks `ok` | §1: a truncation status, frontend union widened |
| **10** Q4: the workers need `copy_context()` | §1 |
| **11** amendment count | this table; v1's full review is appended to v1 |
| **12-13** citations, F2c counts | §1, §2, §4 |

## 1. Part A — the three studies get a stop event

**Cooperative, a `break`, never an exception** — `run_contingency_sweep`'s
closing base re-solve sits outside its `try/finally` (`sweep.py:219-231`; the
`finally` only calls `unfreeze()`), unlike the frontier's
(`frontier.py:228-230`), so an abort raised as an exception would skip the
restore and leave the network on the last contingency.

**Where the flag is checked, and what it costs.**

| study | boundary | worst case after the click |
|---|---|---|
| `mc` | baseline: bottom of `mc_adequacy`'s batch loop; then between ELCC assets and INSIDE the bisection `while` | one batch (`draws`, default 500) in the baseline; one probe at `n_fixed` thereafter |
| `frontier` | between ε points | in-flight solve **+ the closing restore solve** |
| `fmea_sweep` | between contingencies **and between class B and class C** | in-flight solve + restore, per sweep that runs |

*Bottom* of `mc_adequacy`'s loop: a check before the first `_simulate_blocks`
leaves `parts[lab][0]` empty and `np.concatenate` raises. *Inside* the
bisection `while` (`elcc.py:513-520`), never before it: both bracket ends are
probed before the loop (`elcc.py:497, 503`), and an abort between them would
leave `hi = nameplate` unprobed — an upper bound nothing established.

**The `fmea_sweep` worker runs TWO sweeps.** `post_fmea_sweep` calls
`run_class_b_sweep` and then, if scenarios were requested,
`run_class_c_sweep` (`results.py:3059-3066`). Breaking out of class B's
contingency loop returns control here and class C would run in full, so the
worker checks the flag between them. When class C is skipped, class B ran with
`final_state_update=None` and its closing re-solve wrote a private sink — so
the foreground `_state` results are the pre-study ones, which is correct and
is stated rather than discovered.

**The CRN contract.** `mc_adequacy(..., stop_event=None)` is honoured **only**
for the `/mc` worker's own baseline call; `elcc_of_removal`,
`elcc_of_portfolio` and both loops pass `None` explicitly, with a comment at
each site. Otherwise a truncated candidate is compared against a full-budget
baseline, CRN breaks, and a wrong `elcc_mw` ships as `status="ok"` — which
`baseline_key` cannot catch, because it hashes the arguments and never the
result. ★ pinned (F1f).

**What an aborted study serves.** The headline metrics (self-describing
through `n_samples`, `converged=False`, `resolution_floor_h`, all computed
from what ran) plus the rows completed before the flag. A stopped bisection
returns `status="aborted"` with the bracket `[lo, hi]` and `elcc_mw=None` —
never a credit. A stopped portfolio records the truncation as a block status
rather than a silently short `periods` list. `run_class_b_sweep` and
`run_class_c_sweep` **skip ids the partial dict does not carry** (today:
`KeyError`, surfaced as an opaque `failed`).

**Four things that are part of this change, not adjacent to it.**
1. **The GET filters.** The loops' GETs drop `("thread", "stop_event")`; the
   three new ones drop only `"thread"` (`results.py:3000, 3100, 3210`).
   Adding the event without widening them is a **500 on every poll** (F1e).
2. **`base_restored` reaches the wire.** The frontier computes it and the
   route discards it (`results.py:3151-3167`), and its `except` ignores
   `exc.frontier_result`, so an aborted run would lose its completed points.
   Onto the record, the GET and `FrontierPayload`. The sweep's closing
   re-solve moves into a `_restore_base`-shaped guard first — today it is
   unguarded, so a failing restore destroys the partial rows the abort exists
   to keep. **`base_restored=True` means "the restore did not raise", not
   "the plan is back"**, and the plan says so.
3. **`/fmea_modes` gates on `status == "done"`** (`results.py:2954`), so an
   aborted sweep's rows never reach the worksheet the FMEA tab renders. The
   gate widens to `("done", "aborted")` (F1g).
4. **The workers need the loops' context pattern.** `_state` resolves through
   a `ContextVar` a bare `Thread` does not inherit; `post_mc` already closes
   over its record (`results.py:3374-3378`) but `post_frontier` and
   `post_fmea_sweep` write through `_state[key].update` — the anti-pattern the
   loops' own comment names (`results.py:4117-4118`). All three take the
   loops' shape: closed-over record + `copy_context()` + publish-and-start
   under `get_solver_state_lock()` (`results.py:4119-4129`). Without it,
   "the record reaches `aborted`" is not a claim the poller can see.

**The copy.** `ABORTABLE_STUDIES` becomes all five. The route-equality test
(`test_adequacy_study_swap_guard.py:236-253`) asserts set equality both ways
already and **goes green by itself**; the parametrized refusal test's
`else` branch becomes unreachable and is rewritten to one sentence. And
`study_state.blocking_study_detail` has offered an abort for all five studies
**since Phase 7** (`study_state.py:83-86`) — a pre-existing copy defect that
becomes true here, recorded rather than claimed as new work.

**Frontend.** The shipped abort button verbatim (`LoopPanel.tsx:367-371,
428-437`: visible only while running, no disabled state, no "aborting…"
label, invalidate on success). `McStatus.status` gains `'aborted'`;
`ElccRow['status']` gains `'aborted'` and `McPanel.tsx:195`'s `STATUS_LABEL`
gains its entry (else the row renders `undefined`), as does
`ElccPortfolioBlock['status']`; `tests/test_adequacy_mc_endpoint.py:551`'s
closed set widens.

## 2. Part B — `/copt` costs less

**Where the time goes**, measured at 300 units / 8760 h:

| term | 0 profiled | 8 profiled |
|---|---|---|
| `deconvolve` attempts (all fail at this size) | 6.8 s | 7.0 s |
| rebuild + shift | **4.2 s** | **3.8 s** |
| mixture inner loop | 0.1 s | **27.6 s** |
| total `attribute_criticality` | 12.2 s | 37.8 s |

**Change 1 — delete the `deconvolve` call** from `attribute_criticality`
(`copt.py:740-745`). No predicate: the rebuild is faster even when the
deconvolution succeeds (§0). `deconvolve` stays public for its round-trip test
(`test_adequacy_copt.py:275-282`, its only other caller).

**What this costs, owned.** On fleets where the deconvolution *did* succeed,
those rows now come from the rebuild, and the two agree only to float
tolerance. Measured drift in ΔEUE: **2.2e-14** worst on my fixtures (45–200
units, q 0.05–0.45, all succeeding), **9.4e-10** worst on the review's
(200 units, q = 0.45). F2's tolerance is **1e-8** — above the worse of the two
observations, still four orders below any physical claim. "Bit-identical" is
NOT claimed.

**Change 2 — bin the mixture inner loop.** `ES_d(x) = x·F_d[j] − Δ·G_d[j]`
with `j = clip(ceil(x/Δ − ε), 0, n_d)`, and the `x` cells depend only on the
residual and the mixed units, not on the distribution. Bin once —
`A[j] = Σ P[s]·w_h·x`, `B[j] = Σ P[s]·w_h` — and each counterfactual is
`EUE_d = Σ_j (A[j]·F_d[j] − Δ·B[j]·G_d[j])`, cells beyond a shorter table
folded into its last index exactly as `clip` does. Verified exact on nine
adversarial fixtures (off-grid residual, Δ = 2.5 and 7.0, the fold, an empty
table, negative-residual hours, zero weights, mixed+netted): worst rel
**3.8e-13**. The `k+1` histograms accumulate in ONE pass over the `2^k`
states.

**The switch is the measured crossover, not "`mixed` is non-empty".** Binned
work is `O(min(KMAX, n_d))` per counterfactual against direct's `O(2^k·H)`,
but `mixture_hourly` makes several full passes over `H` per state while the
binned form is two multiply-accumulates, so the constants differ by ~40×.
Measured (counterfactual evaluation only, 300 units):

| k | H | direct | binned | faster |
|---|---|---|---|---|
| 8 | 8760 | 28.60 s | 0.283 s | binned, 101× |
| 1 | 8760 | 0.235 s | 0.077 s | binned, 3.1× |
| 1 | 336 | 0.051 s | 0.077 s | **direct, 1.5×** |
| 1 | 168 | 0.049 s | 0.082 s | **direct, 1.7×** |
| 1 | 24 | 0.033 s | 0.055 s | **direct, 1.7×** |

So: **binned when `2^k·H > KMAX/40`**, the ratio measured on this machine and
re-measured by F2c. The losing regime v2 would have shipped is one profiled
unit on a short horizon — which is exactly 12d's per-period-block regime
(S27's fixture is two 24 h periods), where it would have paid 1.7× per block.

**The target, one number for both regimes.** After both changes the cost is
the rebuild, which this phase does not touch: **~5 s at 300 units, profiled or
not** (measured end to end in a scratch implementation: 5.25 s at 8 profiled,
5.20 s at 0 profiled, from 36.2 s and 12.1 s). At 100 units: 8.13 s → 0.35 s
profiled, 1.07 s → 0.30 s unprofiled.

**The floor, and why it is not fixed here.** What remains is the O(n²·L)
rebuild. A divide-and-conquer leave-one-out is the known answer; the v1 review
measured 0.408 s for all 300 tables, my own prototype reached only 2.31 s
against ~3.8 s (1.7×). **Recorded as the next cost item with both
measurements**, not promised. `/copt` therefore stays synchronous: 100 units
is under a second, and 300 units becomes ~5 s rather than 12–38 s.

**Regimes.** Netted units keep the direct path (their counterfactual shifts
the *residual*, a different cell set per unit, and the ceiling in `j(x)`
destroys any low-rank structure — Q1 of v1, answered: no shared histogram
exists), so a fleet where most units are profiled sees little of this. Per
12d the histogram is rebuilt per block; the arithmetic is unchanged (blocks
partition hours, ΔEUE merges by name) and the targets above are single-block.

**Determinism.** Sorting on `(-delta_eue_mwh, name)` goes on **all three**
sites — `attribute_criticality` (`copt.py:781`), the 12d block merge
(`copt.py:876`) and `/fmea_modes` (`results.py:2961`) — or the claim is not
made. It fixes exact ties only; two distinct units whose ΔEUE differ by less
than the binning error can still flip, and the plan says so rather than
implying otherwise.

**Also paying this cost:** `/fmea_modes` calls `get_copt()` directly
(`results.py:2949-2952`) under a different react-query key from the four
panels that share one (`McPanel.tsx:386`, `LoopPanel.tsx:318`,
`AdequacyTab.tsx:58`, `MarginLoopPanel.tsx:127`), so opening the FMEA tab pays
it again. Noted, not changed. `/copt` releases the mutation lock before the
expensive part (`results.py:5075-5091`), which is why this is an operability
defect and not a mesh defect.

## 3. What does NOT change (stated)

- The `/copt` contract: a synchronous GET, same payload.
- `mixture_hourly`, `build_copt`, `_shift_deterministic`, `deconvolve` itself,
  the per-block screening of 12d, `lolp_max`, the netted rows' semantics.
- The mutual-exclusion mesh and its 409s (only the abort remedy changes).
- The two loops' code.
- SSE/log-queue plumbing: the frontier and sweep routes pass no `log_queue`,
  so the restores fall back to a private `SimpleQueue`.
- Abort during a restore: both restores call `run_simulation` with a **fresh**
  `threading.Event`, so the study's flag cannot truncate the restore.

## 4. Tests

`tests/test_adequacy_abort.py`
- **F1 ★** per engine: the flag set before the second boundary stops it there;
  the record reaches `aborted` and keeps what completed. Bite: ignore it.
- **F1b ★** the restore: an aborted sweep still restores and keeps its rows.
  Bite: **raise instead of break in `run_contingency_sweep`** — the frontier
  cannot fail this (a `break` inside its `try` still runs the `finally`), so
  the sweep is the engine the test must drive.
- **F1c ★** idempotence: 200 on a finished run, 404 with no record, 200 twice.
- **F1d ★** bounded time: the record reaches `aborted` within N boundaries and
  the join returns well inside a full run's duration. (Asserting only that the
  mesh reopens is vacuous — `record_is_running` reopens on thread death alone,
  so it passes with the flag ignored.)
- **F1e ★** the three GETs answer 200 with a serialisable body **while a run
  is live**. Bite: leave `stop_event` in the filter.
- **F1f ★** the CRN contract: every ELCC row of an aborted study ran at
  `n_samples == baseline["n_samples"]`. Bite: honour the flag inside
  `mc_adequacy` on the ELCC path.
- **F1g ★** a partial sweep reaches the worksheet and does not `KeyError`.
  Bite: the `("done",)` gate.
- **F1h ★** class C does not run after class B was aborted. Bite: check the
  flag only inside `run_contingency_sweep`.
- **F1i ★** the copy: the refusal offers an abort for every key.

`tests/test_adequacy_copt_cost.py`
- **F2 ★** exactness **by name**, rel **1e-8**, against the shipped
  implementation kept as `_attribute_criticality_direct`: RTS-79, RBTS, an
  **off-grid** residual (else the floor bite is vacuous — `ceil(x−ε) ==
  floor(x)` on integers), a fold fixture, an empty table, Δ ≠ 1, mixed+netted.
  Bites: bin with `floor`; drop the fold.
- **F2b ★** the fold alone: folded 8256.0 against 0.0 unfolded.
- **F2c ★** the claim, machine-independently: on the 300-unit fixture
  `attribute_criticality` makes **zero** `deconvolve` calls and **one**
  `mixture_hourly` call per netted unit (zero when none), counted by
  monkeypatch. Wall time printed beside it, never asserted. Bite: restore the
  per-unit mixture loop.
- **F2d ★** near-ties: two units whose ΔEUE differ by less than the binning
  error keep their relative order on both paths under `(-ΔEUE, name)` — and
  the docstring states that exact ties are what the key fixes, near-ties are
  not. Bite: sort on `-ΔEUE` alone at the block-merge site.

## 5. Docs and gates

MC spec **v1.8** (the abort contract and where the flag may not go; the
attribution's binned form, its switch and its regimes); QA plan **S28**; the
shipped record. Gates: adequacy suite; full tree diffed against
`base_fails_sorted.txt` **both ways**; frontend vitest + `tsc`; live S15–S28
from an explicit `cd`, the server confirmed by `Application startup complete`
with no bind error.

**S28 (live):** start a ten-asset MC study, abort mid-run, and assert the POST
returns 200, the record reaches `aborted`, the next frontier POST is accepted
rather than 409, the three GETs answered 200 throughout, and `/results/copt`
on the 300-unit fixture returns in under 10 s. Bitten live by ignoring the
flag.

## 6. Out of scope, stated

- The O(n²·L) rebuild (measured, recorded, not promised).
- Making `/copt` asynchronous (the cost is reduced, not relocated).
- Aborting mid-solve.
- The static-CF flag and the derate NaN rule; the `validate` TOCTOU.
- `/fmea_modes`' duplicate `/copt` cost and its separate query key.

## 7. Open questions for the review

1. **Is deleting the `deconvolve` call the whole answer**, or should
   `attribute_criticality` keep it behind an explicit opt-in for small fleets
   where a user might want the round-trip property tested in situ?
2. **The crossover constant (~40).** It is machine-measured. Should the switch
   instead be a fixed rule with no constant (e.g. "binned iff `k ≥ 2`"), which
   is worse on some fleets but has nothing to drift?
3. **`base_restored` on the sweep** — is a bool enough, or should it carry the
   restore's solver status, given that `True` only means "did not raise"?
4. **The aborted-portfolio status.** A new block status widens a closed
   frontend union for a case only an abort produces; is a boolean
   `truncated` field better?
