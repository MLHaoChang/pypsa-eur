<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Results: best system per architecture, winning-system-type map, grid-extension break-even

Status: ready-for-agent
Model: Sonnet for 03b and 03c; Opus for 03a
Reviewer: GK B; UX for 03b (and 03c if it adds a UI input)
Blocked by: 03b blocked by 03a; 03c independent
Size: 03a, 03b, 03c, three PRs
Type: task
Gated: yes (GK B, UX)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

Three result views HOMER Pro users expect:
- **Categorized results:** the best system for each architecture (for example PV + BESS, PV + genset, genset only, grid only), side by side.
- **Optimal system type map:** over two sensitivity variables (for example fuel price and solar resource), which architecture wins in each cell.
- **Grid-extension break-even distance:** the line length at which extending the grid costs the same as the off-grid system.

GridSpine has the sensitivity sweep (`run_sensitivity_sweep`), pairwise compare and tornado, but not these three views.

## Likely shape

Built on the existing sweep and compare: one sweep per architecture (assets switched off), a results table, a 2-D map chart, and a break-even figure from a grid-extension cost per km. Check ui-modern (charts) and the business-case break-even code for reuse.

## Triage

Confirm on master, split frontend and backend slices if needed, name the files and reviewers, and set the model tier.

## Comments

### Triage 2026-11-12

Master check (`23aa3bc8`): none of the three views exist; the building blocks do.
- Architecture comparison: not found. `services/dc_architecture.py:525` compares data-centre power chains only. Study options in `services/study/questions.py:79-90` (none, bess_1h/2h/4h, bess_pv_2h) have no genset-only or PV+genset option; the only "best" is the battery NPV in `services/study/findings.py:836`. Study options drop assets by omission (`questions.py:79-80`, "omitted, not zeroed"), which is the right way to switch an architecture off; `run_sensitivity_sweep` (`services/workflow_tools.py:603`) only sets attribute values.
- 2-D type map: not found. Sweep results render as a table (`frontend/src/components/SweepCard.tsx`); the only heatmaps are hour x day (`pages/results/HourDayHeatmap.tsx`); `chart_spec.py` has no frontend consumer.
- Grid-extension break-even: not found. `services/finance/business_case.py:731 break_even()` is a revenue-price break-even; `services/finance/breakeven.py:32` is island capability; cable per-km exists only as a capex basis (`campus_electrical_service.py:927`).
- Overlap: ui-modern 02 (energy chart kit) for the map chart; business-case break-even (G8) for the solver.

### Slices
- **03a** (backend) one study per architecture by omission, a categorized-results table of best per architecture (cost, capex, renewable fraction); a tool and route. Opus.
- **03b** (frontend + small backend) 2-D winner map over two sweep variables. Blocked by: 03a and ui-modern 02's chart kit landing, or a plain SVG grid if not.
- **03c** grid-extension break-even: typed EUR/km input, distance at which grid extension equals the off-grid NPV, reusing the `break_even()` bracketing. Independent.

### Acceptance
- [ ] 03a: results list one row per architecture with its best system; an architecture that is infeasible is listed as such, not dropped.
- [ ] 03a: assets are omitted, not zeroed (test).
- [ ] 03b: for a 3x3 grid, each cell shows the winning architecture; the legend names architectures; tooltip shows the cost gap to the runner-up.
- [x] 03c: break-even km equals the closed-form value on a linear-cost fixture; reports "no break-even" when grid is cheaper at any distance.
- [ ] Release-note fragment per PR.

### Likely files
`services/study/questions.py`, `services/study/findings.py`, `routers/studies.py`, `services/finance/business_case.py`, `frontend/src/pages/decision/*`, `components/SweepCard.tsx`.

### Tests
`backend/tests/test_study_tornado_routes.py` pattern; new `test_homer_architectures.py`, `test_homer_grid_extension_breakeven.py`; vitest beside `SweepCard.test.tsx`.

### 03a plan GO, GK B (50), 2026-11-12

The source is the base project's saved network. Named "supply architecture" (G11). Conditions and where each is held:
- G1 pins the source: the record keeps the base fingerprint and row id, and GET reads `stale` (`test_an_edited_base_reads_stale`).
- G2: slacks are never members and never a supply (`test_a_slack_is_never_a_member_nor_a_supply`).
- G3: omit removes members and their dependants, and an unsupplied load bus is not listed (`test_omit_*`, `test_a_load_bus_with_no_supply_path_is_not_listed`).
- G4: cost uses the runner's reading; capex is None when any extendable member lacks overnight cost (`test_row_reads_cost_capex_and_sizes`, `test_capex_is_unavailable_*`).
- G5: renewable fraction is weighted, uses served load less shortfall, counts import gross, and has a 0/0 guard (`test_renewable_fraction_*`).
- G6 statuses: ok / infeasible / failed / not_reached, in rank order (`test_compare_lists_every_status_and_ranks_them`, route tests).
- G7: forks are deleted on every path (`test_a_solver_exception_*`, `test_a_worker_exception_leaves_no_fork`, abort test).
- G8: one study slot. A run, a tornado or a second comparison is refused while one runs, and so is a delete. Budget and `budget_solves` work as for the tornado (`test_abort_marks_the_rest_not_reached_and_one_slot_at_a_time`, `test_budget_caps_the_run`).
- G9: `schema_version`; an unknown one reads `not_evaluated` (`test_unknown_schema_version_reads_not_evaluated`), and never-run returns 404.
- G10: `compare_supply_architectures` (execution_long_running, INPROC_SOLVER job) and `get_supply_architectures` (read) (`test_chat_tools_*`).
- G11: the name is "supply architecture" in code, the tools and `pypsa-gui/CONTEXT.md`.
- G12: mutants are listed in the PR body.

### 03c plan GO, GK B, 2026-11-12
- C1: closed form `km* = (offgrid_npc - grid_supply_npc - fixed_eur) / eur_per_km`; no solver, `business_case.py` untouched (`test_c1_closed_form_by_hand`).
- C2: `offgrid_cheaper_everywhere` (grid cost at 0 km above off-grid), `grid_cheaper_everywhere` (0 EUR/km, grid cheaper at 0 km), `beyond_max_km` (km reported), and grid cost == off-grid is `break_even` at 0 km (`test_c2_*`).
- C3: `grid_supply_npc` is required, no default (`test_c3_grid_supply_npc_is_required`).
- C4: off-grid basis (currency, currency year, discount rate, horizon) is echoed; a different currency or year, or a missing basis, refuses (`test_c4_*`).
- C5: every float finite, `eur_per_km >= 0`, `max_km > 0` (`test_c5_bad_numbers_are_not_established_never_zero`).
- C6: service plus tests only. The tool and route are a follow-up slice, after the harness PRs land; `offgrid_npc` and its basis are then read from the stored 03a result by id, never typed by the model. Filed as [03d-grid-extension-break-even-tool.md](03d-grid-extension-break-even-tool.md).
