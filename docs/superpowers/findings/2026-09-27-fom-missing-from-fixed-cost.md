# Fixed cost left out FOM on every economic surface

**Date:** 2026-09-27
**Branch:** `claude/fix-fom-reconciliation`
**Doctrine:** `docs/superpowers/specs/2026-08-01-trustworthy-numbers-design.md`, ADR-0001
**Regression test:** `pypsa-gui/backend/tests/test_fom_reconciliation.py`

## What was wrong

PyPSA 1.1.2 (pinned) charges each extendable asset
`periodized_cost × p_nom_opt` in the LP objective, where

```
periodized_cost = capital_cost (annuitised investment) + fom_cost
```

`pypsa/optimization/optimize.py` reads `c.periodized_cost`, which passes
`fom_cost=static["fom_cost"]` to `pypsa.costs.periodized_cost`. The
`c.capital_cost` accessor is the same function with `fom_cost=None`.

Every GUI economic surface priced fixed cost off `capital_cost` alone:

- `periodized_capital_costs` returned only `capital_cost`, and
  `asset_economics`, Compare (`_safe_capital_cost`) and Asset Detail
  (`capex_annual`) all read it.
- `lcoh` read `n.c["Link"].capital_cost` directly.
- `cost_breakdown`, `cost_totals.horizon_system_cost` and `/results/statistics`
  sum `n.statistics()` "Capital Expenditure". That column is also
  investment-only: `statistics.capex` multiplies capacity by
  `comp.capital_cost`, and FOM is reported separately by `statistics.fom()`.
  PyPSA's own `capex` docstring says "investment + fom_cost"; the code does not.

`asset_economics` computed FOM but published it "for information only", and
not on the horizon basis of the fixed cost it described.

### Measured

A four-snapshot network with one extendable generator
(`capital_cost=1000`, `fom_cost=200`) and one extendable electrolyser Link
(`capital_cost=500`, `fom_cost=50`), solved with HiGHS:

| Quantity | Master | This branch |
|---|---:|---:|
| LP objective | 175,371.43 | 175,371.43 |
| `cost_breakdown.total` | 148,228.57 | 175,371.43 |
| `asset_economics` gas `fixed_cost_eur` | 128,571.43 | 154,285.71 |
| gas LCOE, EUR/MWh | 260.00 | 310.00 |
| `lcoh` `capex_eur_per_year` | 14,285.71 | 15,714.29 |

The master gap is exactly `fom_cost × p_nom_opt` for each asset.

## Semantics decided

**Fixed cost reported to the user is annuitised investment plus FOM.** That
is the number the objective paid, so it is the only one that reconciles.
FOM stays broken out as its own line on every surface that has one.

- `periodized_capital_costs` entries gain `fom_cost` and `fixed_cost`.
  `capital_cost` keeps its old meaning, investment only.
- `asset_economics`: `fixed_cost_eur`, `net_profit_eur`, LCOE and LCOS use
  the fixed rate. `fom_cost_eur` is now scaled by horizon years like
  `fixed_cost_eur`, so it reads as the FOM share of it.
- `cost_breakdown` adds `n.statistics.fom()` to every "Capital Expenditure"
  cell, per component, carrier and period. New `fom` fields break it out.
  FOM on new capacity is added to `capex_expansion`, so "existing CAPEX"
  on the waterfall does not absorb it. `*_lifetime` figures are upfront
  investment and never contain FOM.
- `cost_totals.horizon_system_cost` gets the same addition, so the solve
  queue's reported cost agrees with `cost_breakdown`.
- `/results/statistics` keeps PyPSA's "Capital Expenditure" column as PyPSA
  defines it and gains a "Fixed O&M" column. The two together equal the
  fixed cost the objective paid.
- `lcoh`: `capex_eur_per_year` now holds the fixed cost. New
  `fom_eur_per_year` and per-period `fom_eur` fields break FOM out.
- Asset Detail `capex_annual` and `fixed_cost_eur` read the fixed rate. The
  registry label is now "Annualised fixed cost" and the formula strings say
  `(capital_cost + fom_cost) × …`. The export workbook uses the same compute
  functions.

No API field was renamed. Fields were only added.

## The nine surfaces

| Surface | Change |
|---|---|
| `/results/cost_breakdown` | FOM folded into `capex`, broken out as `fom` |
| `/results/asset_economics` | Fixed cost, net profit, LCOE/LCOS include FOM |
| `/results/economics_by_carrier` | Via Compare economics `_safe_capital_cost` |
| `/results/lcoh` | CAPEX includes FOM, new `fom_eur_per_year` |
| `/results/statistics` | New "Fixed O&M" column |
| `/simulation/asset_costs` | New `fom_cost` and `fixed_cost` per asset |
| `/results/asset/{class}/{name}` | `capex_annual`, `fixed_cost_eur` include FOM |
| `…/export.xlsx` | Same compute functions as the line above |
| Compare per-carrier and per-asset | Via `_safe_capital_cost` |

