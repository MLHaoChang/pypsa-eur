<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Feature catalogue: what a human does, and what the copilot can do

Status: ready-for-agent (documents only; no application code changed, no tests run)
Date: 2026-10-19, Europe/Berlin
Source: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Method: read from code, not from prose. Frontend routes, panels, tabs and
action handlers under `pypsa-gui/frontend/src/`. 442 router endpoints under
`pypsa-gui/backend/routers/`. The 256 harness tools in
`harness/catalogue.py` (checked by `smoke/assistant_inventory.py --check`:
256 tools, 15 result tabs, 19 panels).

The owner said OJ-01–12 were only examples. The copilot must cover everything
a human does in the app, through conversation. This file is the inventory
behind that claim. [Owner journeys](owner-journeys.md) OJ-13–46 turn it into
acceptance scenarios, and issues 15–22 hold the gaps.

## How to read the table

- **Copilot today** names the chat tool(s) that can do it on master, or
  `none`. "Partial" says what is missing. A tool's existence is not proof the
  journey works: no journey runs end to end yet.
- **Layer** is the LC layer of the [plan](../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md)
  that closes it, with the ticket when one exists. "Q5" and "Q6" are open
  owner questions. "Out" means the copilot refuses it by design (LC-22).
- **Card** is the confirmation the copilot must show. Under owner decision
  Q3, every parameter change gets an old→new card in both modes, even when
  its tool is write tier (Expert cards only destructive and execution tiers
  today, `harness/confirm.py:28-43`).

## Headline findings

1. **Tools are broad; journeys are missing.** All seven core features have
   tools on master except a post-solve plausibility check and a report for a
   plain network study. What is missing is conversation glue, cards in
   Expert, and tests.
2. **Plausibility after a solve has no native check.** `validate_network`,
   `diagnose_network`, `project_readiness` and `review_eh_study` check inputs
   and Energy Hub designs. `sanity_check_results`, `diagnose_results` and
   `generate_run_report` were registered once, never implemented, and removed
   (`services/chat_tools.py:7332-7341`; guard `smoke/qa_e2e.py:254`). LC-20,
   owner question Q5.
3. **Reports exist only for some studies.** `POST /reports/generate`
   answers 400 `no_evidence` unless an Energy Hub, adequacy, FMEA, investment
   or island source exists (`routers/report_jobs.py:154-161`). A grid study
   (dispatch, capacity, load flow, sensitivities) cannot be reported by a
   human or by chat. LC-21, owner question Q6.
4. **Sensitivities are created and run, but compared only in pairs.**
   `run_sensitivity_sweep` (1–4 cases, up to 20 multi-asset changes each) and
   the experiment tools exist. Every comparison tool is A-vs-B, so "compare
   all with the base" needs a base+N read (LC-17, LC-18).
5. **Several assets at once has no single Expert card.**
   `bulk_update_components` writes one class with no preview.
   `preview_project_changes` previews mixed classes, but only over an
   allowlist of about 27 attributes. Q3 needs one card for any mix (LC-16).
6. **Project summary is thin.** `get_project_statistics` returns bus,
   generator, line and snapshot counts only; loads, storage, transformers and
   capacities need many calls (LC-15).
7. **Re-run and queue control are half there.** The requeue and
   cancel-queued routes have no chat tool (LC-19).
8. **Some human features must stay out of chat:** admin, account,
   secrets and instance-wide queue control. They need named refusals
   (LC-22).

## Coverage summary

