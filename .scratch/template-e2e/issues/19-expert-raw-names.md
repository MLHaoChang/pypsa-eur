<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 19 — Raw internal names across Expert results, readiness and queue (RAW-NAMES-1; adds to 05)

Status: done (#1820)
Landed in #1820, #1821, #1827.
Impact: medium
Templates: all templates
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Readiness: 'ups_battery', bess_new, "Set PoC X/R". Grid study: ups_bridged, gfl_pq gfl_qu gfl_pf gfl_fw gfm_droop gfm_vsm, "Control mode of bess_new". Study card: weak_flexible, strong_grid, off_grid, dsr_opt_in=True, ReferenceDesignReport, "budget_solves exhausted (1/1)", "target: ok / certification: skipped / frontier: not_established / fmea_top", import_model=sampled_unit, "DYNAMICS GATE SCR pass | EMT no", per_load_slack, "reserve_margin in solver settings", "P19 template", "Class-B Links". Solve Queue: [PHASE], [NUMERICS], "n.sanitize()", user_objective_scale, __voll_shipments. Commercial: "capex_supplier (external)", "Template: custom". "Checked figures are switched off for this installation." on LCOE/curtailment of every template. Next step "press Ask the assistant" is a dead end without a key.

## Why it matters

Violates RAW-NAMES-1 on the screens that carry the decision.

## Suggested fix

Plain display names for every listed item; hide the queue log behind "details".

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Acceptance: no raw id (`ups_battery`, `bess_new`, `gfl_pq`, `gfm_droop`, `weak_flexible`, `budget_solves exhausted`, `ReferenceDesignReport`) on the Readiness, Grid study and study-card screens; plain display names for each; the queue log sits behind 'details'. RAW-NAMES-1 applies. Slice by screen (PR-SIZE-1). Gated: UX. Blocked by: none.
