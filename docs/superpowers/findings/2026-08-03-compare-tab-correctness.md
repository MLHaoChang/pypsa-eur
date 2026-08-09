# Compare tab correctness — full findings (Tasks 1–21)

**Extended 2026-08-04 (Tasks 19–21):** this document originally closed out
suspects S1/S2/S3 (Tasks 14–16) only. It is now the authoritative record for
the whole examination: a per-tab section covering every one of the ten
Compare tabs (golden-fixture agreement from Tasks 1–13, plus a one-off
real-project spot check from Task 21), the frontend curtailment defect found
in Tasks 17–18, and the endpoint-wiring (Task 19) / coverage-matrix
(Task 20) work that closes out the plan. Jump to "Per-tab record", "Frontend
defect", "Task 19", "Task 20", "Task 21" or "Known limitations" below the
original S1/S2/S3 writeup, which is unchanged from Task 16.

---

**Closed 2026-08-04 (S1/S2/S3, Tasks 14–16):** all three suspects resolved. S1 CONFIRMED and
escalated (product decision pending, no fix applied). S2 split in two:
`binding_hours` CONFIRMED and fixed (one-line basis change,
`routers/compare.py::_compute_loading_summary`); `mean_loading` and the
Prices tab's mean/median/p90/duration curve MEASURED invariant to the same
basis choice, CLEARED, left unchanged. S3 CLEARED (LCOE ratio 1.0 exactly,
re-verified after the S2 fix landed —
`test_lcoe_is_total_cost_over_total_energy` still passes). Full detail in
each section below.

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

### S1 RESOLVED — 2026-08-04, by the product owner

**Decision: include EXTENDABLE links in the Capacity tab.** Passive branches
(lines, transformers, and non-extendable links) stay excluded — the LP cannot
resize them, so they contribute nothing to the objective and their notional
CAPEX is not capacity expansion.

**Implementation (MEASURED):** `_compute_total_annuitised_capex` gained
`_walk(n.links, "p_nom", "links", extendable_only=True)`, and `_walk` gained
an `extendable_only` filter. `test_capacity_and_economics_agree_on_total_capex`
now passes with the `xfail` marker REMOVED, not widened — Capacity and
Economics agree at `rel=1e-6` on the golden fixture. Full backend suite after
the change: 2174 passed, 18 skipped, 0 failed, 0 xfailed.

`test_capacity_capex_agrees_with_periodized_capital_costs` was updated in the
same commit to mirror the new rule (its oracle now walks extendable links
too); leaving it on the old three-class walk would have made it fail against
correct behaviour.

**KNOWN RESIDUAL — deliberately left open.** `_compute_economics_summary`
walks EVERY link with a positive `capital_cost` (`_walk_capex_plain("Link",
n.links, "p_nom")`), not only extendable ones. So a NON-extendable link
carrying a `capital_cost` would still appear in Economics and not in Capacity,
and the two tabs would disagree again by that amount. No such asset exists on
the golden fixture or on the real project this was measured against, which is
why the test passes — **the test does not prove the general case.** Closing it
means deciding whether a sunk, unresizable asset belongs in a capacity-
EXPANSION view at all, which is a further product question and was not part of
the decision taken here.

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

---

## Per-tab record (Tasks 1–13, 19, 21)

One section per Compare tab. "Golden" = the multi-period golden fixture
(`tests/golden/fixture.py`, Tasks 1–13, permanent regression suite).
"Real project" = the one-off spot check against the user's actual
`3_nodes_system` project (Task 21, detailed numbers in its own section
below) — **evidence, not a regression guard**; every number under that
heading is **MEASURED** on 2026-08-04 by loading `network.nc` directly and
calling the exact `_compute_*_summary` functions
`routers/compare.py::get_results_summary` calls, and is not re-checked by
any committed test. Every claim below is tagged MEASURED (read off a
running test or script) or INFERRED (a code read / mathematical argument).
"No complaint" is recorded explicitly per tab so it is never mistaken for
"not checked."

### Overview

**Golden:** not a dedicated suspect; the tab is oracle-only (bus/generator/
line/link/storage/store counts, snapshot range, objective) and was read for
Task 20's coverage work — `get_compare_state`'s `installed_capacity_by_carrier`
/ `optimised_capacity_by_carrier` come from `_carrier_sum(temp_n.generators,
...)`, `storage_capacity_by_carrier` from `_carrier_sum(temp_n.storage_units,
...)` — Generator and StorageUnit only; `line_count`/`link_count` are bare
`len()` counts with no carrier attribution (see `coverage.py`'s
`compare_overview` entry, Task 20). **AGREEMENT (INFERRED from a code read,
Task 20):** the same `_carrier_sum` pattern the Capacity tab's
`capacity_mw_by_carrier` uses, so the two tabs cannot structurally disagree
on generator/storage capacity.

