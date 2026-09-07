# Phase 12i — RECOMMENDATION: do not build. The defect is real; every available remedy is a worse trade (v3)

**Status:** **DO NOT BUILD.** This supersedes v1 (rejected, 3 blockers) and v2
(rejected, 2 blockers + 4 serious). It is not a third attempt at the design —
it is the argument that the phase should be closed unbuilt, with the
measurements that decide it, and the condition under which it should be
reopened.

**Why this document exists at all.** Phase 12h recorded 12i as a real deferred
defect and sketched a design for it. Two plan versions and two adversarial
reviews later, the defect is confirmed real and **no remedy has been found
that is not worse than the disease in some measurable respect.** Recording
that is worth more than a v4 that dresses up a marginal change, and much more
than silently dropping the item.

---

## 1. The defect is real — this is not in question

The COPT mixes up to `K_EXACT = 8` profiled units exactly and nets the rest at
expected output. For a **constant** availability series that netting is wrong,
and not by a little. On-grid cap-saturating fleets, against a `k_exact`-raised
exact reference:

| fleet | shipped | exact | error |
|---|---|---|---|
| 9 × 100 MW, cf 0.8, load 600 | 9.6171 h | 11.9635 h | −19.6 % |
| 9 × 100 MW, cf 0.8, load 700 | 56.5454 h | 62.1181 h | −9.0 % |
| 12 × 100 MW, cf 0.8, load 800 | 9.6171 h | 3.2875 h | **+192.5 %** |
| 12 × 100 MW, cf 0.8, load 900 | 56.5454 h | 77.2195 h | −26.8 % |

And the error is **sign-varying**, which is itself a finding: 12c-pre measured
that netting a *varying* profile understates LOLE by convexity, and that does
**not** carry here. Netting a constant subtracts a constant from the residual
while the mixture's states are discrete steps of `cap × cf`, so whether the
netted residual crosses a step decides the sign. Exhibited: two fleets sharing
no parameter read an identical 9.6171 h because both leave eight 80 MW units
needing 7 of 8 up.

---

## 2. Remedy A — fold the netted constant units into the table. Rejected.

*(v2's design. Fold only what would otherwise be netted, never what would be
mixed — v1's unconditional fold was rejected for regressing the ordinary
non-saturating fleet by −48 %.)*

**In aggregate it is much better.** Mean absolute error over a swept load
range falls 18× and 105× on two fleets. **Pointwise it can be worse**, because
`_unit_states` apportions a non-grid capacity across two grid states and the
upper one is capacity the fleet cannot actually reach — the fold *manufactures*
capacity by rounding up past a threshold, which netting (subtracting in float)
cannot do:

| 9 × 100 MW, cf 0.833 | exact | shipped | err | folded | err | |
|---|---|---|---|---|---|---|
| load 660 | 11.9635 | 9.6171 | 2.3464 | 11.9635 | 0.0000 | fold wins |
| **load 667** | 62.1181 | 56.5454 | 5.5727 | 48.7435 | **13.3746** | **fold 2.4× worse** |
| load 680 | 62.1181 | 56.5454 | 5.5727 | 62.1181 | 0.0000 | fold wins |

The review found worse: 3.8× on a 20-unit fleet, 8.7× on a 40-unit fleet, and
fold-worse points in 8 of 11 randomised heterogeneous fleets.

**And on realistic mixed fleets it is frequently a no-op.** `split_fleet`
orders by `−mean(a)·cap`, so constant units at a high `cf` sort *into* the
exact mixture and the *varying* units get netted. Six varying units at mean
0.25 plus five constant at 0.833 → **zero folds**. The phase's value is
confined to fleets whose constant units are the low-mean ones.