| Core feature (owner) | Copilot today | Missing | Layer · tickets | Journeys |
| --- | --- | --- | --- | --- |
| 1 Open project, summarise assets | `list_projects`, `load_project`/`activate_project`, `get_project_statistics` (thin), `list_components`, `get_meta` | One bounded asset summary; exact names (LC-01B) | LC-01 · LC-15 | OJ-09, 13, 14, 45 |
| 2 Navigate results with numbers | `ui_open_panel` (15 tabs), `get_results` (31 kinds), `get_loadflow_summary`, `get_adequacy_results`, `get_asset_results`, `explain_investment`, `list_project_runs`, `compare_project_runs` | Per-tab read rule, narration, source ACKs outside load flow, snapshot/asset navigation fixes | LC-03 · LC-09, 11, 14 | OJ-01–04, 15–17 |
| 3 Plausibility | Pre-solve: `validate_network`, `diagnose_network`, `project_readiness`; Energy Hub: `review_eh_study` | Any post-solve rule set | Q5 · LC-20 | OJ-18, 19, 20 |
| 4 Update any asset, several at once | `update_component`, `bulk_update_components`, `preview_project_changes` → `apply_project_changes`, `get_component_schema`, `undo_last` | One old→new card for mixed sets in both modes; relative changes; settings cards | LC-04 · LC-16 | OJ-05, 06, 21–26 |
| 5 Sensitivity: create, run, compare | `run_sensitivity_sweep`, `create_scenario`, experiment tools, `compare_scenarios`, `compare_experiments`, `get_study_evidence(simulation)` | Conversation flow, N-job continuation, base+N comparison, ranking | LC-05 · LC-17, 18 | OJ-27–30 |
| 6 Run, re-run, follow, failure | `run_simulation`, `run_ac_pf_stage`, `solve_queue_*`, `abort_simulation`, `wait_for_job` (≤ 25 s), `get_simulation_log_history` | Continuation after 25 s (LC-12), requeue/cancel-queued tools, failure triage, engine choice | LC-05 · LC-12, 19 | OJ-07, 08, 10, 31–34 |
| 7 Create a report | `generate_report` & report tools (Energy Hub, adequacy, investment, island), `generate_study_report` (decision), exports | Report for a plain network study and sensitivities | Q6 · LC-21 | OJ-35, 36, 37 |
| All, end to end | — | — | LC-08 | OJ-38 |

## Inventory

### Projects and project lifecycle

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Browse projects | Sees all accessible projects on Projects home | `list_projects` | LC-01 | OJ-09 |
| Open a project | Opens a card; it loads into the workbench | `load_project`, `activate_project`; exact names in flight (LC-01B) | LC-01 | OJ-09, 13 |
| Summarise a project | Reads Project info / Overview KPIs | Partial: `get_project_statistics` (4 counts), `get_meta`, `list_components` | LC-01 · LC-15 | OJ-13, 14 |
| Peek at another project | Compare panel or tab strip | `get_project_statistics`, `get_project_network_meta`, `list_project_network_component` | LC-01 · LC-15 | OJ-14 |
| New blank project | Wizard, blank tab | None (workflow sends the user to the wizard) | LC-06 (D01) | — |
| New from template (10 templates) | Wizard, template tab | `create_project_from_template`, `get_eh_template` | LC-06 (D01) | OJ-39 |
| New from file | Wizard, file tab | `import_uploaded_network`, `import_*` tools | LC-06 (D20) | OJ-41 |
| Clone / save a copy | Wizard clone tab; sidebar Duplicate | `save_project_a_copy`, `save_project_as` | LC-06 (D01) | OJ-40 |
| Save | Header Save | `save_project` | LC-06 (D01) | OJ-27, 40 |
| Rename | Header or Overview | `rename_project` | LC-06 (D01) | OJ-40 |
| Delete project | Scenarios tree | `delete_project` | LC-06 (D01) | — |
| Checkpoints (save, restore, delete, diff) | Snapshots panel | `create_project_snapshot`, `list_project_snapshots`, `restore_project_snapshot`, `delete_project_snapshot`; diff: none | LC-06 (D22) | OJ-40 |
| Import/export project bundle | Sidebar | `download_project_bundle`, `import_project_bundle` | LC-06 (D20) | OJ-37 |
| Claim unclaimed or legacy project | Projects home; Admin | None | Out (LC-22) | — |
| Project members (share) | Workspace panel | None | Out for now (LC-22, owner later) | — |
| Edit lock | Lock banner | Locks are enforced on writes; no tool | Not applicable | — |
| Crash recovery | Banner | None | Not applicable | — |
| Project tabs, Guided/Expert switch, theme | Shell | None (client state); the copilot keeps the current mode | Not applicable | OJ-12, 39 |