Checked and left unchanged:

- **Energy-Hub `compute_tea`** divides `cost_at_target_eur` by served energy.
  That cost is `n.objective − shed cost` (`adequacy/report.py`), which
  already includes FOM.

`objective_decomposition` gained a reconciliation bridge in the follow-up
below.

## Frontend

- The Economics tab's fixed-cost KPI, table header, methodology note and LCOH
  panel now say "annuitised CAPEX + fixed O&M".
- The Dispatch tab's "CAPEX (annuitised)" tooltip and the Capacity Expansion
  total-cost tooltip say the same.
- Capacity Expansion's "Annualised" mode reads `fixed_cost` from
  `/simulation/asset_costs`, so the per-asset table matches the KPI row.
- The API types gain the new optional fields.

## Test evidence

The new test file has 15 tests. Twelve solve for real (`live_solve`) and use
`n.objective` minus hand-computed variable cost as the oracle. Three use
hand-built networks with the numbers written down. One test pins the
upstream behaviour itself: the objective charges FOM while
`statistics.capex` does not. If PyPSA ever folds FOM into
`statistics.capex`, that test fails and `cost_breakdown` must stop adding it.

| Run | Master | This branch |
|---|---|---|
| `test_fom_reconciliation.py` | 14 failed, 1 passed | 15 passed |
| Backend `-m "not slow"` | 5570 passed, 29 failed, 107 errors, 31 skipped | 5585 passed, 29 failed, 107 errors, 31 skipped |
| Frontend `npx vitest run` | 1940 passed | 1940 passed |
| `qa_asset_economics.py` | not rerun | 26/26 pass |
| `qa_cost_decomp_overnight.py` | not rerun | 10/10 pass |
| `qa_objective_scale.py` | not rerun | 5/5 pass |
| `qa_results_summary_compare.py` | not rerun | 53/53 pass |

Every backend failure and error on both sides is in desktop, GridSpine,
shutdown and packaging tests. Those need `pywebview` and GridSpine tooling
that this container does not have. None touch economics, and the set is the
same before and after: the 136 failing and erroring test ids match exactly.
Collection grew from 5737 to 5752, which is the 15 new tests, and all 15 pass.
Both backend rows are counted from the progress marks, because the master run
printed no summary line. The branch run's own summary reads 5585 passed,
29 failed, 107 errors and 32 skipped; the extra skip is a counting difference
in pytest's summary, not a new skip.

Existing tests updated to the new semantics:

- `test_asset_economics_capital_costs.py`: the hand-built generator's fixed
  cost now includes its FOM, scaled to the two modelled hours.
- The golden fixture's `gas` generator carries an annual `fom_cost` of
  20,000 EUR/MW/yr, so the cross-surface agreement test covers FOM on every
  surface at once. The oracle gains `fixed_cost_rate` and `fom_per_horizon`,
  and the `asset_costs` adapter reads `fixed_cost`.
- `test_compare_cross_surface.py` reads `fixed_cost` for its expectation.
- `test_solver_facade_surface.py` registers the three new facade names.

Environment: Python 3.11 venv with pypsa 1.1.2, linopy 0.8.0, highspy 1.14.0,
pandas 2.3.3 and xarray 2025.6.1. Pandas 3 broke multi-period solves in this
venv, so pandas was pinned to the lock file's 2.3.3.

## Follow-up: the three items first left alone

### FOM units

PyPSA adds `fom_cost` unscaled, per modelled horizon, while an
overnight-priced investment is scaled by `nyears`. The GUI asks for FOM in
EUR/MW/yr, so on a 24-snapshot model a typed annual FOM was charged as if one
day were a full year: 365 times too much.

The periodized-cost fill that already wraps every GUI solve and every report
now scales `fom_cost` by `n.nyears` for the duration of the block, and the
revert restores the typed value exactly. Solve and reports therefore see the
same figure.
- A nesting guard stops a fill inside another fill from scaling twice. It is a
  module-level registry keyed by network identity. A first version stored a
  flag on the network, and PyPSA then tried to export that flag to netCDF on
  the next project save.
- A full-year model has `nyears = 1` and is untouched.
- When investment periods represent different spans, the mean is used and a
  warning logged. PyPSA refuses `overnight_cost` in the same situation;
  refusing FOM would block every such solve.
