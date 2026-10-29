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