### Model: topology and asset parameters

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Read any component | Properties panel; data grid | `list_components`, `get_component`, `get_component_schema` | LC-01 (LC-01A merged) | OJ-13 |
| Edit one asset's fields | Properties panel | `update_component` (write: no Expert card) | LC-04 | OJ-05, 06 |
| Edit several assets | Data grid multi-select and bulk apply | `bulk_update_components` (one class, no preview); `preview_project_changes` → `apply_project_changes` (allowlist) | LC-04 · LC-16 | OJ-07, 21–23 |
| Create an element | Palette drag; creation form | `create_component`, `batch_create_components`, `create_carrier` | LC-06 (D02) | OJ-24 |
| Delete element; cascade bus | Properties; canvas | `delete_component`, `batch_delete_components`, `cascade_delete_bus` | LC-06 (D02) | — |
| Carriers and shunts | Carrier / shunt panels | `update_component` (Carrier, ShuntImpedance) | LC-04 | — |
| Global constraints (CO2 cap, fuel limits) | Solver settings, constraints tab | `list_global_constraints`, `create_component`/`update_component` (GlobalConstraint) | LC-04 | — |
| Asset provenance | Provenance chip | `get_component_schema` (metadata); value provenance: none | LC-04 | — |
| Line lengths from geometry; rescale impedances | Rescale dialog | `recalculate_line_lengths`; from-geometry and rescale: none | LC-06 (D02) | — |
| Clustering | Solver settings, network tab | `cluster_network` | LC-06 (D02) | — |
| Undo | Header Undo | `undo_status`, `undo_last` | LC-04 | OJ-26 |
| Change history | Bottom panel History | `audit_log`, `clear_audit_log` | LC-06 (D22) | — |
| Network meta | Overview | `get_meta`, `update_meta` (write: no Expert card) | LC-04 · LC-16 | — |
| Reset network | Import page; palette | None | Out (LC-22) | — |

### Canvas, map and site

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Select a component | Click on canvas | `ui_select_component` (slide panel can hide it, LC-14 item 2) | LC-03 · LC-14 | OJ-05 |
| Open the edit card | Double-click | None (LC-14 item 3) | LC-03 · LC-14 | OJ-05 |
| Topology layout | Drag buses | `get_project_layout`, `update_project_layout` | LC-06 (D24) | — |
| Map placement, OSM suggestions, address search | Map canvas | None; `hybrid`/`site` views unreachable (LC-14 item 6) | LC-22 (read-only nav) | — |
| Geometry import | Map dialog | None | LC-22 | — |
| Sites and 3D view | Map, Site canvas | None | LC-22 (read-only nav) | — |

### Time axis and profiles

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Snapshot window | Model horizon | `list_snapshots`, `set_snapshots` | LC-06 (D03) | — |
| Representative weeks | Model horizon | `sample_representative_weeks` | LC-06 (D03) | — |
| Snapshot weights (incl. CSV) | Model horizon | `set_snapshot_weightings`, `download_snapshot_weightings_csv`, `upload_snapshot_weightings_csv` | LC-06 (D03) | — |
| Investment periods and weightings | Model horizon | `list_investment_periods`, `set_investment_periods`, `set_investment_period_weightings`, `set_multi_period_snapshots` | LC-06 (D05) | — |
| Capacity bounds per period | Capacity bounds editor | `list_vintage_bounds`, `set_vintage_bounds`, `delete_vintage_bounds`, `cleanup_orphan_vintages` | LC-06 (D05) | — |
| Profiles: list, view, upload, template, delete | Time series manager | `list_timeseries_profiles`, `get_timeseries`, `list_all_timeseries`, `upload_*_profile`, `upload_timeseries`, `download_timeseries_template`, `delete_timeseries`, `generate_exemplary_timeseries` | LC-06 (D03) | OJ-25 |
| Scale or edit a profile | Re-upload | Partial: no scale operation | LC-06 (D03) | OJ-25 |
| Snapshot picker | Header picker | `ui_set_snapshot` (does not set the index, LC-14 item 1) | LC-03 · LC-14 | OJ-17 |
| Aggregate load | Time series chart | `get_aggregate_load` | LC-03 | — |