**Real project (MEASURED):** `n.generators.groupby("carrier")["p_nom_opt"].sum()`
gives `gas=627.275727 MW, solar=968.000000 MW` — identical to the Capacity
tab's `capacity_mw_by_carrier` totals reported below. Installed (`p_nom`)
capacity is `gas=300.0, solar=300.0 MW`; storage `p_nom` is
`battery=100.0 MW`. Overview and Capacity agree on this real project too.

### Capacity

**Golden (Tasks 1, 5, 14):** capacity CAPEX agreement 1.0 against
`/statistics`, `/api/simulation/asset_costs` (see `test_golden_economics.py`
and the Task-9 CAPEX-delegation fix). S1 (below) is this tab's one open
defect: it structurally omits Link CAPEX that the Economics tab counts.
`test_no_periods_installed_capacity_exceeds_the_total` and
`test_new_capacity_never_exceeds_installed_capacity`
(`tests/test_compare_invariants.py`) hold — **AGREEMENT (MEASURED)**.

**Real project (MEASURED):** `capex_meur_by_carrier` totals `gas=145.332507
M€` (by_period 48.4442 M€ flat across 2027/2028/2029 — brownfield/existing
capacity dominates, no material re-build across periods) and
`solar=137.056558 M€` (by_period 45.6855 M€ flat). Horizon total =
`282.389066 M€`. Additivity walk judged 8 EXTENSIVE/INTENSIVE values on this
tab, 0 failures. This is the smaller side of the S1 gap — see its own
section below.

### Dispatch

**Golden (Task 6):** dispatch energy basis agreement 1.0 (`generators`
weighting column, matches `n.statistics()` / Results-tab KPIs — a real
divergence would have shown up given the golden fixture's own weighting
setup); dispatch vs `/carrier_kpis` agreement 1.0.
`test_dispatch_energy_uses_the_generators_weighting_basis` recomputes GWh
from PyPSA primitives independently and matches to `rel=1e-6` —
**AGREEMENT (MEASURED)**.

**Real project (MEASURED):** `dispatch_gwh_by_carrier`: `gas=9839.1416 GWh`,
`solar=3520.9120 GWh`, `battery=231.2715 GWh` (discharge-only, per the
function's own documented convention). `opex_meur.total=590.348494 M€`,
`total_load_gwh.total=12864.4775 GWh`. Additivity walk judged 6 values, 0
failures.

### Line loading

**Golden (Tasks 7, 15):** `binding_hours` CONFIRMED wrong (S2, see above) and
fixed; `mean_loading` measured invariant to the fix, correct either way. On
the golden fixture `snapshot_weightings["objective"]` and `["generators"]`
are identical (both flat 1.0), so `test_binding_hours_never_exceed_the_horizon`
/ `test_mean_loading_never_exceeds_peak_loading` on `golden` cannot by
themselves distinguish the two bases — the dedicated
`compare_local_networks.build_weighting_basis_network` fixture (Task 15) is
what actually separated them (3.0 h wrong vs 9.0 h correct).

**Real project (MEASURED):** 5 branch entries (3 lines + the 2 links).
`binding_hours <= horizon hours` (26,280.0 h) holds for every entry;
`mean_loading <= peak_loading` holds for every entry. Most-loaded branch:
`Electrolyzer 1`, `peak_loading=1.0000`, `mean_loading=0.8552`,
`binding_hours=1260.00 h` (≈4.8% of the 26,280 h horizon spent at ≥99%
loaded). **Limitation, stated honestly:** `snapshot_weightings["objective"]`
and `["generators"]` are ALSO identical on this real project (both bases give
horizon hours = 26,280.0000 exactly) — so, like the golden fixture, this
real-project spot check does **not** independently re-verify the S2
`binding_hours` basis fix; that fix is verified only by the purpose-built
Task-15 fixture. Both large networks this suite has access to happen to run
unweighted (flat 1.0) snapshots.

### Prices

**Golden (Tasks 8, 15):** duration curve monotonicity, percentile bounds —
agreement 1.0 (`test_the_duration_curve_is_monotonically_non_increasing`,
`test_price_statistics_lie_inside_the_observed_range`). Prices basis (S2
half) MEASURED invariant to the objective/generators choice under uniform
rescaling, CLEARED — see S2 section above.

**Real project (MEASURED):** duration curve monotonically non-increasing;
`mean_price`/`median_price` lie inside `[min_price, max_price]`.
`min_price≈0.0000 €/MWh`, `mean_price=429.1822 €/MWh`,
`median_price=109.8667 €/MWh`, `p90_price=171.2381 €/MWh`,
`max_price=100000.0000 €/MWh` — the max is exactly the project's configured
VOLL (`solver_config.json: voll=100000.0`), consistent with the Lost-load
tab reporting real shedding (below): the price duration curve is correctly
picking up VOLL-priced snapshots, not clipping or dropping them. The mean
being far above the median is exactly what a small number of VOLL spikes
does to a weighted mean on a right-skewed price distribution — expected
shape, not a symptom.

### Emissions

**Golden (Task 9):** per-carrier emissions sum to total, agreement 1.0.
Intensity denominator PINNED as total GENERATOR DISPATCH (not load) —
measured 125.758 kg/MWh matches dividing by generator dispatch, NOT the
131.746 kg/MWh a load-basis division would give — see the definition note
at the top of this document (carried over unchanged from Task 9/16).

**Real project (MEASURED):** `by_carrier_kt` sums to `total_kt.total`
exactly (`parts=4088.7099 == total=4088.7099`). Intensity identity holds:
reported `intensity_kg_per_mwh.total=306.039935` equals
`total_kt.total × 1e6 / total_generator_dispatch_mwh` computed independently
from `n.generators_t.p` — same identity, same denominator, a different
number from golden's 125.758 (different network, different carrier mix and
CO2 price, as expected). Only `gas` carries emissions on this project
(`solar` has no `co2_emissions`; `battery` discharge is not a combustion
event) — consistent with `compare_emissions`'s Generator-only coverage
established in Task 20.

