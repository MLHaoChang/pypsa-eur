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

- **`objective_decomposition`** needed no code change. It compares the LP
  total with `cost_breakdown.total`, which now reconciles.
- **Energy-Hub `compute_tea`** divides `cost_at_target_eur` by served energy.
  That cost is `n.objective − shed cost` (`adequacy/report.py`), which
  already includes FOM.

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
| Backend `-m "not slow"` | 5570 passed, 29 failed, 107 errors, 31 skipped | pending (run in progress) |
| Frontend `npx vitest run` | 1940 passed | 1940 passed |
| `qa_asset_economics.py` | not rerun | 26/26 pass |
| `qa_cost_decomp_overnight.py` | not rerun | 10/10 pass |
| `qa_objective_scale.py` | not rerun | 5/5 pass |
| `qa_results_summary_compare.py` | not rerun | 53/53 pass |

Every backend failure and error on both sides is in desktop, GridSpine,
shutdown and packaging tests. Those need `pywebview` and GridSpine tooling
that this container does not have. None touch economics, and the set is the
same before and after.

Existing tests updated to the new semantics:

- `test_asset_economics_capital_costs.py`: the hand-built generator's fixed
  cost is now `(1000 + 20) × 100`.
- The golden fixture's `gas` generator now carries `fom_cost = 50`, so the
  cross-surface agreement test covers FOM on every surface at once. The
  oracle gains `fixed_cost_rate`, and the `asset_costs` adapter reads
  `fixed_cost`.
- `test_compare_cross_surface.py` reads `fixed_cost` for its expectation.

Environment: Python 3.11 venv with pypsa 1.1.2, linopy 0.8.0, highspy 1.14.0,
pandas 2.3.3 and xarray 2025.6.1. Pandas 3 broke multi-period solves in this
venv, so pandas was pinned to the lock file's 2.3.3.

## Deliberately left alone

- **FOM units.** PyPSA adds `fom_cost` unscaled, as a per-horizon figure,
  exactly like a raw `capital_cost`. Only an overnight-priced investment is
  scaled by `nyears`. On a 24-snapshot model, a typed annual FOM is charged
  as if one day were a full year. The GUI now reports what the LP paid. The
  property tooltips still say "€/MW/yr" for `fom_cost`, which matches how
  they already describe `capital_cost`. Changing that is a modelling-UX
  decision, not a reconciliation fix.
- **FOM on new capacity in `capex_expansion`** is multiplied by total
  horizon years without checking per-period asset activity. That matches
  `asset_economics`'s `total_years_factor`. An asset retired partway through
  a multi-period horizon would be slightly over-counted there.
- **The golden fixture's existing objective gap.** `cost_breakdown.total`
  still exceeds the LP total on the golden network. The cause is the
  non-extendable line's `capital_cost`, which `n.statistics()` counts and
  the LP does not. That gap predates this change and is documented in
  `services/cost_totals.py`.
- **Upstream docstring.** PyPSA's `statistics.capex` docstring claims it
  includes FOM. It is not fixed here.