- `/simulation/asset_costs` reports `fom_cost` on the per-horizon basis, like
  `capital_cost`, plus `fom_cost_annual` as typed.
- The five FOM input tooltips now say the charge is scaled to the share of a
  year the snapshots represent.

Measured on a network whose four snapshots cover half a year: the raw PyPSA
solve charges the full annual FOM, and the GUI solve charges half.

### New-capacity FOM, and a bug it exposed

The first fix added new capacity's FOM for every horizon year, active or not.
It now counts only the periods in which the asset is active, weighted by
that period's years. That is the same rule PyPSA's statistics apply to the
installed figure.

Testing that exposed an older, larger defect in `capex_expansion`. On a
multi-period network PyPSA's `expanded_capex` returns a DataFrame with one
column per period. The parser iterated it as a Series, walked the columns,
skipped every value as a bare year, and fell back to a manual sum with no
years weighting and no activity check. On a two-period network with 10 years
per period, EUR 3.8 M of new-capacity investment was reported as 0.38 M. The
Capacity Expansion waterfall shows existing CAPEX as `capex − capex_expansion`,
so the difference was booked as existing-fleet cost.

`capex_expansion` is now one calculation: per period, fixed cost (investment
plus FOM) times capacity added, over active assets, times the period's years.
The regression test checks it against PyPSA's own per-period `expanded_capex`
times years, plus FOM. It covers the vintage case the old fallback existed
for.

### The golden fixture's objective gap

The gap has two legitimate causes, not one.
- **Non-extendable fixed cost.** Reporting counts it; the LP cannot size those
  assets, so it never charges them. On the golden network that is mainly the
  line's EUR 7.5 bn.
- **Period weighting.** Reporting weights each period by
  `investment_period_weightings.years`, the undiscounted money spent. The LP
  weights it by `.objective`, which is 1.0 by default and PV times years
  under auto-discount.

Forcing the two totals equal would be wrong, so `objective_decomposition`
now explains the gap instead:

```
gap_eur = −nonextendable_fixed_cost_eur
          + period_weighting_adjustment_eur
          + residual_gap_eur
```

`lp_basis_total` is the reported cost restated on the LP's basis, and
`residual_gap_eur = lp_total − lp_basis_total`. On a plain solve the
residual is zero. Anything left there is an LP term the reporting surfaces do
not model, such as the curtailment-subsidy wrapper or VOLL slacks. Existing
fields keep their meaning. The bridge is skipped under myopic foresight,
where `n.objective` holds only the last period's LP.

| | Golden fixture | Two-period test |
|---|---:|---:|
| LP total | 431,622.96 | 25,682,000.00 |
| `cost_breakdown.total` | 7,528,015,383.89 | 56,845,500.00 |
| `gap_eur` | −7,527,583,760.93 | −31,163,500.00 |
| Non-extendable fixed cost | 7,524,778,211.68 | 35,500.00 |
| Period-weighting adjustment | −2,805,549.25 | −31,128,000.00 |
| Residual | 0.00 | 0.00 |

The two-period test network uses objective weights of 7 and 4 against years
of 10 and 10.

## Test evidence for the follow-up

`test_fom_reconciliation.py` now has 23 tests, all passing:
- the raw-PyPSA contract, which pins that FOM is added unscaled
- the GUI solve charging `fom × nyears`
- nested fills scaling once and reverting exactly
- a full-year model left untouched
- every surface against the objective at `nyears = 0.5`
- new-capacity FOM counting only active periods
- the multi-period and golden bridges closing to zero residual

| Run | Result |
|---|---|
| Backend `-m "not slow"` | 5599 passed, 29 failed, 107 errors, 32 skipped. The 136 failures and errors are the same test ids as on master, all desktop, GridSpine, shutdown and packaging tests; passed grew by 14, the 8 new FOM tests plus 6 facade-surface checks for the 3 new re-exports |
| Frontend `npx vitest run` | 1940 passed; `tsc --noEmit` clean |
| QA drivers | asset economics 26/26, overnight cost decomposition 10/10, objective scale 5/5, results-summary compare 53/53, safe capital cost exit 0 |

## Second follow-up: the items that were still open

### A directly typed `capital_cost` is annual too

PyPSA uses a directly typed `capital_cost` unscaled, per modelled horizon, and
the GUI labels it €/MW/yr, the same mismatch FOM had. The periodized-cost fill
now scales `capital_cost` by `n.nyears` alongside `fom_cost`, only where
`overnight_cost` is unset. PyPSA ignores `capital_cost` for overnight-priced
assets, which it already scales.

