<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — One sheet and native charts per result view, over the whole horizon

Status: ready-for-agent
Type: task
Blocked by: 01
Gated: yes (results data; GK B, GK A for the adequacy, island and grid-forming views' numbers)
Model tier: opus
Size: L

Read [the spec](../spec.md) first, item 4 of "The workbook".

## What to build

Fill 01's sheet-builder registry for every result view the app's result tabs show (`frontend/src/pages/Results.tsx` `TABS`, `services/result_views.py`), except Overview/Summary/Investment case (01) and Asset Detail (the existing per-asset export covers it). Each sheet: a short key-results block, then the view's full data over every snapshot and investment period, then 1–3 native charts of its key results, chosen in one table in the code. Read each view's data from the same backend function its tab's route uses, so the workbook and the tab cannot disagree.

## Acceptance

- [ ] Every view in the registry has a test on a fixture run: the sheet's key numbers equal its route's payload; its charts reference its own cells.
- [ ] A view with no results for the run is listed in About as "no results", with no empty sheet.
- [ ] A table over Excel's row limit or the 25 MB cap is written at the coarsest resolution that fits, and the sheet says which.
- [ ] RW-SCEN-1: each view sheet carries a scenario column for every run; when that would exceed the row limit or the 25 MB cap, the base case is written in full and the other scenarios at key-results level, and the sheet says so.
- [ ] Multi-period runs show every period; nothing is limited to the first period.

## Out of scope

The UI and chat tool (03).

## Comments

### 2026-10-29 — GK B plan verdict: GO, as separate PRs, with conditions

Slices, each its own PR with its own tests: **R1** network views (capex, dispatch, flows, loadshape, storage, curtailment, lostload, prices, economics, emissions) + scenario columns; **R2** loadflow + campus/gridspine views (grid_code, electrical_design, island, grid_forming), GK A co-reviews the numbers; **R3** adequacy + fmea, GK A co-reviews; **R4** the cell budget and coarsening; **R5** RW-SCEN-2 experiment runs.

1. Every lifted route body (dispatch/storage `_ts_payload`, `/adequacy`, the lost-load empty branch, loadflow's optional n/state) changes nothing: master's route tests run green unchanged, and the JSON is byte-equal to master's on the existing fixtures.
2. Each key figure equals the route payload; each scenario column comes from that run's own network/cfg/result_df (a builder fed the base's network for every run must fail a test).
3. A period-2 figure is asserted on every view; no long table is first-period-only.
4. (R4) Coarsening with hand values and a non-unit weighting: MW = weighted mean; energy = Σ p·w (an unweighted sum must fail); no bucket crosses a period (a day straddling the boundary); the sheet names the resolution; fallback (b) tested.
5. A builder returns False only when the route would be empty or not-run; an exception is a reported error in About, never a silent "no results". One test of each.
6. (R3) Until ticket 30 lands, fmea's `criticality_eur_per_year` and `severity_eur` are not presented as per year: omitted, or labelled "per study horizon (ticket 30)", in any chart or ranking too. Test the label.
7. (R5) Reuse `experiment_comparison._case`. Tests: tampered evidence digest excluded with its reason; different basis gives `comparison_basis_differs`; a case project the user can't open is counted, not named; a failed or not-yet-completed case is excluded; a case whose network or context changed after the receipt is excluded. Don't hold `experiments._LOCK` while loading case networks: verify under the lock, release, load, then check the loaded network's input fingerprint equals the verified one. MAX_CASES is shared; the excess is counted.
8. Access first: the campus/gridspine views and every scenario/case run call `resolve_project` before reading any file of that project.
9. Charts read only number cells (or formulas over them) on their own sheet; every view covered.
10. Per PR: `test_route_chat_disposition`, `test_project_route_acl_policy`, `test_packaging_requirements` when imports change; a release fragment; these conditions recorded here.

Accepted not-done: overnight capex by period and fuel stay `not_established`; a campus-study scenario's IC key leaves out the extra_assets digest; the cell budget is a heuristic with `MAX_BYTES` as the backstop.

### 2026-10-29 — GK B: condition 5 ruling, and GK OK on R1

GK OK @14f6bee94c7d3a50352348b2c3575559a4933eac on R1 (#896). Condition 5, ruling A: R1 leaves `compute_prices`, `compute_curtailment` and `compute_cost_breakdown` swallowing exceptions as master does. Follow-up: [04](04-result-services-raise.md).

### 2026-10-29 — R2 built (Load Flow, Grid code & connection, Electrical design, Island, Grid-forming)

`services/results_workbook/views_grid.py`; tests in `tests/test_results_workbook_views_grid.py`.

1. `loadflow_summary.get_loadflow_view` / `_loadflow` take an optional `context` (a run's saved context: its network, solver state, user series and project id), threaded to the helpers as `state`; omitted, every read is the active one as before. Tests: `test_loadflow_route_json_is_unchanged_and_a_context_read_of_the_active_run_equals_it`; master's `test_loadflow_summary`, `test_loadflow_navigation`, `test_results_source_navigation` unchanged and green; a one-off check against master's module gave byte-equal JSON on the `test_loadflow_summary` fixture (lopf, windows, ac_pf). Departure: `context` instead of separate `n`/`state`, because the exact-input fingerprint the route checks needs the run's user series too.
2. Keys equal the route's function (`get_loadflow_view`, `ce.get_state`, `gs.get_capacity`/`get_connection`, `ce.get_island` + `island_report`, `cit.get_settings`); scenario columns differ from the base and match their own run: `test_loadflow_keys_equal_the_route_and_each_scenario_reads_its_own_context`, `test_project_views_read_each_runs_own_project_after_resolving_it`, `test_grid_code_keys_equal_the_campus_payload`.
3. Load Flow: every period is the tab's window over that period (`2040` asserted); Electrical design: cost per period (`2040` asserted, `test_electrical_design_costs_per_period_with_the_second_period`). Grid code, island and grid-forming have no period split and say so in the sheet note (`NO_PERIODS`).
5. False only when the tab would be empty: load flow `not_solved`/`no_ac_branches`, no campus results, a study project for Electrical design/island/grid-forming, island report with no table beyond its inputs, GFM `not_run`, gridspine 404 before a screened run. Tests: `test_a_loadflow_the_tab_cannot_show_is_no_results`, `test_grid_forming_not_run_is_no_results`, `test_a_campus_study_not_run_is_no_results`, `test_a_failing_project_view_is_an_error_in_about_not_no_results`, `test_a_planning_study_before_its_screen_is_no_results_but_other_errors_raise`.
8. `WorkbookInputs.project_for(run)` (the route's `_workbook_project`: `resolve_project` for the acting user, None when refused) is called per run before any project file is read; the route now keeps the acting user through the build. Scenario runs keep their saved context (`Run.context`), loaded by `gather_scenarios` after its `resolve_project`. Tests: `test_a_run_the_user_cannot_open_reads_nothing_of_its_project`, `test_the_routes_project_reader_resolves_access_first`, `test_without_project_access_the_project_views_have_no_data`.
9. `CHARTS` entries for the five views; `test_every_chart_reads_number_cells_on_its_own_sheet` covers each (grid code both hub and study).
10. `test_route_chat_disposition`, `test_project_route_acl_policy` green; no new third-party imports. Fragment `doc/release-notes.d/claude-results-workbook-02-loadflow.md`.

Not in the sheets: the campus harmonic scan and voltage-stability blocks (nested; the sheet note points to the tab), and the hub cost comparison.

### 2026-10-29 — R3 built (Adequacy, FMEA)

`services/results_workbook/views_adequacy.py`; tests in `tests/test_results_workbook_views_adequacy.py`.

1. The `/adequacy` body is now `adequacy.report.adequacy_payload(state, solved_settings)`; the route passes `_state` and `_solved_settings`, so its reads are the same. Departure: both are required arguments, not defaulted to the active ones, because `_solved_settings` lives in the router and services never import routers. Tests: `test_adequacy_route_json_equals_the_lifted_function_in_every_state` (204, `configured: false`, report); master's `test_adequacy_http`, `test_result_configured_fields`, `test_chat_adequacy_tools` unchanged and green; a one-off check against master's route body gave byte-equal rendered JSON in all four states (nothing solved, settings only, report, legacy stash). FMEA already read `copt_endpoint.build_fmea_modes_payload`, unchanged: `test_fmea_route_json_is_the_function_the_sheet_reads`.
2. Each scenario column reads its own saved context's solver state (Adequacy) and its own network and config (FMEA); the base reads the active ones: `test_adequacy_keys_equal_the_route_per_period_and_each_scenario_reads_its_own_state`, `test_fmea_keys_equal_the_route_and_each_scenario_reads_its_own_network`.
3. Adequacy: ENS cap and achieved ENS per period (`2040` asserted) and the horizon (their sum, as the report's system figure); MW and the margin per period only, never summed. FMEA has no period split: `NO_PERIODS` in the note, asserted, and every run's rows listed.
5. False only on the route's 204 (nothing solved; no failure mode from any source); solved without a target writes a "not modelled" sheet, as the tab shows: `test_nothing_solved_and_no_failure_mode_are_no_results`, `test_adequacy_solved_without_a_target_writes_not_modelled`, `test_a_failing_read_is_an_error_in_about_not_no_results[adequacy|fmea]`.
6. FMEA (after GK A's NOT OK @7040b6d1, fixed in 44951e0c): each row's criticality and severity sit in the columns of their own basis, with "criticality basis" and "severity basis" columns. The basis is the row's `criticality_basis` (ticket 30) when present, else by class: A/B "per study horizon (ticket 30)", C per year (frequency × per-event severity). "Highest criticality" is one key per family (A/B, C), None when a family mixes bases. There is one criticality chart per basis, and a mixed column is never charted. Both sheets note the horizon length. Tests: `test_each_fmea_row_is_labelled_by_its_own_basis[4]`, `test_mixed_bases_get_one_highest_key_each_and_no_chart_mixes_them`, `test_a_family_on_two_bases_has_no_highest`.
8. Neither view reads a project's files; scenario runs come from `gather_scenarios`, which resolves each project first.
9. `CHARTS` entries for both views; `test_every_chart_reads_number_cells_on_its_own_sheet[adequacy|fmea]`.
10. `test_route_chat_disposition`, `test_project_route_acl_policy` green; `test_packaging_requirements` green but for the known defusedxml failure; no new third-party imports. Fragment `doc/release-notes.d/claude-results-workbook-02-adequacy.md`.

Not in the sheets: the Adequacy tab's other study surfaces (COPT screening, frontier, Monte Carlo, planning loops) and the FMEA worksheet's expert class-D rows and overlays (per-project sidecar; the FMEA note points to the tab).

### 2026-10-29 — GK B (10): R4 plan verdict GO, with conditions (GK A co-reviews the numbers)

R4 adds `Table.time` ("mean" | "sum") on the snapshot tables and a resolution ladder: snapshot → day → week → month, bucketed per (period, bucket). The budget is estimated rows and cells, with MAX_BYTES as the backstop; when it is exceeded, fallback (b) applies. Prices are the time-weighted mean, not load-weighted, and the note says so.

1. Means and sums weight by `snapshot_weights(n, "generators")` (hours), never `objective`. A hand test with objective = generators × 10 must fail a builder that uses objective.
2. Gaps: a NaN cell drops out of both Σv·w and Σw for its column. An all-NaN or Σw = 0 bucket is written blank, never 0. Test both.
3. Non-datetime snapshots: fixed-count blocks named as such ("blocks of 24 snapshots"), or fallback (b). Never a crash or a wrong bucket. Test one.
4. One resolution per sheet for every scenario column, named once in the note; About lists each sheet's resolution.
5. State of charge is labelled "weighted mean of the state", never energy.
6. Keys always come from full-resolution data. Charts read only the coarsened number cells on their own sheet (condition 9, re-run on a coarsened workbook).
7. If MAX_BYTES is still hit after fallback (b), About reports an error and no truncated sheet is written. Test with a tiny monkeypatched MAX_BYTES.
8. Mutations that must each fail a named test: an unweighted mean; the period key dropped from the bucket (the straddling day becomes one row); the NaN rule dropped.
9. Stacked on #900: trial-merge with master, and run every test_results_workbook* file plus the route-policy tests (condition 10).
