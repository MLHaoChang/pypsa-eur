<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 — A what-if case cannot be run or compared

Status: done
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: decide/compare: scenario → what-if → compare against base.

## What

Expert Scenarios '+' creates a child project. On DC its Guided section 8 says 'Needs an Energy Hub site' so the study cannot run. Compare shows 'Solved 0' (needs an LOPF objective) on DC, microgrid, H2 and ammonia. Clicking a child row leaves the header on the parent with no active-scenario marker. The scenario list is workspace-wide (other templates' children). A duplicate child name leaves the dialog open with no error. An edit to genset_1 capacity in the Guided drawer on DC did not persist (checked by API; not investigated). Not walked for hospital, IPP, staged templates.

## Acceptance

- A child scenario runs the same study and appears in Compare with its numbers matching the base for unchanged inputs.
- The active scenario is shown; duplicate names give an error.
- The scenario list is per project.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Three slices (PR-SIZE-1): (a) a child scenario runs the same study and Compare shows its numbers (the 'Needs an Energy Hub site' refusal is still in `flow_scope.py`); (b) the active scenario is marked in the header and duplicate names give an error; (c) the scenario list is per project. Acceptance as above. Gated: GK B and UX. Blocked by: none.

## Comments

- 2026-11-24, master 6bbc05d8f, walked in the app (DC and microgrid templates, each with one child):
  - (a) merged in #1838 (59849cc4).
  - (b) **already fixed on master, dropped.** Switching to a child row's arrow puts the child's name in the header, opens its tab and badges the row ACTIVE. A duplicate child name returns 409 and shows the toast "Create failed: Project 'DC1-kid' already exists" (the dialog stays open for a retry). Clicking the row text itself does nothing; only the arrow switches, as designed.
  - (c) reproduced on master (DC1's Scenarios list showed MG1 and MG1-kid, count 4). Fixed here: the panel lists only the open project's tree (root plus descendants); with DC1 open it now shows 2.
- Not walked: hospital, IPP and the staged templates (the fix is template-independent: it filters by the parent pointer).
- The genset_1 Guided-drawer persistence point was not re-walked in this slice, so no new issue was filed for it.
