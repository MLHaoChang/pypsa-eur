<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Workbook engine, Overview KPIs and finance sheets

Status: ready-for-agent
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

- [ ] The Overview has per-period and horizon capex (overnight and annualised), opex, system cost, energy served and system/per-carrier LCOE, matching `compute_asset_economics` / `economics_by_carrier` within 1e-9 relative on a fixture network with two investment periods.
- [ ] With an investment case: NPV, IRR, payback, DSCR and financial LCOE equal `load_ic_report`'s values; the finance sheets reuse `finance/export_xlsx.py`; NPV cells are live formulas. Without one: each shows `not_established` and "run the investment case"; nothing is computed or run.
- [ ] The Overview has three native charts (capex vs opex by period; LCOE by carrier by period; cash flow with cumulative NPV when the case exists), each referencing the sheet's own cells (tests read `ws._charts` and the series refs).
- [ ] A stale run is refused with a named error kind; formula-like text is neutralised; the 25 MB cap holds.
- [ ] The workbook opens in openpyxl (load_workbook round trip) and in LibreOffice headless if available in CI; otherwise say so.

## Out of scope

Per-view sheets beyond Overview/finance (02); the UI picker and the chat tool (03).
