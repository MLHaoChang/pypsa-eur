<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Results workbook "Mean price" is the unweighted mean; the checked mean price is weighted

Status: needs-triage
Type: task
Found by: AA-10 S4 metric parity (`pypsa-gui/backend/tests/assurance_acceptance/test_p4_metric_parity.py`), 2026-11-17.

## Problem

For one bus over one run, two surfaces show a "mean price" with different numbers once the snapshot weightings are not uniform:

- the checked answer (`harness/price_calculations.py`, `bus-price/1`, `mean_price`) is the generator-weighted mean Σ w·p / Σ w;
- the results workbook's Prices sheet (`services/results_workbook/views_network.py`, `_prices`: "Mean price — <bus>", and the "Mean price by bus and period" table) is the plain mean over snapshots. Its note says so.

On S4's hand network (prices 0, 10, 30 EUR/MWh at weights 1, 0.5, 2) the checked mean is 65 / 3.5 = 18.571… and the workbook cell is 40 / 3 = 13.333…. With uniform weights the two agree, which is why existing fixtures did not show it. The Prices route serves the series only, with no mean.

## Scope

Decide which mean the workbook shows (the weighted one, as the checked answer and an energy-weighted reading need; or both, labelled), then change the cell and its table, and flip S4's `_pending` row in issue 10 to `_covered` with a test that the workbook cell equals the checked `mean_price`. Needs a GK B plan; owner call if the label or the plain mean stays.
