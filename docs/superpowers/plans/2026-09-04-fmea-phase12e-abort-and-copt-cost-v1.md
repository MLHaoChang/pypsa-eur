# Phase 12e — every study can be stopped, and `/copt` stops blocking (plan, v1)

The second hardening item. Two recorded defects, both operability rather than
arithmetic, and neither changes a single number the engines report.

* **Three of the five studies cannot be stopped.** `coupling_loop` and
  `margin_loop` carry a `stop_event` and an `/abort` route; `mc`, `frontier`
  and `fmea_sweep` carry neither, so a user who starts a ten-asset ELCC run
  (a baseline plus ~10 bisected MC evaluations per asset — minutes) or a
  frontier over eight targets (eight full solves) waits it out. The
  mutual-exclusion mesh makes that worse than an inconvenience: while one
  study runs, every other study AND the foreground solve are refused, so an
  unstoppable study freezes the whole surface. The refusal copy already tells
  the truth — `ABORTABLE_STUDIES = ("coupling_loop", "margin_loop")` and the
  blocked user is told "It cannot be aborted, so wait for it to finish"
  (`project_context.py:255-265, 338-350`) — with a comment saying the tuple is
  "pinned by a test against the routes that actually exist, so this cannot
  drift the day someone gives the MC an abort". This is that day.
* **`/copt` holds a request for half a minute on a real fleet.** Measured on
  this machine (`scratchpad/proto12e`), a synchronous GET the Adequacy tab and
  the loop panel each fire on mount:

  | fleet | horizon | profiled | `/copt` wall time |
  |---|---|---|---|
  | 100 units | 8760 h | 0 | 0.91 s |
  | 300 units | 8760 h | 0 | 8.68 s |
  | 300 units | 168 h | 8 | 13.23 s |
  | **300 units** | **8760 h** | **8** | **31.18 s** |

  The cost is in `attribute_criticality`, and it is a function of the UNIT
  COUNT, not the horizon: one leave-one-out counterfactual per unit, each
  re-evaluating the per-hour mixture over `2^k` states across the whole
  horizon.

## 1. Part A — the three studies get a stop event

**The rule.** Every study record carries a `stop_event`; every study engine
takes `stop_event=None` and checks it at its own natural boundary, which is
the point where its state is consistent and its partial result is honest:

| study | boundary | worst-case latency after the click |
|---|---|---|
| `mc` | between adaptive batches; between ELCC assets; between portfolio periods | one batch (250 draws) or one bisection evaluation |
| `frontier` | between ε points | one full solve |
| `fmea_sweep` | between contingencies | one full solve |

A study that stops carries `status="aborted"` and **keeps what it measured**:
the frontier's completed points, the sweep's completed rows, the MC's headline
metrics when the abort lands after the baseline. Where nothing completed, the
result is `None` and the status says so — never a partial number presented as
a whole one.

**The restore is not optional.** `frontier` and `fmea_sweep` MUTATE the network
(every point re-solves it under a different cap) and already restore in a
`finally` (`frontier.py:163-172`). An abort takes the same path: the closing
re-solve runs, `base_restored` reports what actually happened, and an abort
that cannot restore says so rather than leaving the user's plan silently
rewritten. **This is the half that makes the feature safe**, and it is why the
abort is a cooperative flag checked between solves rather than a thread kill.

**Routes.** `POST /results/{mc,frontier,fmea_sweep}/abort`, each a copy of the
shipped loop route's contract (`results.py:3616-3644`): 200 and idempotent
even when the run is already finishing (a "stop" on something stopped is
satisfied; a 409 there makes the button flicker into an error at the moment it
worked), 404 only when no run was ever recorded. Deliberately NOT folded into
`/simulation/abort`, whose stop event belongs to the foreground solver thread.

**The copy collapses.** `ABORTABLE_STUDIES` becomes all five keys, so
`project_context.blocking_action_detail`'s two-branch remedy becomes one
sentence, and the test that pins the tuple against the routes that exist keeps
doing exactly that — it is the reason this cannot drift.

**Frontend.** An Abort button on the MC, frontier and sweep panels, mirroring
the loop panels' (same disabled/aborting states, same "aborting…" label while
the request is in flight).

## 2. Part B — the attribution's inner loop, reformulated

**Where the 31 s goes.** `attribute_criticality` computes, per unit,
`ΔEUE_i = EUE(fleet) − EUE(fleet with i perfectly available)`, and each EUE is
`mixture_hourly` over the profiled units' `2^k` states across `H` hours. With
300 units and 8 profiled that is `300 × 256 × 8760 ≈ 673 M` table lookups.

**The reformulation.** `expected_shortfall_vec` is a pure grid lookup
(`copt.py:192-198`):

    ES_d(x) = x · F_d[j(x)] − Δ · G_d[j(x)],   j(x) = clip(ceil(x/Δ − ε), 0, n_d)

