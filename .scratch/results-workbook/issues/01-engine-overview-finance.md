<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Workbook engine, Overview KPIs and finance sheets

Status: done
Type: task
Blocked by: none
Gated: yes (finance; GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) first; its owner decisions RW-CHARTS-1, RW-TABS-1, RW-KPI-1 and RW-PLAN-1 bind this issue.

## What to build

- A new module `pypsa-gui/backend/services/results_workbook/` that builds one openpyxl workbook for a project's current run: About, Overview and (when the investment case exists) the finance sheets, per spec §"The workbook" items 1–3 and 5.
- A small native-chart helper (`openpyxl.chart`: bar, stacked bar, line, scatter) that draws a chart from a cell range on the same sheet, so package 02 can reuse it.
- A sheet-builder registry keyed by result view (`services/result_views.py` names), which 02 fills. In this issue it holds About, Overview and the finance sheets only.
- The included-views resolution: the selected cards from `flow_scope.json` plus an explicit `extra_views` list or `all`, per RW-TABS-1.
- `GET /api/results/workbook.xlsx` (query: `views=`, `all=`), guarded like the existing exports, with route parity and access policy declared.

## Acceptance

- [x] The Overview has per-period and horizon capex (overnight and annualised), opex, system cost, energy served and system/per-carrier LCOE, matching `compute_asset_economics` / `economics_by_carrier` within 1e-9 relative on a fixture network with two investment periods.
- [x] With an investment case: NPV, IRR, payback, DSCR and financial LCOE equal `load_ic_report`'s values; the finance sheets reuse `finance/export_xlsx.py`; NPV cells are live formulas. Without one: each shows `not_established` and "run the investment case"; nothing is computed or run.
- [x] The Overview has three native charts (capex vs opex by period; LCOE by carrier by period; cash flow with cumulative NPV when the case exists), each referencing the sheet's own cells (tests read `ws._charts` and the series refs).
- [ ] RW-SCEN-1: the engine API takes the set of runs (base plus every sensitivity/experiment run); with more than one, the first sheet after About compares their key KPIs (Overview KPIs, IC KPIs where present) with deltas vs base, per period and over the horizon, with native charts, reusing the existing experiment/sensitivity comparison services. A test on a fixture with a base and two sensitivities checks the values against those services. Partly built on #874: sensitivity runs (Scenarios sheet); experiment runs are with the coordinator.
- [x] A stale run is refused with a named error kind; formula-like text is neutralised; the 25 MB cap holds.
- [x] The workbook opens in openpyxl (load_workbook round trip) and in LibreOffice headless if available in CI; otherwise say so.

## Out of scope

Per-view sheets beyond Overview/finance (02); the UI picker and the chat tool (03).

## Notes from the build

- Overnight capex is a horizon figure (`cost_breakdown.capex_lifetime`, the present value of the upfront investment); its period cells read `not_established` because no service splits it by period. Fuel reads `not_established`: PyPSA's marginal cost does not separate fuel from variable O&M.
- The finance sheets are written when the `investment` view is included (the Commercial & finance card, or `views=investment`), prefixed "Case ".
- LibreOffice: `tests/test_results_workbook.py` recalculates the workbook headless and checks the live NPV and system LCOE. CI installs no LibreOffice, so there that step skips; it passed in the build container (LibreOffice 24.2.7.2).
- GK B plan conditions: one source per row, with a reconciliation row (total system cost − (annualised capex + opex)). Energy served uses the cost columns' weighting, and each LCOE divides undiscounted cost by undiscounted energy. An unknown value is an empty cell with the reason on its row, and no formula reads an empty or text cell. A stored investment case that is stale against the current inputs (`investment_case_runner.staleness`, as `GET /investment_case` uses) is labelled "from an earlier run", and its figures are not shown.