### Economics

**Golden (Tasks 10, 14, 16):** CAPEX agreement 1.0 vs `asset_economics`
across 4 carriers; LCOE identity 1.0 on all 4 carriers with positive
dispatch (S3, CLEARED). S1's OTHER half — Economics counting Link CAPEX
that Capacity omits — is this tab's side of the escalated defect, not a
defect IN this tab (Economics is the more complete of the two).

**Real project (MEASURED):** LCOE identity `((capex+opex)×1e6)/(dispatch_gwh×1e3)
== lcoe_eur_per_mwh` holds exactly for all 5 carriers with positive
dispatch:

| carrier | capex (M€) | opex (M€) | revenue (M€) | dispatch (GWh) | LCOE (€/MWh) |
|---|---|---|---|---|---|
| gas | 145.3325 | 590.3485 | 1292.6593 | 9839.1416 | 74.7709 |
| solar | 137.0566 | 0.0000 | 218.6347 | 3520.9120 | 38.9264 |
| battery | 0.0000 | 20.6974 | 671.6063 | 231.2715 | 89.4942 |
| h2 (Electrolyzer 1) | 54.2807 | 31.5937 | 0.0000 | 3159.3692 | 27.1809 |
| heat-pump-air (P2H 2) | 1.9117 | 0.0000 | 0.0000 | 190.7140 | 10.0240 |

`h2` and `heat-pump-air` are the two Link carriers — their `capex_meur`
here is exactly what S1 measures as MISSING from the Capacity tab (see next
section).

### Curtailment

**Golden (Tasks 11, 16):** structurally vacuous on the golden fixture —
listed in `KNOWN_VACUOUS_TABS` (`test_compare_invariants.py`) because no
golden generator carries a time-varying `p_max_pu`. A dedicated
purpose-built network (`compare_local_networks.build_curtailment_network`)
covers the additivity + rate-bounds invariants for real (75% curtailment
rate, non-degenerate). Cross-surface agreement vs `/results/curtailment`
1.0 (Task 13/prior work).

