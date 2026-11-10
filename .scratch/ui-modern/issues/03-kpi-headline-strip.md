<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# KPI headline strip per project type

Status: done
Blocked by: 01
Type: feature (first slice of DASHBOARD-1)
Model: Sonnet. Reviewer: UX.

## What to build

A row of headline numbers at the top of Results for each project type, for example LCOE, CAPEX, curtailment and, for data centres, time to power. Each card shows its unit, the delta against the base case, a small trend line, the source run, and an "out of date" mark when its inputs changed. Values come from the run's own checked facts; nothing is recomputed in the frontend.

## Plan (UX GO, 10 Nov 2026, conditions K1–K8)

The strip sits at the top of Results › Summary, in Guided and Expert. One PR per slice (PR-SIZE-1):

- S1 (backend): `GET /api/results/headline_kpis`, the ordered cards for the project's type. Energy hubs and data centres: LCOE, system cost, new-build capital cost, curtailed share, unserved energy. Microgrids: unserved energy first. Plain networks: system cost, new-build capital cost, curtailed share, peak line loading.
- S2 (UI): the strip on that route, with its source line, the "Out of date" mark, loading, failed and empty states; "per year" in place of "(annualised)"; the Results header says "solved" and "all periods".
- S3 (UI): the delta against the base case, and a trend line over the investment periods of a multi-period run.
- Time to power: no run computes it, so it has no card (owner decision TTP-1). The engine task is `.scratch/datacenter-grid-readiness/issues/27-time-to-power-fact.md`; the card follows in `03b-time-to-power-card.md`.

## Comments

- GK B (44), PR #1343 @88c90b13, condition C1 for slice 2: the `not_via_chat` entry for the strip must not attach to a goal that puts the figures elsewhere. Slice 2 adds excel, word, csv, pdf, workbook, document, bundle, exported, save, download and compared to `_STRIP_ELSEWHERE`, with GK B's five goals in `test_ordinary_goals_get_no_refusal`. Done in slice 2 (one goal per word as well).
- S3 (session …4zMm464JdjviPVCe): the delta is the change in the card's own figure. `harness/sensitivity_comparison` decides who the base is and whether the case compares (`_read`, `_reasons`); its metrics are on another basis than the cards (pro-rated CAPEX + OPEX, all annuitised CAPEX, curtailed GWh, no LCOE), so the base's cards are built by `headline_kpis` on the base's saved context and subtracted (`services/results/headline_delta.py`). Only Total system cost has a figure per investment period (`compute_cost_breakdown` `by_period`), so only that tile draws a trend; the checked formulas refuse a multi-period run, and lost load and new-build capital are horizon-wide.
