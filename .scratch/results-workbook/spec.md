<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Results workbook: one Excel file of a run's results, with native charts and an investment-grade overview

## The owner's request (2026-10-28, verbatim)

> integrate a additional module to do the export of Excel-based export of all the results, enable the export and automatic creation of charts that cover the key results of the different tabs or story cards that was chosen by the user, and depending also on the workflow. So the Excel should contain all the results that was run, but structured in different tabs, and you should have also visualized tab or graphics of your key results per tab, right? That you can export as one coherent file, and it should have an overview of all your key informations, maybe specifically focusing on financial metrics as well as these investments, capex, opex, and financial LCOEs. I think these very high level investment grade needed KPIs need to be computed and calculated and summarized and produced also as charts to be executed. It should show all the results of the entire simulation horizon, basically, for detailed analysis in different tabs.

## Owner decisions

- **RW-CHARTS-1: native Excel charts.** Charts are real Excel charts (`openpyxl.chart`) drawn from the workbook's own data cells, so they stay editable and follow edits. A chart type Excel lacks natively (e.g. a waterfall) becomes the nearest native form (a sorted bar), and says so in its title.
- **RW-TABS-1: selected cards, plus more.** By default the workbook has one sheet per result view of the cards the user switched on for the project (`flow_scope.json`, `services/flow_scope.py` `CARDS`, `selected()`), plus Overview and Summary. Before export the user can tick further result tabs, or "all".
- **RW-KPI-1: investment case if run, else flagged.** The Overview always shows capex, opex, annualised cost and system and per-carrier LCOE per investment period and over the horizon, from the optimisation. NPV, IRR, DSCR, payback and financial LCOE come from the project's investment case when one exists; otherwise each shows `not_established` with "run the investment case" (ADR-0001: no fabricated numbers). Export never runs the investment case itself.
- **RW-SCEN-1: scenarios in the workbook.** When the project ran several scenarios (sensitivities, experiments), their results go into the workbook too, and the key KPIs are compared on one sheet against the base case (owner's words in `docs/agents/owner-decisions.md`).
- **RW-PLAN-1: three packages in sequence.** 01 engine + Overview + finance sheets (Opus, GK B), then 02 one sheet and chart per remaining result view (Opus, GK B), then 03 the export picker in Hand-off › Exports plus a chat tool (Sonnet, UX). Each merges before the next opens.

## What exists (reuse, don't duplicate)

Paths are under `pypsa-gui/backend/` unless stated.

- **Writers:** `services/finance/export_xlsx.py` (investment-case workbook: About, Summary, one sheet per `IC_REPORT_SECTIONS` section, CashflowLines; `not_established` text; formula-injection guard `_put`); `services/study/report_xlsx.py` (guided study report); `services/study/workbook_view.py` (injection-safe `text`/`formula` cell writers, live NPV formulas in `engine_cash_flows`, `kpis`); `services/asset_results/export.py` (one asset, pandas on openpyxl, `_style`). None draws charts: there is no `openpyxl.chart` use in the backend yet.
- **KPIs:** `services/finance/engine.py` `run_case` → `FinanceResult.metrics` (project/equity IRR and NPV pre/post tax, lifecycle NPV, payback, min/avg DSCR, LCOE), stored report via `services/finance/report.py` `load_ic_report`; LP economics `services/results/asset_economics.py` `compute_asset_economics` (capex/opex/revenue/LCOE per asset, `total` + `by_period`); routes `/results/economics_by_carrier`, `/cost_breakdown`, `/objective_decomposition`, `/lcoh` in `routers/results.py`; `services/economics.py` `annuitised_capex_by_carrier`.
- **Tabs and cards:** the 21 result tabs `TABS` in `frontend/src/pages/Results.tsx`, grouped by `CARD_GROUPS` in `frontend/src/pages/results/groups.ts`; backend view registry `services/result_views.py` (`list_result_views(card=…)`); workflow from the project's `eh_template.json` id and `services/flow_scope.py` `preset_for`.
- **Hand-off › Exports** is the one "Export…" chooser (owner decision SAVE-1; `.scratch/project-workflow/card-workspaces.md` §3.6). The workbook becomes one more choice there.
- **Dependency:** openpyxl ≥ 3.1.5 (`pixi.toml`). No new dependency.

## The workbook

1. **About:** project, run id and finish time, network/input fingerprint, template/workflow, cards included, money year and currency, units, `not_established` legend. A stale run (inputs changed since the solve) is refused, as `create_chart` does, unless the export is explicitly of the stored run.
2. **Scenarios (RW-SCEN-1, when more than one run exists):** the first sheet after About compares the base case with every sensitivity and experiment run: key KPIs (the Overview's, plus investment-case KPIs where a case exists), deltas against base, per period and over the horizon, with native charts. Reuse `services/experiments.py`, `harness/sensitivity_comparison.py`, `harness/experiment_comparison.py`, `services/compare/` and `run_evidence.compare_project_runs`.
3. **Overview (investment-grade KPIs):** one table per investment period and a horizon total column: total capex (overnight and annualised), fixed and variable opex, fuel, total system cost, energy served, system LCOE and per-carrier LCOE; then the investment-case block (NPV, IRR project/equity pre/post tax, payback, min/avg DSCR, financial LCOE, debt size) or its `not_established` row. Charts: capex vs opex stacked by period; LCOE by carrier by period; and, when the case exists, the yearly cash flow with cumulative NPV.
4. **Finance sheets** (when the investment case exists): reuse `finance/export_xlsx.py`'s section sheets and CashflowLines, with live NPV formulas through `workbook_view`.
5. **One sheet per included result view** (package 02): the view's full data over every snapshot and period (long tables go below a short key-results block), and 1–3 native charts of its key results, chosen per view in a table in the code (e.g. Capacity Expansion: optimal capacity by carrier by period; Dispatch: stacked generation by carrier per period; Prices: price duration curve; Emissions: by period; Curtailment, Lost load, Storage cycling, Adequacy, Load flow, Island, Grid-forming, FMEA, Grid code: their headline series). A view with no results for the run is listed in About as "no results", not given an empty sheet.
6. **Limits:** Excel's 1,048,576-row limit and the 25 MB export cap. An hourly multi-year table that would exceed them is written at the coarsest resolution that fits, and the sheet says which. Every text cell goes through the injection-safe writer.

## Not in scope

- Running the investment case or any solve on export.
- PDF/DOCX reports (the study report already has them).
- Pictures of the app's charts (RW-CHARTS-1).
