<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — One sheet and native charts per result view, over the whole horizon

Status: done
Type: task
Blocked by: 01
Gated: yes (results data; GK B, GK A for the adequacy, island and grid-forming views' numbers)
Model tier: opus
Size: L

Read [the spec](../spec.md) first, item 4 of "The workbook".

## What to build

Fill 01's sheet-builder registry for every result view the app's result tabs show (`frontend/src/pages/Results.tsx` `TABS`, `services/result_views.py`), except Overview/Summary/Investment case (01) and Asset Detail (the existing per-asset export covers it). Each sheet: a short key-results block, then the view's full data over every snapshot and investment period, then 1–3 native charts of its key results, chosen in one table in the code. Read each view's data from the same backend function its tab's route uses, so the workbook and the tab cannot disagree.

## Acceptance

- [x] Every view in the registry has a test on a fixture run: the sheet's key numbers equal its route's payload; its charts reference its own cells.
- [x] A view with no results for the run is listed in About as "no results", with no empty sheet.
- [x] A table over Excel's row limit or the 25 MB cap is written at the coarsest resolution that fits, and the sheet says which.
- [x] RW-SCEN-1: each view sheet carries a scenario column for every run; when that would exceed the row limit or the 25 MB cap, the base case is written in full and the other scenarios at key-results level, and the sheet says so.
- [x] Multi-period runs show every period; nothing is limited to the first period.

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

### 2026-10-29 — GK B (11): R5 plan verdict GO, with conditions

1. Scope: only this base's experiments. Include only records whose `project_id` equals the open base project's id and whose `owner_id` is the acting user. Test that an experiment of another project owned by the same user never appears, and neither does another user's experiment on the same shared project.
2. Baseline check against the open base.
   - When `base_basis["ok"]` is false, every experiment case gets `baseline_unavailable`.
   - When the preflight's base `input_fingerprint` differs from `base_basis["fingerprint"]`, the case gets `baseline_changed`.
   - When the record has a `baseline_run_id`, keep `_case`'s baseline evidence check in `_verify`.
3. One reason vocabulary. The Scenarios sheet already uses `sc._reasons`' names: `case_inputs_changed`, `baseline_changed`, `mode_differs`, `horizon_differs`, `solver_settings_differ` and `case_unsolved`. Use those for experiment cases instead of a new `comparison_basis_differs`; one sheet should not have two spellings for the same refusal. Add new names only for what has no equivalent (`evidence_tampered`, failed or not-run cases), and give them the same note or About treatment.
4. `compare_experiments` unchanged. `_case` must keep exactly today's exception set and its collapse to `experiment_source_unavailable`; an HTTPException from `resolve_project` inside `_load_case` is still caught. Prove it by copying master's `test_experiment_comparison.py` into the PR tree and running it there (review-gk byte-identity rule).
5. The lock is really released. `ex._LOCK` is an RLock, so leaving an inner `with` does not release it if a caller further up already holds it. The `_load_context` spy asserts `not ex._LOCK._is_owned()`. Show that the workbook route enters `gather_experiments` without holding `ex._LOCK`. Keep the plan's case mutated between verify and load.
6. Access before anything is read. For a case the user can't open, a spy shows that neither `_load_context` nor `run_evidence._load_run` is called and nothing in its directory is read. Asserting only that the name is absent is not enough.
7. Same filling as sensitivity runs. `_fill` applies the same `dispatch_ready`, `result_df_for` and `solved_settings_for` reads. A case whose dispatch the routes would not serve gets `case_unsolved` and no figures. Test it.
8. Cap. The cap is applied before any load. A case excluded after loading still holds its slot, so the cut is deterministic. Test that 6 sensitivity runs plus 4 experiment cases give 8 shown and "2 not shown", and that `_load_context` is never called for the 2 over-cap cases.
9. Unique run names. "<title>: <case id>" can repeat (two experiments with the same title) or match a sensitivity project's name. Make the names unique, for example by suffixing a short experiment id on a clash, and test it. Check that no view keys a dict by run name.
10. Mutation, one mutant per refusal, each killing a test:
    - skip the post-load fingerprint check;
    - skip the evidence digest check;
    - resolve after loading;
    - drop the `project_id` filter;
    - drop the owner filter.

At the trial merge, run `test_results_workbook*`, `test_experiment_comparison` and the routers/results tests. Keep the `routers/results.py` edit inside `_workbook_scenarios` because of #851. Send me the PR head when it's open.

### 2026-10-29 — GK B (11): R5 ruling on condition 5 (`_observe` loads under the lock): Option A

Option A, with three conditions. I read `_observe` (experiment_execution.py:155–200). It works on `copy.deepcopy(ledger)`, resolves access before it loads, and writes nothing, so running it outside the lock on the copy is sound. The post-load exact-fingerprint and evidence-digest checks still close the gap. Loading each case twice is accepted at a cap of 8.
- (a) No writes in that window. A test runs the gather and shows that each case's ledger file and record file are byte-identical before and after.
- (b) A concurrent change is caught. Change one case's ledger between the lock release and that case's load (for example, a cancel or refresh that rewrites it). That case must be excluded, or shown consistently with the ledger copy, never with figures from a network that differs from its receipt. Assert it.
- (c) The spy is strict. Condition 5's spy covers every `_load_context` and `_load_run` call made during the gather, including the ones inside `_observe`.

Condition 1, revised. `ex._read_record` already refuses a record whose owner or project differ (experiments.py:269), so don't add a redundant filter just to make a mutant killable. Keep the two exclusion tests: another project of the same user, and another user on a shared project. Replace the two filter mutants with one mutant that passes the wrong project or owner into `_read_record`, or skips it. It must fail one of those tests.

Condition 9: your answer is noted. The unique-name test still stands.

### 2026-10-29 — GK B (11): R5 second ruling (an experiment case's child is also a sensitivity run): Option A

GK B (11) on R5, second ruling (15:16Z, answering: each experiment case's child is also listed as a sensitivity run): Option A. Show each experiment case once, as an experiment case, checked against its receipt. Record this with the points below.

1. The marker. Use the strongest marker that exists.
   - If the experiment path writes anything identifying into the child's `sensitivity_case.json` or the registry row, such as an experiment id, use that.
   - Otherwise use an anchored full match of `_name`'s output: `exp-[0-9a-f]{32}-<case>` with the optional `-<n>` suffix. Build the regex next to `_name`, or derive it from `_name`, so the two can't drift. No loose `startswith("exp-")`.
2. No silent drop. A child the marker excludes from the sensitivity list that matches no case gathered for the acting user's own experiments on this base still gets counted in the Scenarios note and the About "not shown" row, and is never named. That covers other users' experiment children and a sensitivity case that happens to look like an experiment name.
3. Tests.
   - An experiment child never appears as a sensitivity run.
   - An experiment case of another user is counted, not named.
   - Condition 8 becomes 6 true sensitivity runs plus 4 experiment cases: 8 shown, 2 not shown.
   - Mutant: drop the marker filter in `_cases`. The never-listed-twice test must kill it.
4. Scope. The change stays in `scenarios._cases`. Leave `sensitivity_comparison._read` and the chat tool `compare_sensitivity` alone; if they also list experiment children, report that as a follow-up rather than widening R5.

### 2026-10-29 — R5 built (experiment cases as scenario runs), PR #919

`services/results_workbook/scenarios.py` (`gather_experiments`, `_experiment_run`, shared `_fill`); tests in `tests/test_results_workbook_experiments.py`.

1. Scope: `_read_record(root, id, project, owner)` is the filter: `test_another_users_or_another_projects_experiments_never_appear`.
2. Baseline: `test_a_case_of_a_changed_base_is_baseline_changed`, `test_without_current_base_results_every_case_is_baseline_unavailable`, `test_a_changed_recorded_baseline_run_is_baseline_changed`.
3. One vocabulary: `sc._REASONS` names, plus only `evidence_tampered`, `case_failed`, `case_not_completed`, `case_unavailable`: `test_failed_and_not_completed_cases_say_so_on_the_sheet_and_the_view_sheets`.
4. `compare_experiments` unchanged: master's `test_experiment_comparison.py` byte-identical to the tree's (`cmp`), 26 passed.
5. Lock (ruling: Option A): `test_every_case_load_is_outside_the_lock_and_nothing_is_written` (spies on `_load_context` and `_load_run` assert `not ex._LOCK._is_owned()`; the route enters unlocked); mutated between verify and load: `test_a_network_changed_after_the_ledger_copy_is_excluded`, `test_run_evidence_changed_after_the_receipt_is_evidence_tampered`.
6. Access first: `test_a_case_the_user_cannot_open_is_counted_and_nothing_of_it_is_read`.
7. Same filling: `test_a_case_whose_dispatch_the_routes_would_not_serve_is_unsolved`.
8. Cap: `test_six_sensitivity_runs_and_four_cases_show_eight_and_count_two`.
9. Unique names: `test_run_names_are_unique`.
10. Mutants, each applied by hand and killed (table in the PR body): post-load fingerprint skip, evidence digest skip, resolve after load, wrong owner, wrong project, raw JSON instead of `_read_record`, and (ruling 2) the marker filter dropped in `_cases`.

Ruling 2 (duplicates, Option A): `CASE_PROJECT_NAME` sits next to `_name`, `test_the_case_project_name_marker_is_exactly_the_engines_name`; a case's child is shown once, never as a sensitivity run; other children are counted, never named. A case whose ledger cannot be read is shown as `case_unavailable` and its child (matched by `_name`) is not also counted as another's: `test_a_case_whose_ledger_cannot_be_read_is_unavailable_and_its_child_not_counted`. An unreadable store gives a note, no case: `test_an_unreadable_experiment_store_is_said_and_names_no_case`.

Not built: experiments of other domains than network; `sensitivity_comparison._projects` still lists experiment children (follow-up, per ruling 2 point 4).

### 2026-10-29 — Done: R1 #896, R2 #899, R3 #900, R4 #907, R5 #919 merged

Acceptance by slice: every view's key figures equal its route and its charts read its own cells (R1–R3, conditions 2 and 9); "no results" vs a reported error (condition 5); the cell budget, period-safe coarsening and fallback (b) (R4); scenario columns for sensitivity runs (R1) and the acting user's experiment cases (R5); a period-2 figure on every view (condition 3). Follow-up #926 labels FMEA rows by LC-30's annualised basis.

Left open, logged: `sensitivity_comparison._projects` (`compare_sensitivities` with no case ids) still lists experiment case projects as sensitivity cases and counts them toward its cap; only network-domain experiments are gathered; an unreadable experiment store gives a note, not a 409; `CELL_BUDGET` is uncalibrated (MAX_BYTES is the backstop); `scenarios._cases`' return annotation still says `set[str]` (GK B nit on #919).