**Real project (MEASURED) — fills the golden coverage gap for real:**
per-carrier curtailment sums to the total exactly; `system_rate_pct` is
inside `[0, 100]` (`11.5854%`); `total_gwh.total=461.3604 GWh`, only carrier
`solar` (the project's only generator with a `p_max_pu` profile) — genuinely
non-trivial, non-vacuous evidence that this tab's additivity and rate-bounds
logic behaves correctly on real (not purpose-built) data. Still not a
committed regression test — the purpose-built fixture remains the permanent
guard.

### Lost load

**Golden (Tasks 12, 16):** vacuous on golden for the documented reason
(`compare_support.summarise()` passes a guaranteed-nonexistent `project_dir`,
so `_compute_lost_load_summary` always takes the no-capture branch). Covered
for real by a dedicated fixture that writes a synthetic `results_state.pkl`
VOLL capture and checks `cost = energy × VOLL` plus per-bus/per-carrier
additivity (1.0 agreement).

**Real project (MEASURED) — fills the golden coverage gap for real, with an
ACTUAL solved capture:** `available=True`, `voll_eur_per_mwh=100000.0`
(matches `solver_config.json` and the Prices tab's `max_price`, above).
`total_cost_meur.total=43.479637 M€` equals `total_mwh.total(434.7964) ×
voll / 1e6` exactly. `by_bus` and `by_carrier` both sum to the horizon total
exactly (`434.7964 MWh`). Genuine, non-synthetic VOLL shedding on a real
project, matching the identity a purpose-built fixture had to manufacture on
golden.

### Storage cycling

**Golden (Tasks 13, 16):** vacuous on golden — `storage_units_t.p['bess']`
is exactly 0.0 on all 48 snapshots (verified directly), so the one battery
never cycles. `KNOWN_VACUOUS_TABS` documents this as a genuine golden-fixture
coverage gap, not a code defect. Covered for real by a dedicated
`compare_local_networks` fixture: "cycles = throughput / (2 × energy
capacity)" per unit (2.0 cycles, oracle-exact), and "horizon total = AVERAGE
of per-period cycles, never the sum" (2.0, not 4.0 — the naive-sum canary).

**Real project (MEASURED) — fills the golden coverage gap for real:** one
storage unit, `BESS_B3` (battery, `p_nom=100 MW`, `max_hours=4` → 400 MWh
capacity). Horizon `cycles.total=203.1366` equals the average of its three
per-period values (`2027: 167.2652`, `2028: 187.3131`, `2029: 254.8316`) to
`rel=1e-6` — the AVERAGE-not-SUM rule (Task 13) holds on real, non-synthetic
multi-period dispatch, not just the purpose-built fixture. (~200
equivalent-full-cycles/year is aggressive but plausible arbitrage behaviour
against a price series whose mean is 429 €/MWh and whose spikes hit the
100,000 €/MWh VOLL price — not implausible for a battery with no capital
cost pressure to sit idle.)

---

## Frontend defect: Curtailment tab renders an absent payload as zero (Tasks 17–18, NOT FIXED)

**Status: CONFIRMED, deliberately NOT fixed.** Documented via a failing test
(`it.fails`) rather than a code change — this examination's scope was to
find defects, and changing frontend behaviour is a separate product/eng
decision from measuring it.

**The bug:** `CompareView.tsx`'s `EmissionsTab` guards its "no data" banner
with `if (!emA || !emB) return <banner>` — an OR, correctly tripping the
moment EITHER side is `null` (a project that never computed/serialised that
tab). `CurtailmentTab` (`CompareView.tsx:1730`) guards with
`if (!hasAnyA && !hasAnyB)` — an AND, which only trips when BOTH sides are
missing. When only project B's `curtailment` field is `null` (a real state:
Task 19 confirms `ResultsSummary`'s optional fields can legitimately be
absent, e.g. before a tab's compute path has ever run for that project),
the AND guard does not fire, and the tab proceeds to render B's curtailment
as if it existed. Every per-field read then falls through `readPV`
(`CompareView.tsx:2769`, `if (!pv) return 0`) to a literal `0`.

**Measured consequence:** with A's `system_rate_pct` at 100% and B's
`curtailment` payload `null`, the rendered delta is a literal `-100.00%` —
"B eliminated all curtailment" — which is not information the payload
contains. B simply never reported the field. A project that curtails
NOTHING (a real 0%) and a project that never computed curtailment AT ALL
(an absent field) render identically, and the delta fabricates a swing that
does not exist in the data.

**Test:** `pypsa-gui/frontend/src/pages/CompareView.test.tsx`, the `it.fails`
block under `describe('Task 18 — zero vs absent baseline')` — reproduced
2026-08-04, still fails as documented (`33 passed | 1 expected fail` for the
whole file under `npx vitest run`). The adjacent
`'emissions: a null side correctly bails to a message, not a fabricated
-100%'` test in the same `describe` block demonstrates the CORRECT behaviour
side by side, on the same harness, so the contrast is not hypothetical.

**Why not fixed here:** per the task brief that found it, this examination's
job was to find and document defects, not decide which ones ship a fix —
frontend behaviour changes (what "no data" should look like, whether to
distinguish "reported zero" from "never reported" in the UI copy) are a
product decision the same way S1 is. The `it.fails` marker keeps this
defect from silently regressing into "looks green" — if `CurtailmentTab`'s
guard is ever fixed to match `EmissionsTab`'s, this test starts passing and
`it.fails` should be replaced with a normal assertion (removing the
now-redundant failure documentation) as part of that fix.

---

## Task 19 — endpoint wiring: values reach the wire

**Status: VERIFIED, no defect found.** `ResultsSummary`'s docstring
(`models/schemas.py`) says later phases "fill in additional optional
fields" on the payload — every prior test in this suite called the
`_compute_*_summary` functions directly, in-process, never through
`GET /api/projects/{name}/results-summary` itself, so a tab computing
correctly but never reaching the `return ResultsSummary(...)` statement
would have gone uncaught.

**MEASURED** (`pypsa-gui/backend/tests/test_compare_endpoint.py`, new file):
the solved golden network, saved as a real DB-backed, org-scoped project via
the authenticated `client` fixture + a real `POST /api/projects/{name}`
save, then `GET .../results-summary` over HTTP. All nine optional tab
fields (`capacity, dispatch, loading, prices, emissions, economics,
curtailment, lost_load, storage_cycling`) are non-null. `capacity.
capex_meur_by_carrier`, read back from the JSON HTTP response, matches
`compare_support.summarise()` computed in-process on the identical network
to `rel=1e-9` for every carrier and every `by_period` entry — proving the
netcdf round-trip + HTTP/Pydantic serialisation layer preserves the
computed VALUES, not merely their presence.

**Result:** every tab that Tasks 1–18 verified as computationally correct
does reach the real HTTP response. No wiring gap found.

---

## Task 20 — coverage matrix: all ten tabs now accounted for

**Status: DONE, no defect found; documentation/test-infrastructure task.**
`tests/golden/coverage.py::SURFACES` listed ten economic surfaces before
this task, of which only two (`compare_capacity`, `compare_economics`) were
Compare tabs — the other eight tabs (`compare_overview`, `compare_dispatch`,
`compare_loading`, `compare_prices`, `compare_emissions`,
`compare_curtailment`, `compare_lost_load`, `compare_storage_cycling`) had
no entry at all, so `test_golden_coverage.py`'s exhaustive-by-default guard
(every fixture class on every surface is COVERED or EXPLICITLY EXCLUDED)
never looked at them.

**Per-tab component-class coverage**, read off each `_compute_*_summary`
function body (not assumed):

| Surface | Covers | Excludes | Why |
|---|---|---|---|
| `compare_overview` | Generator, StorageUnit | Line, Link | `line_count`/`link_count` are bare counts, never carrier-attributed |
| `compare_dispatch` | Generator, StorageUnit | Line, Link | dispatch is an energy-mix concept; branches carry no dispatch of their own |
| `compare_loading` | Line, Link | Generator, StorageUnit | loading (branch flow magnitude / rating) is branch-only |
| `compare_prices` | (none) | all four | purely bus-level (`buses_t.marginal_price`, grouped by `buses.carrier`) |
| `compare_emissions` | Generator | Line, Link, StorageUnit | emissions = generator dispatch × carrier CO2 intensity |
| `compare_curtailment` | Generator | Line, Link, StorageUnit | curtailment = renewable-availability concept |
| `compare_lost_load` | (none) | all four | purely bus-level (VOLL slack DataFrame, grouped by `buses.carrier`) |
| `compare_storage_cycling` | StorageUnit | Generator, Line, Link | cycling is a storage-only metric |

None of the eight report CAPEX/fixed-cost at all (they report capacity
counts, energy, loading ratios, prices, emissions, curtailment, shedding
cost and cycling — each a genuinely different quantity), so none can carry
an `ADAPTERS` entry in `test_golden_economics.py`'s CAPEX cross-surface
loop; each got a `NO_ADAPTER_REASONS` entry instead — a documented gap, not
a silent one. `ROUTE_SURFACES` (`test_golden_coverage.py`) updated to match:
`get_compare_state` now claims `compare_overview`; `get_results_summary`
claims all nine `ResultsSummary` fields.

**Result:** the coverage matrix's exhaustiveness guard
(`set(ADAPTERS) | set(NO_ADAPTER_REASONS) == set(coverage.SURFACES)`, plus
`test_golden_coverage.py`'s per-class census) now covers all ten Compare
tabs. No new numeric defect found in this task — it is entirely
documentation/test-infrastructure, closing the "silence reads as agreement"
gap the coverage matrix exists to prevent, one level up (at "which surfaces
did we even look at").

---

## Task 21 — real-project spot check (3_nodes_system)

**This section is EVIDENCE, not a regression guard.** The project lives at
`~/Documents/PyPSA GUI/Projects/3_nodes_system` — real user data, not
committed to the repository, and this measurement is a one-off script run
on 2026-08-04, not a pytest test. It must never be read as "covered by the
suite" — every number below was produced once, by hand, and is not
re-checked automatically.

**Method:** `network.nc` loaded directly via `pypsa.Network().
import_from_netcdf(...)` (no PyPSAService, no HTTP, no FastAPI app — the
same standalone-script pattern the Asset Detail horizon-scaling
verification used against this same project, per the task brief). The
project's own `solver_config.json` was loaded into
`routers.simulation._state["solver_config"]` via
`routers.projects._solver_config_from_dict` so CAPEX/economics numbers
resolve against the discount rate/lifetime/CO2 price this project was
actually solved with, not framework defaults. The exact `_compute_*_summary`
functions `routers/compare.py::get_results_summary` calls were then invoked
directly, and the same additivity-walk / invariant logic
`tests/test_compare_invariants.py` runs against the golden fixture was
re-run against the results. **The running desktop app was deliberately NOT
driven** — it is a stale frozen build that predates today's `binding_hours`
fix and would misreport it.

**Network shape:** 5 buses (3 electrical: B1/B2/B3, 1 H2, 1 heat), 2
generators (`Gas_B2` carrier `gas`, `PV_B3` carrier `solar`, both
extendable), 2 extendable Links (`Electrolyzer 1`: B3→H2,
`P2H 2`: B2→heat), 1 StorageUnit (`BESS_B3`, battery, 100 MW / 400 MWh), 3
AC lines, 0 Stores. 3 investment periods (2027/2028/2029), 8,760 snapshots
each = 26,280 snapshots total (vs the golden fixture's 48) —
`multi_investment_periods=True`, `investment_period_weightings.years` = 1.0
per period (Σ = 3.0). `discount_rate=0.07`, `default_lifetime=25.0 yr`,
`voll=100,000 €/MWh`, per-period `co2_price` (100/120/150 €/t across
2027/2028/2029), per-period electrical `load_scalers` (1.0/1.1/1.2). Solved
with `sclopf=True`. `dispatch_status` classified the reloaded network as
`fresh` (`has_solve=True`).

**MEASURED, all PASS:**

- **All nine tab fields non-null.** Same conclusion as Task 19's endpoint
  test, now on a network two orders of magnitude larger (26,280 vs 48
  snapshots) and with real Link/StorageUnit/multi-carrier content.
- **Determinism:** summarising the network twice in-process produces a
  byte-identical `model_dump()` for every one of the nine tabs.
- **Additivity walk** (the same `_walk_period_values`/`_extensive_verdict`/
  `_intensive_verdict` logic `test_compare_invariants.py` uses): judged
  counts per tab — `capacity=8, dispatch=6, loading=12, prices=12,
  emissions=3, economics=32, curtailment=4, lost_load=10,
  storage_cycling=3`. **Every tab judged at least one real comparison** —
  in contrast to golden, where `curtailment`, `lost_load` and
  `storage_cycling` judge ZERO (see `KNOWN_VACUOUS_TABS`,
  `test_compare_invariants.py`) and needed purpose-built fixtures to be
  exercised at all. Zero EXTENSIVE-additivity failures, zero
  INTENSIVE-non-additivity failures (i.e. no INTENSIVE metric was
  accidentally summed).
- **Loading:** `binding_hours ≤ horizon hours` (26,280.0 h) and
  `mean_loading ≤ peak_loading` hold for all 5 branch entries. Most-loaded:
  `Electrolyzer 1` (peak 1.0000, mean 0.8552, binding 1,260.00 h).
- **Prices:** duration curve monotone non-increasing; mean/median inside
  `[min, max]`. `max_price = 100,000.0000 €/MWh` exactly equals the
  project's VOLL, correctly picked up from real shedding snapshots (see
  Lost load below) rather than clipped.
- **Emissions:** per-carrier sums to total exactly (4088.7099 kt both
  sides). Intensity identity holds: `intensity_kg_per_mwh = total_kt × 1e6 /
  total_generator_dispatch_mwh` (reported 306.039935, expected
  306.039935) — the same denominator definition pinned on golden (Task 9),
  confirmed on independent real data.
- **Economics — LCOE identity** holds exactly for all 5 carriers with
  positive dispatch (`gas, solar, battery, h2, heat-pump-air`) — see the
  table in the "Economics" per-tab section above.
- **Curtailment:** per-carrier sums to total; `system_rate_pct = 11.5854%`
  is inside `[0, 100]`; `total_gwh = 461.3604 GWh`, carrier `solar` only.
  **Non-vacuous** — fills the golden fixture's documented coverage gap with
  real (not purpose-built) data.
- **Lost load:** `available=True`; `total_cost_meur = total_mwh × voll /
  1e6` exactly (`43.479637 M€ = 434.7964 MWh × 100,000 / 1e6`); `by_bus` and
  `by_carrier` both sum to the horizon total exactly. **Non-vacuous, and a
  genuine solved VOLL capture** (not a synthetic pickle written for a test).
- **Storage cycling:** 1 unit (`BESS_B3`). Horizon `cycles.total =
  203.1366` equals the average of its three per-period values (167.2652 /
  187.3131 / 254.8316), confirming the AVERAGE-not-SUM rule (Task 13) on
  real multi-period dispatch. **Non-vacuous.**

**MEASURED — S1 escalation, second independent confirmation:**
`capacity.capex_meur_by_carrier` totals `282.389066 M€` (`gas=145.332507 +
solar=137.056558`); `economics.by_carrier[*].capex_meur` totals
`338.581519 M€` (adds `h2=54.2807` and `heat-pump-air=1.9117` to the same
gas/solar figures). Difference = `56.192453 M€`. Computed independently via
`services.solver_service.periodized_capital_costs` (the same oracle
`test_golden_economics.py`'s `_from_asset_costs` adapter uses) — per-link
horizon CAPEX is `Electrolyzer 1 = 54.280730 M€` and `P2H 2 = 1.911723 M€`,
summing to **56.192453 M€ exactly**, matching the tab-level difference to
every printed digit. This is the SAME defect measured on golden (Δ=0.166250
M€ there, equal to the golden fixture's single electrolyzer's CAPEX), now
confirmed on a second, structurally independent, much larger real network
with two Links instead of one — the omission is systematic, not a fixture
artefact. The open product decision (include extendable links in
`_compute_total_annuitised_capex`, or document the omission in the UI) is
unchanged by this measurement; it strengthens the case that whichever
option is chosen, the number at stake is not negligible on real projects
(56.2 M€ here vs the golden fixture's comparatively small 0.17 M€).

**MEASURED — limitation on S2:** `snapshot_weightings["generators"]` and
`["objective"]` are IDENTICAL on this real project too (both produce
26,280.0000 horizon hours) — the same limitation the golden fixture has.
This real-project spot check therefore does **not** independently
re-confirm the `binding_hours` basis fix (S2); that fix remains verified
only by the dedicated `compare_local_networks.build_weighting_basis_network`
fixture (Task 15), which is the only network in this whole examination
whose two weighting columns actually diverge.

**Read:** `pypsa-gui/backend/tests/test_compare_endpoint.py` (Task 19,
committed) proves endpoint wiring on the golden fixture; nothing from this
section is committed anywhere — the script that produced these numbers was
run once from the scratchpad and is not part of the repository.

---

## Known limitations (honest inventory)

Stated plainly so a reader six months from now knows exactly what was and
was not checked, and does not mistake "not raised as a finding" for
"verified."

- **Three tabs are structurally vacuous on the golden fixture** —
  `curtailment` (no generator has a time-varying `p_max_pu`), `lost_load`
  (`compare_support.summarise()` passes a guaranteed-nonexistent
  `project_dir`), `storage_cycling` (the one battery never cycles on flat
  solar + flat demand). Documented in `KNOWN_VACUOUS_TABS`
  (`tests/test_compare_invariants.py`) and covered instead by three
  purpose-built `compare_local_networks.py` fixtures. Task 21's real-project
  spot check additionally exercises all three for real (461 GWh curtailed,
  434.8 MWh of genuine VOLL shedding, 203 cycles/yr of real battery
  arbitrage) — but that is a one-off measurement, not a second permanent
  regression guard; the purpose-built fixtures remain the committed tests.
- **Neither large network available to this suite (golden or
  `3_nodes_system`) has divergent `objective`/`generators` snapshot
  weighting columns.** Both report identical horizon hours under either
  basis. The `binding_hours` basis fix (S2) is verified ONLY by the
  dedicated `build_weighting_basis_network` fixture (Task 15) — a real
  representative-week run (where the two columns genuinely differ, per
  CLAUDE.md's own note that `sample_representative_weeks` resets weights on
  promotion to multi-period) has not been observed by this examination.
- **S1 remains an open product decision**, now measured on two independent
  networks: golden (Δ=0.166250 M€, one electrolyzer) and `3_nodes_system`
  (Δ=56.192453 M€, two Links). Both differences equal exactly the sum of
  the omitted Links' own horizon CAPEX — CONFIRMED, not merely suspected,
  on both. The two options remain: (1) include extendable Links in
  `_compute_total_annuitised_capex`, leaving fixed/passive branches out;
  (2) keep the current omission and surface it explicitly in the Capacity
  tab's UI copy. No fix applied in this examination, per its explicit
  scope (measure and escalate, not decide).
- **The Curtailment tab's frontend AND-vs-OR null-guard bug (Tasks 17–18)
  is confirmed and deliberately NOT fixed** — see its own section above.
  Frontend behaviour changes are a product decision outside this
  examination's scope.
- ~~**`get_prices()` duplicates the merit-order correction inline**~~ —
  **CLOSED 2026-08-09 (`02b5e806`).** The inline copy implemented only the
  first of the shared helper's two branches; a subsidised renewable pinned
  AT its ceiling with the dual below its effective LP cost was corrected by
  `corrected_marginal_prices` (and so by `/asset_economics` and both Compare
  price surfaces) but left as the raw negative dual on the Prices tab. The
  drift this entry predicted was therefore already present, not merely
  latent — it just could not be observed on either network available to this
  suite, both of which lack the triggering configuration.

  The algorithm now lives in `_apply_merit_order_correction(n, prices)`,
  which takes already-fetched duals; `corrected_marginal_prices` and
  `get_prices` each keep their own fetch. That split is what made the
  collapse possible: `corrected_marginal_prices` hardcodes `source="lopf"`,
  so `get_prices` could never have called it without losing its own `source`
  parameter — which is why the copy grew in the first place. Guarded by
  `tests/test_prices_merit_order_parity.py`, whose parity assertion pins the
  two surfaces to each other rather than to hardcoded values.

  **Still open:** `get_asset_economics` holds a THIRD copy. It implements
  both branches, so it is a maintenance duplicate rather than a drift
  source, and collapsing it would touch an economics surface — deliberately
  left rather than swept into the Prices fix.
- **No fixture or real project available to this examination exercises a
  Store.** `_compute_total_annuitised_capex` and `_compute_economics_summary`
  both walk `Store` in their component lists (per S1's own writeup and
  `coverage.py`'s citations), but neither the golden fixture nor
  `3_nodes_system` contains one (`n.stores` is empty on both) — so the
  Store branch of either function's code path is read, not measured, in
  this examination.

---

## Final summary — all ten tabs and both defects

| Tab / item | Verdict | Key evidence |
|---|---|---|
| Overview | Agreement (no defect) | INFERRED code read (Task 20); MEASURED real-project capacity match |
| Capacity | Agreement, minus S1 | S1 CONFIRMED/escalated, both networks; otherwise 1.0 agreement |
| Dispatch | Agreement (no defect) | MEASURED energy-basis identity (golden); MEASURED additivity (both) |
| Line loading | Fixed (binding_hours), else agreement | S2 binding_hours FIXED; mean_loading/peak invariant on both networks |
| Prices | Agreement (no defect) | S2 prices half CLEARED; monotonicity + range hold on both networks |
| Emissions | Agreement (no defect) | Intensity-denominator pinned (Task 9); identity holds on both networks |
| Economics | Agreement, minus S1's other half | LCOE identity 1.0 (S3 CLEARED) on both networks |
| Curtailment | Agreement (no defect) | Vacuous on golden, non-vacuous + correct on real project (Task 21) |
| Lost load | Agreement (no defect) | Vacuous on golden, non-vacuous + correct on real project (Task 21) |
| Storage cycling | Agreement (no defect) | Vacuous on golden, non-vacuous + correct on real project (Task 21) |
| S1 — Capacity/Economics CAPEX gap | CONFIRMED, escalated | Δ=0.166250 M€ (golden) and Δ=56.192453 M€ (real), both == omitted Link CAPEX exactly |
| S2 — binding_hours basis | CONFIRMED, FIXED | 3.0 h → 9.0 h, one-line fix; unverifiable on either large network (both weighting-basis-degenerate) |
| S2 — mean_loading / prices basis | CLEARED | Invariant to the basis under uniform rescaling |
| S3 — LCOE time basis | CLEARED | Ratio 1.0 exactly, 4 (golden) + 5 (real) carriers |
| Curtailment frontend AND/OR guard | CONFIRMED, NOT FIXED | `it.fails` test; -100% fabricated from a null payload |
| Task 19 — endpoint wiring | Verified, no defect | All nine fields non-null over real HTTP; values match `rel=1e-9` |
| Task 20 — coverage matrix | Done, no defect | All ten tabs now in `coverage.SURFACES`; exhaustiveness guards pass |
