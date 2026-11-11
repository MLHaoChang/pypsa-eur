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
- GK B (44), S3 plan GO with conditions, 11 Nov 2026. One route; the base is read only on `?compare=1`, asked after the strip paints. S1: a base or child the user cannot open, one in another workspace, a deleted one and no project at all give the same answer, with no name or id. S2: the acting user is restored on every exit. S3: reading the base writes nothing and does not make it the open project. S4: the base read takes the base context's own lock and the shared NetCDF read lock while it loads; a busy card costs that card's delta. S5: only `ready` and `zero` cards are subtracted, and the base passes the same gate. S6: currency, money year, discount rate, snapshots, weightings and investment periods are in the fingerprints `sensitivity_comparison._reasons` compares. S7: a non-finite or missing figure gives no number; a zero base keeps the value and has no percentage; nothing is rounded on the server. S8: `comparison.basis` says what a change is a change of, and the not-via-chat sentence says chat compares on other measures. S9: the trend says its basis (undiscounted, each period over its years) and is null for a partial series. S10: a plain project's answer is S2's with the new keys null. S11: trial-merge checks and mutants before the OK.
- UX (17), S3, D1 to D5: the change line uses the tile's own number format and U+2212; the not-compared reason is plain words; an out-of-date strip dims the change and the trend under the one mark; a multi-period placeholder has the taller height; the trend's visible text and its label give the same two figures.
- Found while taking the S3 shots: `07-sensitivity-case-loses-its-project-type.md`.
- S3, `mp-code-review` (session …5JAjteTEaAK66UhL): after a new solve the strip no longer keeps the old comparison beside the new figures; any failure of the base read gives the not-compared sentence, where an unlisted one was a 500; a change that rounds to nothing reads "No change". Found while timing: `08-long-horizon-never-compares-with-its-base.md`.

