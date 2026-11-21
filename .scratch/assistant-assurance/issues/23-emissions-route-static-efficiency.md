<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Emissions route and workbook ignore a time-varying generator efficiency

Status: needs-triage
Type: task
Found by: AA-10 S4 metric parity (`pypsa-gui/backend/tests/assurance_acceptance/test_p4_metric_parity.py`), 2026-11-17.

## Problem

`services/results/emissions.py` (`compute_emissions`) divides each generator's weighted energy by its static `efficiency` column. When `generators_t.efficiency` varies by snapshot, PyPSA's CO2 constraint and the checked answer (`harness/emissions_calculations.py`, `network-emissions/1` and `generator-emissions/1`, via `get_switchable_as_dense`) use the per-snapshot value, so the surfaces disagree:

On S4's hand network with G1's efficiency 0.5, 0.5, 0.25 the checked total is 57.5 tCO2; `GET /results/emissions` and the workbook's "Total emissions" (which reads the same service) show 37.5.

The by-generator and by-carrier rows, the CO2 cap's `used_tCO2` and slack, and every reader of `compute_emissions` share the static division.

## Scope

Use the time-varying efficiency in `compute_emissions` as the LP does, then flip S4's `_pending` row in issue 10 to `_covered` with the route and workbook asserted equal to the checked 57.5. Needs a GK B plan; check the fuel-curve branch (curve slope) keeps its own rule.