so the weighted EUE is a sum over cells `(s, h)` whose `x` values **do not
depend on the distribution** — only `F`, `G` and the table length do. Bin the
cells once:

    A[j] = Σ_{cells with index j, x>0} P[s] · w_h · x
    B[j] = Σ_{cells with index j, x>0} P[s] · w_h

and every counterfactual is then a dot product over the grid:

    EUE_d = Σ_j ( A[j] · F_d[j] − Δ · B[j] · G_d[j] )

with the cells beyond a shorter table folded into its last index, exactly as
`clip` does. **Algebraically identical**, not an approximation.

**Measured** (`scratchpad/proto12e`, 40 table units + 4 profiled, 8760 h):
the 40 counterfactuals cost **0.15 s direct against 0.0013 s** binned — a
**115×** inner-loop speedup — and agree to a **max relative 1.9e-14**, which is
float summation order, not a different answer.

**Which rows it covers, and which keep the direct path.**

* **Table units** (the bulk — 292 of 300 in the measured case): one shared
  histogram, one dot product each.
* **Mixed units** (≤ `K_EXACT` = 8): their counterfactual fixes `s_i = 1`,
  which changes the cells, so each needs its own histogram — `k` builds, not
  `n` — still ~9 mixture passes instead of 300.
* **Netted units** (only when more than `K_EXACT` units carry a profile):
  their counterfactual changes the RESIDUAL itself (`r − q_j·a_j`), a
  different cell set per unit, so they keep the direct path. Their row already
  carries `NETTED_ROW_NOTE`; nothing about them changes.

**Target.** 300 units / 8 profiled / 8760 h: **31.18 s → under 2 s**, measured
end to end through the route, with the payload unchanged field for field.

**What must not move.** Every criticality number, to within float round-off:
pinned by an exact comparison against the shipped implementation on the
benchmark fixtures (§4 F2) rather than by re-deriving the values. The RTS-79 /
RBTS anchors, `mixture_hourly` itself (untouched), the per-block screening of
12d, `lolp_max`, and the netted rows' semantics.

## 3. What does NOT change (stated)

- The `/copt` contract: still a synchronous GET, same payload, no frontend
  change. Making it asynchronous would move the cost onto a poll rather than
  remove it, and would put a spinner in front of a number that can be
  instant.
- The mutual-exclusion mesh, the 409 messages (except the abort remedy), and
  the study records' shape on the wire.
- `mixture_hourly`, `build_copt`, `deconvolve`, `_shift_deterministic`.
- The two loops: they already abort, and their code is untouched.

## 4. Tests (`tests/test_adequacy_abort.py`, `tests/test_adequacy_copt_cost.py`)

★ = must fail against the named broken variant; restores by hash.

- **F1 ★ (abort, per engine).** A stop event set before the second boundary
  stops each engine there: the frontier returns the points it completed and
  `status="aborted"`; the sweep likewise; `mc_adequacy` returns its completed
  batches. Bite: ignore the event in the loop.
- **F1b ★ (the restore).** An aborted frontier still restores the base plan —
  `base_restored` true and the network's capacities equal to the pre-study
  solve's. Bite: abort by returning early, before the `finally`.
- **F1c ★ (idempotence and 404).** Abort on a finished run is 200; abort with
  no record is 404; a second abort is 200. Bite: 409 on a finished run.
- **F1d ★ (the mesh reopens).** After an abort completes, the next study POST
  is accepted rather than 409 — the failure this feature exists to prevent.
  Bite: leave the record `running`.
- **F1e ★ (the copy).** `ABORTABLE_STUDIES` equals the set of keys with an
  `/abort` route, and the blocked-action remedy no longer says "cannot be
  aborted" for any study. Bite: add a key without its route (the shipped test
  already pins this direction; the new assertion is the other one).
- **F2 ★ (Part B, exactness).** On the benchmark fixtures and on a
  profiled-fleet fixture, every criticality row from the reformulated
  attribution equals the SHIPPED implementation's (kept as
  `_attribute_criticality_direct` for the test to call) to rel 1e-12,
  row for row including `severity_eur` and the netted rows. Bite: bin `x`
  with `floor` instead of the grid rule; bite: drop the beyond-table fold.
- **F2b ★ (the fold).** A fixture whose residual exceeds the shortest
  counterfactual table (so cells land beyond it) — the folded index is the
  only thing that makes those cells count. Bite: drop the fold.
- **F2c (cost).** The 300-unit / 8-profiled / annual fixture completes under
  2 s (a soft gate that PRINTS the measurement, like the RBTS width gate,
  rather than failing on a slow machine).
- **F3 ★ (frontend).** Abort buttons on the three panels: disabled when no
  run is live, "aborting…" while in flight, and the refusal rendered from the
  backend detail. Bite: the button ignores the response status.

## 5. Live (QA plan S28)

