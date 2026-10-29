<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LV 08 against LC-06 (owner decision LV08-HOLD-1)

Read-and-write-up only; LV 08's Status line is unchanged (the owner decides).
Master `c2bd3b7e9`. Numbers come from running `smoke/family_manifest.py --write` and
`smoke/assistant_control_matrix.py --write` to a scratch path on that commit (nothing
generated is committed). LC-06 S12/S13 (#891, #892) are still open and are not counted.

## Recommendation: shrink LV 08 to a named remainder

LC-06 has built the behavioural fixtures that LV 08 slice C waits for, in a different
shape: 51 of 73 variants are `covered` by public-seam tests with hand oracles, grouped by
the 14 families LV 08 defined. It does not touch the rest of LV 08, which stays done
(slices A, B, control matrix, 128-bound test) or is voice-specific. Do not close LV 08 as
covered, and do not build slice C's journeys again as new tests. Shrink slice C to R1–R4:

- **R1.** Re-point the matrix to the LC-06 manifest. Replace the 14 `pending:issue-05-*`
  journey ids in `assistant_control_matrix.FAMILY_JOURNEYS` by manifest references, so
  there is one ledger (today two ledgers say `pending` and `covered` about one family).
  Make the `full_coverage` guard read variant status from `family_manifest`.
- **R2.** Route parity drift: 8 GUI routes read `needs-tool` again (see table). Close them
  by tool or declaration in the packages that added the routes, not in LV 08.
- **R3.** The six families LC-06 deliberately leaves out (D01/02/20–23: projects,
  topology chronology, import/export, reports, audit/undo, provider parity), plus D24 UI.
  LC-06 says "D01/02/20–23 stay out". These need an owner to say who builds them
  (LC-06 follow-up or issue 05).
- **R4.** Voice-only gates stay with issue 05: hosted profile/device/latency, the prolonged
  mixed-domain conversation, spoken navigation acknowledgements and client-only UI-control
  parity (`ui-local-control-parity-unaudited`).

## Requirement table

Verdict: **covers**, **partly**, **not**. Manifest row = `smoke/family_manifest.py`
variant id; tests are in `pypsa-gui/backend/tests/`.

| # | LV 08 requirement | Verdict | Evidence and what is missing |
|---|---|---|---|
| 1 | Matrix generated from tools, routes, schemas, GUI bindings | not (LV 08 done) | LV 08 own: `assistant_control_matrix.py`, `assistant_route_parity.py`, CI workflows. LC-06 adds `family_manifest.py` (367 tools, each in one primary family, 0 unmapped). |
| 2 | Map every eligible tool to a behavioural fixture | partly | 44 of 367 tools are listed by a `covered` variant. 323 are not. The capability matrix still reads `behavioral_fixture: pending` for all rows. |
| 3 | Panels/subviews/filters/editors | not | Client-only controls are `ui-local-control-parity-unaudited`; LC-D24 is `pending` ("frontend e2e; not re-audited in LC-06"). |
| 4 | Parameter types, units, previews, freshness | partly | Units: D04 (MVA/MWh), D07 (exact 60/80→100 MVA). Freshness/preview: S9 (D17 apply, re-study), S11 (health read fresh from storage). The 165 creation controls have only the mapping test `test_assistant_control_matrix.py`, no behavioural fixture. |
| 5 | Job adapters | partly | Covered: D04 `test_lc06_d04_capacity_lp.py`, D05 `..._d05_foresight.py`, D12 standard and zonal MC, D13 energization/DTC (3 of 10 variants), D15, D16 E1–E6, D17. Pending: `LC-D06.sclopf`, `LC-D06.ac_stage`, `LC-D13.margin_loop`, `reserve_margin_coupling`, `elcc_frontier_campaign`, `eh_redundancy`, `eh_levers`, `eh_dtc_planning`, `multi_energy`, `LC-D14.static_campus` (S12 open). |
| 6 | Evidence sections | partly | D08 (lost load, curtailment, balance), D10, D18 covered. `LC-D09.inflow_spill` pending (S13 open). `LC-D11.fmea_criticality` pending (ticket 30). |
| 7 | Import/export/report operations | not | `LC-D20.delivery` and `LC-D21.reports` are pending ("seam only; bytes/facts not independently inspected"); LC-06 says they stay out. |
| 8 | Fixtures | partly | LC-06 supplies the oracle fixtures above; none for projects, templates or import. |
| 9 | Resolve AR against UI vocabulary | not (LV 08 done) | Matrix 2026-10-11: AR maps to Asset Reliability (adequacy/FMEA IDs). LC-06 neither changes nor needs it. |
| 10 | Fix adequacy/fmea navigation parity, audit aliases | not (LV 08 done) | `test_assistant_navigation_coverage.py` (every result tab and panel, invalid inputs rejected, aliases kept). Not LC-06 scope. |
| 11 | Add missing typed tools over services | not (drifted) | LV 08 closed this at 0 needs-tool of 466. Now 8 of 474 GUI routes: `GET /api/results/process_view` (all-results); `site-anchor` GET/PUT and 4 `sites/{id}/scene` routes (projects). Added after LV 08's close by other packages. |
| 12 | ACL/mode/project eligibility, no click workaround | partly | `test_assistant_tool_reachability.py` checks `eligible_for_project` for every tool on network and planning_dynamics projects (LV 08). LC-06 asserts ACL only in D01 (pending). |
| 13 | Discovery, then offering and execution of every eligible tool | partly | Discovery and offering: same test, every catalogue tool. Execution: only the 44 covered tools; the other 323 are never executed through the seam. |
| 14 | 128-tool OpenAI offer bound | covers (LV 08 test) | `test_assistant_tool_reachability.py` asserts `len(offered) <= 128` for the OpenAI profile with `CONTROL_TOOLS` always offered; selection is `_bounded_tools`/`_OPENAI_TOOL_LIMIT` in `harness/providers/wiring.py`. LC-06 adds nothing. |
| 15 | Claude parity | partly | Same test runs a `claude` profile (offer and eligibility); `test_assistant_navigation_provider_parity.py` for navigation. LC-D23 `provider_parity` is pending ("dispatch parity only"): no journey runs on both wires. |
| 16 | All navigation acknowledgements | partly | `test_assistant_navigation_coverage.py`, `test_assistant_navigation_provider_parity.py`, `test_lc14_navigation_reach_journey.py` (text path). Spoken acknowledgements are voice-only (R4). |
| 17 | Matrix regeneration/check in CI | covers | LV 08 workflows `assistant-control-matrix.yaml`, `assistant-route-parity.yaml`; LC-06 `family_manifest.py --check` has its own tests. Two ledgers: R1. |
| 18 | Every required interaction has a mapping and passing contract | partly | Mapped yes (matrix, manifest). Passing contract: see rows 2, 4–8. |
| 19 | Every family has a real journey assigned to issue 05 | not | The 14 `pending:issue-05-*` ids are unchanged. Issue 05 text names no family journeys beyond load flow and GridSpine; it is blocked by 01–11 and has not started. LC-06's manifest covers 12 of 14 families' suites only in part (see below). |
| 20 | Remaining limitations block the full-coverage claim explicitly | covers (both) | `full_coverage: not_established` with 14 journey blockers, 3 standing blockers and 2 `route-parity:needs-tool` blockers in the matrix; the manifest prints `full_coverage=not_established`. Keep both. |

### Slice C (behavioural journeys on issue 05) by family

Counts are LC-06 variants (covered / pending / unavailable) of the suites that
`SUITES_BY_FAMILY` assigns to the family, so a suite shared by two families counts in both.

| Family (`pending:issue-05-…`) | LC-06 state | Verdict |
|---|---|---|
| projects | D01 pending, D19 covered (2), D22 pending | partly |
| network-topology | D02 pending, D09 3/1, D15 covered (7) | partly |
| timeseries-horizon | D03 1/1 (chronology edges pending), D05 covered (4), D09 as above | partly |
| solver-simulation | D04 4, D05 4, D06 5/2, D07 6/0/1, D14 pending, D16 6/0/1, D17 1 | partly |
| all-results | D04–D18 mixed (see suites) | partly |
| reliability-adequacy | D11 3/1, D12 covered (3), D13 3/7 | partly |
| gridspine-campus | D14 pending, D15 7, D16 6/0/1, D17 1 | partly |
| economics-investment | D04, D05, D09, D10, D18 | partly |
| experiments | D19 covered (2) | covers |
| import-export | D20 pending | not |
| reports | D21 pending | not |
| audit-undo | D22 pending | not |
| harness-workflows | D23 pending | not |
| ui-monitoring | D24 pending | not |

## Checks asked for

- **Capability-matrix `route_parity` blockers.** Two now: `route-parity:needs-tool:projects`
  (7 routes) and `...:all-results` (1). LV 08's last comment said 0 of 466. Cause: GUI
  routes added since (site anchor/3D scene, `process_view`); not an LC-06 or LV 08
  regression. `assistant_route_parity.py --check` still passes with 0 unclassified.
  Counts: 515 backend routes, 474 GUI routes, 379 tool, 8 needs-tool, 87 limitation.
- **128-tool bound and Claude parity.** Covered by LV 08's own
  `test_assistant_tool_reachability.py` (all tools, 3 profiles, 2 project kinds). Offer-time
  only. LC-06 has no 128 or wire-parity test.
- **Issue-05 journey assignments.** Unchanged and not wired to LC-06. Issue 05 stays blocked
  by 01–11; its text still describes load flow and GridSpine as the illustration.

## Overlap if nobody acts

Building slice C as written would duplicate LC-06 for solver, reliability, gridspine and
experiments (the four families where LC-06 has a hand oracle) and miss the six it left out.