### Economics, solver and run settings

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Economic basis (rates, money year) | Economics panel | `update_solver_config` (write: no Expert card) | LC-04 · LC-16 | — |
| Asset costs table | Economics panel | `get_asset_costs` | LC-03 | OJ-06 |
| Tariffs, contracts, library | Economics; Investment tab | `list_library_items`, `get_library_item`, `import_urdb_tariff`, `attach_tariff`, `set_site_connection`, `define_participants`; series/meter upload: none | LC-06 (D18) | OJ-42 |
| Solver, foresight, MIP gap, threads | Solver settings | `get_solver_config`, `update_solver_config`, `get_solver_capabilities`, `check_solver_availability` | LC-06 (D06) · LC-16 card | OJ-44 |
| N-1 and AC power flow settings | Solver settings | `update_solver_config`; `run_ac_pf_stage` | LC-06 (D07) | OJ-31 |
| CO2 price, VoLL, reserve margin | Solver settings | `update_solver_config` | LC-04 · LC-16 card | — |
| Preflight and Issues panel | Issues | `validate_network`, `diagnose_network`, `project_readiness` | LC-05 · LC-19 | OJ-20 |

### Runs and jobs

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Run (header) | Run button | `run_simulation`, `solve_queue_enqueue` (execution card) | LC-05 | OJ-07, 31 |
| AC power flow stage | Solver settings | `run_ac_pf_stage` | LC-05 · LC-19 | OJ-31 |
| Follow progress, log | Solve queue; Log tab | `get_simulation_status`, `get_simulation_log_history`, `solve_queue_list`, `wait_for_job` (≤ 25 s) | LC-05 · LC-12 | OJ-08 |
| Conversation continues when the job ends | (human watches) | None | LC-05 · LC-12 | OJ-07, 27 |
| Abort running job | Run button; queue row | `abort_simulation`, `solve_queue_abort`, `force_reset_simulation` | LC-05 | OJ-08 |
| Requeue / dismiss a job | Queue row | None (routes exist) | LC-05 · LC-19 | OJ-34 |
| Cancel all my queued jobs | Queue panel | None (route exists) | LC-05 · LC-19 | OJ-34 |
| Pause/resume queue; clear finished | Queue panel (super-admin) | `solve_queue_clear_finished` exists; pause/resume: none | Out (LC-22) | — |
| Explain a failed solve | Read the log | `get_simulation_log_history` + solver-error prompt | LC-05 · LC-19 | OJ-33 |
| Durable multi-step plan | — | `start_task`, `get_task`, `resume_task`, `cancel_task`, `resolve_task_step`, `set_goal` | LC-05 | OJ-07 |

### Results

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Summary tab | Reads headline KPIs | `get_project_results_summary`, `get_results(cost_breakdown, statistics)` | LC-03 · LC-11 | OJ-15 |
| Overview (multi-period) | Per-period mix | `get_results` kinds, `get_vintage_results` | LC-03 · LC-11 | OJ-15 |
| Capacity expansion | Sized assets, CAPEX | `get_results(cost_breakdown, economics_by_carrier)`, `explain_investment` | LC-03 · LC-11 | OJ-15 |
| Dispatch | Per-snapshot generation and storage | `get_results(generators, storage_dispatch, loads, links)` | LC-03 · LC-11 | OJ-15 |
| Load flow | Line loading, voltages, losses | `get_loadflow_summary` (current only), `get_results(lines, voltages, losses, line_duals, line_reactive)` | LC-03 · LC-09 | OJ-01 |
| Prices | Price curves and drivers | `get_results(prices, price_drivers)` | LC-03 · LC-11 | OJ-15 |
| Economics | Per-asset revenue, LCOE | `get_results(asset_economics, lcoh)` | LC-03 · LC-11 | OJ-15 |
| Emissions | CO2 totals, cap dual | `get_results(emissions)` | LC-03 · LC-11 | OJ-15 |
| Curtailment | Curtailed energy | `get_results(curtailment)` | LC-03 · LC-11 | OJ-15, 19 |
| Lost load | Unserved energy | `get_results(lost_load)` | LC-03 · LC-11 | OJ-19 |
| Storage cycling | Cycles, heatmap | `get_results(storage, storage_dispatch)` | LC-03 · LC-11 | OJ-15 |
| Asset detail | One asset, every series | `get_asset_results`, `ui_open_asset_detail`, `export_asset_results` | LC-06 (D08) | OJ-04, 17 |
| Run-bound view; run history | Results for a past run | `list_project_runs`, `get_run_evidence`, `discuss_project_run`, `export_run_evidence` | LC-03 | OJ-01, 16 |
| Compare two runs | — | `compare_project_runs` | LC-03 | OJ-16 |
| Charts | Chart export | `create_chart` | LC-06 (D20) | — |
| Plausibility of results | Engineer's judgement | None after a solve | Q5 · LC-20 | OJ-18, 19 |