- **Existing projects change.** A project priced through `capital_cost` on a
  model shorter than a year now pays the modelled share of its annual cost, in
  the LP and in every report. A full-year model has a factor of 1 and is
  unchanged. On the golden network the line's fixed cost drops from EUR
  7.5 bn to EUR 20.5 M: 1 M EUR/MVA/yr × 500 MVA for 24 of 8,760 hours in each
  of 15 years.
- **Upfront costs back-calculated from `capital_cost` are now right.** PyPSA
  divides by `annuity × nyears`, so an unscaled `capital_cost` on a one-day
  model back-calculated an upfront cost 365 times too high. That feeds
  Capacity Expansion's "Total over lifetime" view.
- **A hazard found on the way.** For an asset without `overnight_cost`,
  PyPSA's `capital_cost` accessor returns the column itself, not a copy. LCOH
  read it inside the fill, and the revert then restored the typed values into
  the object LCOH was holding. It now copies the series.
- The five `capital_cost` input tooltips say the charge is scaled to the share
  of a year the snapshots represent.

### Fixed cost only in the periods an asset exists

Four surfaces multiplied fixed cost by every horizon year, while
`cost_breakdown` and the LP charge only periods where PyPSA considers the
asset active. The four were asset economics, Asset Detail, Compare's
per-carrier economics and Compare's capacity totals. An asset retired partway
through the horizon was over-charged on those four.

All five now share `period_utils.active_period_years`: `years[p]` where the
asset is active, 0 where it is not.
- **Asset economics:** per asset and per period.
- **Asset Detail:** active years times the selected window's share of each
  period. With the whole horizon selected this is exactly the asset-economics
  figure.
- **Compare:** both walks take the same per-row activity. An earlier comment
  there claimed PyPSA's statistics do not mask activity. They do, measured, and
  the comment is corrected.

For a vintage parent the rule uses the parent row's activity, the same as
`cost_breakdown`. So the earlier Compare case, where gating on each vintage's
build year under-counted a battery, keeps its full-horizon figure.

On the retirement test network, `old` and `nonext` stop paying after 2030 and
`new` pays only from 2040. All four surfaces now report the same per-asset
figures, and they sum to `cost_breakdown.capex`.

### PyPSA's `statistics.capex` docstring

That docstring lives in the upstream PyPSA repository, which this session
cannot reach; only this fork is attached. The GUI no longer relies on it. The
raw-PyPSA contract test pins the actual behaviour, so an upstream change
fails loudly here. Ready to file upstream:

> **`statistics.capex` "See Also" says it includes `fom_cost`; it does not**
>
> PyPSA 1.1.2, `pypsa/statistics/expressions.py`. The "See Also" blocks of
> `overnight_cost` (line 991) and `fom` (line 1075) describe `capex` as
> returning "total fixed costs (overnight_cost + fom_cost)" and "(investment
> + fom_cost)". `capex` multiplies capacity by `comp.capital_cost`, which
> calls `periodized_cost(..., fom_cost=None)`. So it is investment-only, while
> the objective uses `comp.periodized_cost`, which adds `fom_cost`. A user
> summing `statistics.capex()` and `statistics.opex()` to reconcile with
> `n.objective` is short by `statistics.fom()`.
>
> Suggested fix: change both lines to "Returns annuitised investment costs
> (excluding fom_cost; see `fom`)". Optionally add a `statistics.fixed_cost()`
> that returns `capex + fom`, the quantity the objective charges.

## Test evidence for the second follow-up

`test_fom_reconciliation.py` now has 26 tests. The new checks are activity on
asset economics, Asset Detail and both Compare paths, and `capital_cost`
scaling and restoration. Run against the previous commit's service code, the
five that test the new behaviour fail. On this branch they pass.

Existing tests updated to annual `capital_cost`:
- **Hand-built expectations** now use the modelled share of a year:
  `test_asset_economics_capital_costs`, both Asset Detail compute tests and the
  `qa_asset_economics` driver's scenarios 1 and 5.
- **Fixed link fixture:** `test_asset_economics_links` types an annual cost
  whose two-hour share is 1,000 EUR/MW, so every figure in it is unchanged.
- **Capex-parity fixtures** for line, link and store are weighted to model one
  full year, so the typed cost is charged in full.
- **Totals contract:** `test_cost_totals_contract` reads its unweighted
  baseline inside the same fill as the total it is compared with.
- **Golden anchors:** the line and solar anchors use
  `oracle.capital_cost_per_horizon`.

| Run | Result |
|---|---|
| Backend `-m "not slow"` | SECOND_FOLLOWUP_BACKEND |
| Frontend `npx vitest run` | SECOND_FOLLOWUP_FRONTEND |
| QA drivers (`run_qa_drivers.py`) | all 21 pass |