**A process finding, recorded because it matters more than the design.** v2's
evidence table said it swept "every integer load in `[0.45, 0.80] × total
capacity`". The script actually swept `[0.45, 0.80] × Σ(cap × cf)` — the
*derated* total, 337…599 MW, not the nameplate 405…720 MW. Load 667 lies
inside the window the plan claimed and outside the one it measured. The table
was not reproducible from its own stated method, and the one window that
produced it is the window in which the counterexample is unreachable. That was
not deliberate, and it is exactly the failure this program keeps catching: a
favourable measurement from a fixture that cannot see the failure. It is the
third time in this phase.

---

## 3. Remedy B — fold, and refine the table grid so the fold is exact. Rejected on cost.

The pointwise regression in Remedy A is entirely the 1 MW grid. Make the grid
fine enough that `cap × cf` lands on it and the fold becomes exact:

| 9 × 100 MW, cf 0.833 | delta 1.0 | delta 0.5 | delta 0.1 |
|---|---|---|---|
| load 667, err | 13.3746 | **0.0000** | **0.0000** |
| load 583, err | 5.7487 | 3.2850 | **0.0000** |
| load 584, err | 0.0000 | 0.0000 | **0.0000** |

Exact at every point measured. But the table is `O(N·C/Δ)`, and on real fleets
that is not free:

| fleet | system MW | delta 1.0 | delta 0.5 | delta 0.1 |
|---|---|---|---|---|
| 50 units × 200 MW | 10 000 | 54.7 ms | 87.8 ms | 423.7 ms |
| 100 units × 300 MW | 30 000 | 390.0 ms | 714.6 ms | 5 894 ms |
| 200 units × 500 MW | 100 000 | 5 423 ms | 14 644 ms | **76 837 ms** |

**A 14× slowdown on the systems where adequacy analysis matters most**, to fix
a term that only bites a fleet whose constant units are also its low-mean ones.
`/copt` is an on-demand route with no solve behind it; 77 seconds is not a
route, it is a study. Rejected.

*(A per-unit or adaptive grid — refine only when a fold happens, and only as
far as the folded capacities require — is not measured here. It is the one
direction left, and §5 says what it would have to show.)*

---

## 4. What is NOT wrong, and bounds the value of any remedy

- **The sequential MC is already exact** for a constant series: it samples
  outages on the series and never calls `split_fleet`.
- **The reserve margin is already right**: it credits the unit at
  `(1 − q) × avail`, and the apportionment in Remedy A is mean-preserving
  (measured `2.3e-13`), so no engines-vs-margin divergence is introduced or
  removed by any of this. This phase would not close a disagreement between
  surfaces — unlike 12h, which existed to close one.
- **Phase 12h's `deterministic` bucket already handles the flagged half
  exactly**, in float, with no grid term. v1 proposed moving those units into
  the table and the review measured the cost: the true 8.4000 h read as
  5.8800 h, a 30 % understatement, plus a silently dropped FMECA note.

So the population left for 12i is: **unflagged, constant-series, in a fleet
that saturates the exact-mixture cap, and whose constant units sort below the
varying ones.** That is a narrow target, and it is only reachable by the COPT
— one of four surfaces.

---

## 5. The condition for reopening

Build this phase when **either** holds:

1. **A cheaper exact table exists.** If the convolution's cost stops scaling
   with `1/Δ` — a different representation, or a per-unit grid that refines
   only the folded units' states — then Remedy B becomes free and the fold is
   exact rather than merely better-on-average. Measure the 200-unit fleet
   above; anything under ~2× the delta-1.0 time makes this worth building.
2. **A real network shows the defect.** Every fixture in §1 and §2 is
   synthetic and homogeneous. If a PyPSA-Eur import or a user project is
   measured to carry a cap-saturating constant-series fleet whose constant
   units sort below its varying ones, the population stops being hypothetical
   and the aggregate improvement of Remedy A may be worth its pointwise risk —
   with the risk disclosed on the payload.

Until then the shipped behaviour, which is a documented approximation with a
`fidelity_note` that says a unit is "netted at expected output" and that "their
criticality rows understate their outages", is **honest about being
approximate**. Remedy A would replace it with an approximation that is better
on average, worse at specific loads, and silent about which — and `fidelity_note`
would read *more* confident, not less, because the netted sentence disappears.
That is the trade this document declines.

---

## 6. What should be recorded elsewhere

- `docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md`: the
  netting approximation's error for a **constant** series is sign-varying and
  can reach +192 %, which the current text does not say (it carries 12c-pre's
  "netting understates" finding, true for varying profiles only).
- The PR's "Known limitations" already names the netting approximation and the
  measured 99 s cost of the exact alternative. It should gain the constant-series
  case and point at this document.

## 7. The review record

| plan | verdict | findings | what it was rejected on |
|---|---|---|---|
| v1 | **REJECT** | 9 (3 blockers) | the fold is not exact (on-grid fixture hid it); folding a would-be-*mixed* unit regresses the ordinary fleet −48 %; the `deterministic` reversal was an accuracy regression sold as a relabelling (8.40 → 5.88 h) that also dropped a FMECA note; `folded_units` built from a list the fold never touches, deleting a disclosure; merge double-report; `split.table` capacity ambiguous; a dead line; preflight unfounded |
| v2 | **REJECT** | 10 (2 blockers, 4 serious) | "never worse" is false (load 667: 5.57 → 13.37 h) and the evidence window was not the one the plan stated; H5 asks `split.table` to be both the math input and the disclosure, which `build_copt` refuses; the merge rebuild yields `folded_constant = None` → a 500 on `/copt`; H4 fixes an unreachable case; the new preflight code is not computable where preflight runs and leaves a block-constant population named by nothing; ★I6 tautological |
| v3 | **DO NOT BUILD** | — | the defect is real; Remedy A is pointwise worse and often a no-op; Remedy B is exact but 14× slower on large fleets; no surface disagreement is closed by either |