### Reliability, Energy Hub, investment

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Adequacy, COPT, reserve margin | Adequacy tab | `get_adequacy_results` | LC-06 (D11, D13) | OJ-12 |
| Frontier, MC, coupling and margin loops | Adequacy panels | `run_frontier_study`, `run_mc_study`, `run_coupling_loop`, `run_margin_loop`, `abort_adequacy_study`, `start_campaign`/`campaign_status`/`end_campaign` | LC-06 (D12, D13) | OJ-43 |
| Stress scenarios | Stress editor | `get_stress_scenarios`, `put_stress_scenarios` | LC-06 (D13) | — |
| FMEA | FMEA tab | `get_fmea_worksheet`, `run_fmea_sweep` | LC-06 (D11) | — |
| Asset health | Worksheet | `get_asset_health`, `record_asset_health` | LC-06 (D11) | — |
| Energy Hub study | Hub panels | `suggest_eh_setup`, `review_eh_study`, `run_eh_study` | LC-06 (D13) | OJ-39 |
| Investment case | Investment tab | `run_investment_case`, `get_investment_case`, `solve_ppa_price`, `explain_cashflow`, `explain_investment` | LC-06 (D18) | — |
| Participants, value flows | Investment tab | `define_participants`; value-flow designer: none | LC-06 (D18) | — |

### Studies

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Decision study (11 views) | Decision panel | `start_decision_study`, `set_study_parameter`, `run_decision_study`, `get_study_findings`, `explain_verdict`, `generate_study_report`; tornado: none | LC-06 (D18) | OJ-42 |
| Campus electrical: draft, run, MILP | Campus panel | `campus_get_study`, `campus_draft_campus`, `campus_run_study`, `campus_get_milp`, `campus_get_investment` | LC-06 (D14) | — |
| Island study, cuts, GFM | Campus sections | `campus_island_config`, `campus_set_island_config`, `campus_run_island_study`, `campus_island_cuts`, `campus_apply_island_cuts`, `campus_island_gfm_settings`, `campus_recommend_gfm_settings`, `campus_apply_gfm_settings` | LC-06 (D15–D17) · LC-14 item 5 | OJ-11 |
| Campus libraries, grid codes | Campus sections | `campus_get_library`, `campus_set_library`, `campus_list_grid_codes`, `campus_extract_grid_code`; upload/publish: none | LC-06 (D14) · LC-22 | — |
| GridSpine pipeline | GridSpine panel | 16 `gridspine_*` tools and `get_study_evidence` | LC-06 (D14) · LC-14 item 5 | — |

### Sensitivities and comparison

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Create a scenario (branch) | Scenarios tree | `create_scenario` (destructive), `list_scenarios` | LC-05 · LC-17 | OJ-27 |
| Sensitivity sweep (create + queue) | Scenarios tree, solve subtree | `run_sensitivity_sweep` (1–4 cases) | LC-05 · LC-17 | OJ-27, 28 |
| Typed experiment with receipts | — | `start_experiment` … `reconcile_experiment` (≤ 2 cases executed) | LC-05 · LC-17 | OJ-27 |
| Compare A vs B | Compare view, 10 tabs; Results compare rail | `compare_scenarios` (no `loading` focus), `get_project_compare_state`, `ui_open_panel(compare…)` | LC-05 · LC-18 | OJ-29 |
| Compare base with N cases | (one pair at a time) | None | LC-05 · LC-18 | OJ-29, 30 |
| Export a comparison | — | `export_experiment_comparison` | LC-05 · LC-18 | OJ-29 |

