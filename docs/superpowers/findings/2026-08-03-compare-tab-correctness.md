# Compare tab correctness — suspects S1, S2, S3 (Tasks 14–16)

**Date:** 2026-08-04
**Method:** `pypsa-gui/backend/tests/golden/fixture.py`'s golden network
(multi-period, 2030 [5 years] + 2035 [10 years], 15 modelled years total,
discount rate 7%) plus, for S2, a small purpose-built two-bus network
(`compare_local_networks.build_weighting_basis_network`) whose two
snapshot-weighting columns are set to genuinely different values — the
golden fixture's `objective` and `generators` columns are identical (both
flat 1.0) and cannot distinguish the two bases at all.

Tasks 1–13 (prior work) found no wrong numbers: additivity holds everywhere
and capacity CAPEX, dispatch energy, prices, emissions, economics CAPEX,
curtailment and storage cycling all agree exactly with their live
counterparts. This document closes out the three suspects a code read
flagged but measurement had not yet settled: S1 (Task 14), S2 (Task 15), S3
(Task 16).

Every claim below is marked **MEASURED** (read off a running test/script) or
**INFERRED** (derived from a code read / mathematical argument, not executed
against live numbers).

---

## S1 — Capacity tab omits link CAPEX that Economics counts (Task 14)

**Status: CONFIRMED, escalated — awaiting a product decision. No fix applied.**

`_compute_total_annuitised_capex` (`routers/compare.py`) walks only
Generator, StorageUnit and Store. `_compute_economics_summary` walks those
three **plus Link**. On the golden fixture the `electrolyzer` Link is
extendable and the LP builds ~28.57 MW of it — so the Capacity tab's total
CAPEX is short by exactly that asset's CAPEX.

**MEASURED** (`test_capacity_and_economics_agree_on_total_capex`,
`tests/test_compare_cross_surface.py`, run against the solved golden
network):

| Quantity | Value (M€) |
|---|---|
| Capacity tab total (`capex_meur_by_carrier` summed) | 25.15453529546885 |
| Economics tab total (`by_carrier[*].capex_meur` summed) | 25.32078506683662 |
| Difference | 0.1662497713677702 |
| `electrolyzer` Link CAPEX (Task 10/S3 measurement) | 0.16624977136776928 |

The difference equals the electrolyzer's CAPEX to every printed digit —
confirming the hypothesis exactly, not merely approximately.

**INFERRED (code read):** the omission is deliberate — `_walk`'s trailing
comment in `_compute_total_annuitised_capex` explains that lines/links are
excluded because `n.statistics()` reports passive branches as zero CAPEX
even when `capital_cost` is derived from `overnight_cost`. That reasoning is
correct for a **fixed** line (no LP objective contribution) but does not
hold for an **extendable** link, which genuinely enters the LP objective and
whose optimized capacity DOES carry real annuitised CAPEX.

Two defensible resolutions exist, and picking between them is a product
decision, not something to guess at in this task:

1. Include extendable links (`p_nom_extendable == True`) in
   `_compute_total_annuitised_capex`, leaving fixed/passive branches out.
2. Keep the current omission, but surface it explicitly in the Capacity
   tab's UI copy (e.g. a footnote: "excludes link investment — see
   Economics tab for full CAPEX").

**Test:** `tests/test_compare_cross_surface.py::test_capacity_and_economics_agree_on_total_capex`,
marked `@pytest.mark.xfail(strict=True, reason="S1: Capacity omits link CAPEX
that Economics counts — awaiting product decision, findings §S1")`. No
production code was changed for S1, per the task brief's explicit
instruction to measure and escalate, not decide.

---

## S2 — the hours/weighting basis in Loading and Prices (Task 15)

Both `_compute_loading_summary` and `_compute_prices_summary` call
`_build_snapshot_weights(n)` with no explicit `column` argument, which
defaults to `"objective"` (the COST basis) rather than `"generators"` (the
ENERGY/HOURS basis `n.statistics()`, the Results-tab KPIs, and this
codebase's own `test_dispatch_energy_uses_the_generators_weighting_basis`
all use). The two columns are identical on the golden fixture (both flat
1.0), so no existing test — including the two Task-7 "internal identity"
tests on `binding_hours` — could distinguish which basis was actually in
use. This task builds a fixture where the two columns genuinely differ and
settles the question separately for each of the three affected quantities,
per the brief's explicit warning not to assume one answer covers all three.

### Test fixture

`compare_local_networks.build_weighting_basis_network()` — two buses
(`b1`, `b2`) linked by a single, non-extendable, thermally-limited Line
`L1` (`s_nom=25`). `b1` carries a cheap Generator (`marginal_cost=10`)
whose only route to the load is across `L1`; `b2` carries a load that
varies snapshot-to-snapshot (`[20, 40, 30, 40]` MW) and an expensive
backup Generator (`marginal_cost=200`) that only dispatches once `L1`'s
25 MW cap is exhausted.

**MEASURED** (solved directly, before any snapshot-weighting override):