Start a ten-asset MC study over the API, abort it mid-run, and assert: the
POST returns 200, the record reaches `status="aborted"` within one batch, the
subsequent frontier POST is accepted (not 409), and `/results/copt` on the
300-unit fixture returns under 2 s. Bitten live by ignoring the stop event.

## 6. Out of scope, stated

- The static-CF per-asset flag and the margin derate's NaN rule (backlog).
- The `validate` route's TOCTOU (backlog).
- Making `/copt` asynchronous (§3: the cost is removed, not relocated).
- Aborting a study MID-SOLVE. The boundary is between solves, so an abort
  during an eight-minute HiGHS call waits for that call. Killing a solver
  mid-flight is the foreground solver's own problem and its own mechanism.

## 7. Open questions for the review

1. **Is the binned attribution exact for the netted-unit path too?** The plan
   keeps netted units on the direct path; is there a cheap shared histogram
   for them (their residual shift is rank-1) that I have missed?
2. **The `mc` abort's granularity.** Between batches is one 250-draw batch;
   inside an ELCC bisection, one evaluation. Is checking between bisection
   probes worth the extra state, or is per-asset enough?
3. **Partial results.** Is an aborted MC's headline metric (baseline complete,
   ELCC rows partial) honest to serve, or should an aborted run serve no
   numbers at all?
4. **`base_restored` on an aborted sweep** — the frontier restores by
   re-solving the base case, which itself costs a solve. Should an abort skip
   the restore solve and instead report the network as dirty, naming what to
   do? (I think no; the plan says restore.)
5. **The soft cost gate (F2c).** Does a printed-not-failed timing assertion
   earn its place, given the RBTS precedent?


## v1 REVIEW (2026-09-04, adversarial subagent) — REJECT, return for a v2

Two blockers, both verified against the code before being recorded, and both
fatal to the plan as written.

1. **BLOCKER — the cost model named the wrong term, and the plan's own timing
   table refuted its target.** v1 attributed `/copt`'s 31 s to
   `300 × 2^k × H` mixture evaluations. Measured split at 300 units / 8760 h /
   8 profiled: **13.6 s counterfactual tables + 24.9 s mixture** — and with
   ZERO profiled units, where v1's mechanism contributes nothing, the whole
   8.1 s is still the table half. Root cause: `deconvolve` fails for **every**
   unit above ~30–40 (measured 300/300 at n = 300, 4.28 s of attempts that
   cannot succeed) and falls through to an O(n²·L) rebuild. v1's
   "31.18 s → under 2 s" was unreachable while §3 froze `deconvolve` and
   `build_copt`.
2. **BLOCKER — a stop event inside `mc_adequacy` would corrupt every ELCC
   credit.** `mc_adequacy` is not the MC study's function but the shared
   evaluator: `elcc.metrics_at` calls it with `_NEVER_CONVERGE` / `max_draws
   = n_fixed` so candidates replay the baseline's batch sequence bit for bit,
   and `elcc_of_portfolio` and both loops do the same. A flag in its batching
   loop truncates candidates against a full-budget baseline, breaks CRN and
   returns a wrong `elcc_mw` with `status="ok"` — and `baseline_key` hashes
   the arguments, never the result, so the guard that exists for exactly this
   cannot see it.

Nine further findings, each reproduced: the three GETs would 500 on every poll
(they filter only `thread`, not `stop_event`); the sweep's closing re-solve is
OUTSIDE its `finally`, so an abort raised as an exception skips the restore;
`base_restored` is computed by the frontier engine and discarded by the route,
whose `except` also throws away `exc.frontier_result`; `/fmea_modes` gates on
`status == "done"` so an aborted sweep's rows never reach the worksheet;
`run_class_b_sweep` raises `KeyError` on a partial result dict; the exactness
tolerance `1e-12` fails at 100+ units (RTS-79 measures 7.6e-13, 300 units
3.0e-11) and exactly-tied rows reorder, so "unchanged field for field" was
false; the binned path is SLOWER than the direct one when no unit carries a
profile; the latency table omitted the closing restore solve; and F1e's
premise was wrong — the route-equality test already asserts set equality in
both directions and needs no change, while `blocking_study_detail` has offered
an abort for all five studies since Phase 7, a pre-existing copy defect v1
described as already truthful. F2c (a print-only timing gate) could never
fail, and F2's floor bite is vacuous on a grid-aligned residual.

Superseded by **v2** (`2026-09-04-fmea-phase12e-abort-and-copt-cost-v2.md`),
which answers all 23 amendments by number. One correction to the review, made
by re-measuring rather than accepting it: the divide-and-conquer leave-one-out
it proposes for the rebuild measured **0.408 s** in its hands but only
**2.31 s against ~3.8 s (1.7×)** in mine, so v2 records the rebuild as the
next cost item with both measurements instead of promising a number it cannot
hit.