### Reports, exports, imports

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Generate report (Energy Hub, adequacy, investment, island) | Reports panel | `generate_report`, `get_report_status`, `abort_report_generation`, `build_study_report` | LC-06 (D21) | OJ-36 |
| Report for a network study | Not possible today | None (400 `no_evidence`) | Q6 · LC-21 | OJ-35 |
| Evidence-only report | Reports panel | None (route exists, no tool) | LC-06 (D21) | — |
| View, regenerate section, diff versions | Report viewer | `list_reports`, `get_report`, `get_report_table`, `regenerate_report_section`, `diff_report_versions` | LC-06 (D21) | — |
| Templates, mapping, round trip | Report viewer | `list_report_templates`, `set_report_template`, `get_report_template`, `propose_report_mapping`, `set_report_mapping`, `import_edited_report`, `list_report_roundtrips` | LC-06 (D21) | — |
| Export DOCX / PDF | Report viewer | `export_report_docx`, `export_report_pdf`, `export_eh_report_docx` | LC-06 (D21) | OJ-36 |
| Decision report (DOCX, XLSX, HTML) | Decision report step | `generate_study_report` | LC-06 (D18) | OJ-42 |
| Network export (nc, Excel, CSV, MATPOWER) | Import/export modal | `export_network_nc`, `export_excel`, `export_csv_bundle`, `export_matpower` | LC-06 (D20) | OJ-37 |
| Results export | Asset detail; chat | `export_to_excel`, `export_to_csv`, `export_preview_png`, `export_asset_results`, `build_delivery`, `get_file_delivery` | LC-06 (D20) | OJ-37 |
| Network import (4 formats) | Import/export modal | `import_network_nc`, `import_csv_bundle`, `import_excel`, `import_matpower`, `inspect_import`, `import_uploaded_network` | LC-06 (D20) | OJ-41 |
| Excel demand import; image reconstruction | Chat uploads | `read_excel_sheet`, `apply_demand_from_excel`, `reconstruct_network_from_image` | LC-06 (D20) | — |
| Chat summary export | Chat | `export_chat_summary` | LC-06 (D23) | — |

### Guided mode and workflows

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Hub design stepper (5 steps) | Guided hub | `ui_open_panel('hubDesign')`, `hub-design` workflow; cannot target a step | LC-06 (D24) · LC-14 item 4 | OJ-39 |
| Guided results (summary, adequacy, FMEA) | Guided Results | `ui_open_panel(results, …)` | LC-06 | OJ-12 |
| Chat workflows (8) | Assistant start menu | `start_workflow`, `advance_workflow`, `end_workflow`: open-project, build-network, import-data, run-study, explain-results, improve-design, investment-decision, hub-design | Per workflow's family | OJ-39, 42 |
| Chat skills (3) | — | `use_skill`: grill, network-experiment, project-refinement | LC-05 | OJ-27 |
| In-app guides and tours | Guided tour | `get_feature_guide` | LC-06 (D24) | — |
| Capability discovery | — | `find_capabilities`, `use_toolset` | LC-01 | OJ-45 |

### Account, administration, settings

| Feature | What a human does | Copilot today | Layer | Journey |
| --- | --- | --- | --- | --- |
| Sign-in, passwords | Auth pages | None | Out (LC-22) | — |
| Users, organisations, legacy claim, email test | Admin pages | None | Out (LC-22) | OJ-46 |
| Assistant API key; local settings | Settings | None | Out (LC-22, secrets) | — |
| LLM profiles | Settings | `set_active_profile` (destructive card) | LC-06 (D23) | — |
| Dictation | Composer | Input path | LC-07 | — |

## Denominators

- Frontend: 7 routes; 19 slide panels; 12 bottom tabs; 15 result tabs in 5
  groups; 10 compare tabs; 11 decision views; 5 hub steps; 10 templates;
  about 160 human actions. Dead, unrouted pages are left out:
  `ResultsViewer`, `GenerationStack`, `LoadEditor`, `LoadProfileManager`,
  `layout/TopBar.tsx`.
- Backend: 442 router endpoints in 32 modules (plus 2 in `main.py`).
- Harness: 256 tools (read 124, write 75, destructive 37, execution 14,
  long-running execution 6); 8 workflows; 3 skills.
- Catalogue rows above: 122. A tool covers 89 today; 13 are partial; 20
  have no tool. Of those 20, LC-22 refuses 6 by design. The other 14 sit in
  LC-06, LC-12, LC-14 and tickets 18–22. "Covered" means a tool exists, not
  that a journey passes.

Counts are from this source and change with master; regenerate with
`smoke/assistant_inventory.py` and the route listing before relying on them.