| snapshot | load (MW) | `L1` flow (MW) | `L1` loading | `b1` price | `b2` price |
|---|---|---|---|---|---|
| 0 | 20 | 20.0 | 0.80 | 10 | 10 |
| 1 | 40 | 25.0 | **1.00 (binding)** | 10 | 200 |
| 2 | 30 | 25.0 | **1.00 (binding)** | 10 | 200 |
| 3 | 40 | 25.0 | **1.00 (binding)** | 10 | 200 |

`L1` is congested (loading ≥ 0.99, PyPSA's own binding threshold) at 3 of 4
snapshots and genuinely uncongested at the 4th — giving both a real
partial-binding pattern for the loading test and real locational price
separation for the prices test, from an actual LP solve, not a hand-set
value.

`snapshot_weightings` are then overwritten **uniformly**
(`objective = 1.0`, `generators = 3.0` for every snapshot — the exact
`_rep_week_network` recipe the task brief specifies), on a flat
(single-period) network so no `investment_period_weightings.years`
multiplier is in play. **MEASURED:** `generators`-basis horizon hours =
12.0, `objective`-basis horizon hours = 4.0 — genuinely different, so the
fixture can actually decide the question (unlike the golden fixture).

### binding_hours — CONFIRMED wrong basis, FIXED

`binding_hours` is a raw, un-normalized **sum** of weight over the
snapshots where a branch is loaded ≥ 99%: an unambiguous hours/energy
quantity (see also PyPSA's own `optimization/constraints.py`, which calls
`n.snapshot_weightings.generators` "elapsed hours" in a code comment, and
`statistics/expressions.py::transmission()`, which weights branch-flow → MWh
conversion by the `generators` column, never `objective`).

**MEASURED**, before the fix: `_compute_loading_summary` reported
`L1.binding_hours.total == 3.0` (3 binding snapshots × the `objective`
weight of 1.0 — the COST basis) instead of the correct `9.0` (3 × the
`generators` weight of 3.0 — the ENERGY basis). A raw sum is NOT invariant
to which basis is used, unlike the two quantities below — this is a real,
observable defect, not merely a style preference.

**Fix applied** (`routers/compare.py::_compute_loading_summary`, one call
site): `weights = _build_snapshot_weights(n)` → `weights =
_build_snapshot_weights(n, "generators")`.

**MEASURED**, after the fix: `L1.binding_hours.total == 9.0` — correct.

### mean_loading — measured invariant, unaffected by the fix

`mean_loading` is a snapshot-weighted **mean**
(`Σ(loading·w) / Σw`): the same weight series appears in numerator and
denominator, so a *uniform* rescaling of the weighting column cancels
algebraically. **MEASURED:** on this fixture, `L1.mean_loading.total ==
0.95` under BOTH the `objective` basis (pre-fix) and the `generators` basis
(post-fix) — bit-identical. The one-line fix above therefore changes
`binding_hours` (correctly) while leaving `mean_loading` numerically
untouched, exactly as the brief predicted ("a uniform scaling cancels — it
may be unaffected"). No separate action needed for this quantity; it was
already correct in the sense that matters (it does not depend on which of
the two proportional bases is used), and moving it onto the `generators`
basis alongside `binding_hours` is additionally the more defensible choice
on principle (it is a physical dispatch/loading average, the same family as
`transmission()` above), not merely a side effect.

### Prices tab (duration curve + mean/median/p90) — MEASURED no observable defect, left unchanged (CLEARED)

`_compute_prices_summary` uses the same un-parameterised
`_build_snapshot_weights(n)` (objective/cost basis) for BOTH (a) positioning
points along the 101-point weighted duration curve, and (b) the
`mean_price`/`median_price`/`p90_price` statistics. The brief explicitly
warns these are a *different question* from `binding_hours`: only a raw
hours count is unambiguously energy-basis; a duration curve's implicit
"hours" position and a cost-weighted mean price are each their own question.

**Mathematical argument (INFERRED):** both the duration curve's sample
index (`searchsorted(cum_w, linspace(0, total_w, 101))`) and the
`_stats()` weighted mean/median/p90 are **ratios/positions built from a
single weight series appearing on both sides of a scale** — under a
*uniform* per-snapshot rescaling of that series (the only kind of
objective≠generators divergence this task's fixture, or a typical
representative-period run, produces), both are provably invariant. This is
the same cancellation argument as `mean_loading` above, extended to
`_compute_prices_summary`'s two consumers of the weight series.

**MEASURED** (recomputing the SAME weighted mean price with the weight
series swapped from the `objective` value to the `generators` value on the
`build_weighting_basis_network` fixture): `mean_price` is bit-identical
between the two bases, and the 101-point `duration_curve` array is
element-wise identical (`pytest.approx(..., rel=1e-9)`) between a network
solved with `objective=1.0` and one solved with `objective=3.0` (mimicking
what using the `generators` column would produce). No numeric divergence
exists to fix on any fixture achievable within this task's scope.

**Precedent (INFERRED):** PyPSA's own `statistics/expressions.py::revenue()`
weights price × dispatch by `n.snapshot_weightings.objective` (a COST-domain
choice), and `market_value()` divides that objective-weighted revenue by a
`generators`-weighted supply — i.e. PyPSA itself treats price/revenue
aggregation as legitimately living on the `objective` basis, distinct from
the `generators` basis it uses for the underlying MWh. This supports leaving
`_compute_prices_summary`'s mean/median/p90 (and, by the same argument, the
duration curve, whose value is likewise unaffected under any achievable
test) on the `objective` basis rather than blindly copying the loading
tab's fix.

**Decision: `_compute_prices_summary` is UNCHANGED.** This half of S2 is
CLEARED — measured to produce no wrong number today, and changing it would
be an unverifiable, unmeasured guess about a genuinely different question
(cost-weighted price vs. energy-weighted duration) than the one `binding_hours`
answered. Residual open question, not actionable here: a network where the
two weighting columns diverge **non-uniformly across snapshots** (not just
by a single global constant) — e.g. different representative periods
carrying different objective:generators ratios — could in principle expose
a real difference in the duration curve's shape or the price means; no such
fixture was built or is required by the current suite.

**Tests:** `tests/test_compare_invariants.py` —
`test_binding_hours_use_the_energy_basis_not_the_cost_basis` (binding_hours,
fails pre-fix / passes post-fix), `test_mean_loading_is_invariant_to_the_weighting_basis`
(mean_loading, passes either way), `test_price_statistics_are_invariant_to_the_weighting_basis_under_uniform_scaling`
(prices, passes either way — CLEARED). `compare_local_networks.py` gained
`build_weighting_basis_network` / `solve_weighting_basis_network`.

**Fix:** `routers/compare.py::_compute_loading_summary`, one call site,
`_build_snapshot_weights(n)` → `_build_snapshot_weights(n, "generators")`.
`_compute_prices_summary`'s call site is untouched.

---

## S3 — Economics tab LCOE time basis (Task 16)

**Status: CLEARED. No code change.**

Task 10 (`test_lcoe_is_total_cost_over_total_energy`,
`tests/test_compare_invariants.py`) already decides this suspect: it checks
that the Compare Economics tab's reported LCOE equals
`(capex_meur.total + opex_meur.total) × 1e6 / (dispatch_gwh.total × 1e3)`
for every carrier with positive dispatch, on the golden fixture (15
modelled years — GOLDEN_YEARS = (5, 10) — so a horizon-vs-annual CAPEX
confusion would show up as a ~15× ratio, not a subtle rounding gap).

**MEASURED** (re-run for this task; `../../.pixi/envs/test/bin/python -m
pytest tests/test_compare_invariants.py::test_lcoe_is_total_cost_over_total_energy` →
1 passed):

| carrier | capex (M€) | opex (M€) | dispatch (GWh) | reported LCOE (€/MWh) | expected LCOE (€/MWh) | ratio |
|---|---|---|---|---|---|---|
| gas | 0.376324 | 2.134286 | 42.685714 | 58.816149 | 58.816149 | 1.0000000000 |
| solar | 24.750000 | 0.000000 | 21.600000 | 1145.833333 | 1145.833333 | 1.0000000000 |
| diesel | 0.028212 | 0.360000 | 3.600000 | 107.836577 | 107.836577 | 1.0000000000 |
| h2 | 0.166250 | 0.102857 | 10.285714 | 26.163172 | 26.163172 | 1.0000000000 |

Ratio 1.0 exactly (not ~15×) for all four carriers with positive dispatch —
`_capex_commitment` (`routers/compare.py`) already scales CAPEX by the
full-horizon year sum before it reaches this quotient, so the sibling defect
fixed in `services/asset_results/compute.py` (commit `922eb4d0`) does not
apply here. `test_lcoe_is_total_cost_over_total_energy` already encodes this
permanently; no further test or code change needed for S3.

---

## Summary

| Suspect | Verdict | Evidence | Action |
|---|---|---|---|
| S1 (Task 14) | CONFIRMED, escalated | MEASURED: 25.154535 vs 25.320785 M€, Δ=0.166250 M€ == electrolyzer CAPEX | `xfail(strict=True)` test added; awaiting product decision; no fix applied |
| S2 — binding_hours (Task 15) | CONFIRMED, fixed | MEASURED: 3.0 h (wrong, cost basis) → 9.0 h (correct, energy basis) | One-line fix in `_compute_loading_summary` |
| S2 — mean_loading (Task 15) | Correct as-is | MEASURED: invariant (0.95 either basis) | No change; basis switched alongside binding_hours anyway (harmless, and more principled) |
| S2 — prices (Task 15) | CLEARED | MEASURED: mean_price and duration_curve invariant under the achievable basis swap; INFERRED PyPSA precedent supports the `objective` basis | No change |
| S3 (Task 16) | CLEARED | MEASURED: LCOE ratio 1.0 exactly, all 4 carriers | No change |
