<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 — A what-if case cannot be run or compared

Status: needs-triage
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: decide/compare: scenario → what-if → compare against base.

## What

Expert Scenarios '+' creates a child project. On DC its Guided section 8 says 'Needs an Energy Hub site' so the study cannot run. Compare shows 'Solved 0' (needs an LOPF objective) on DC, microgrid, H2 and ammonia. Clicking a child row leaves the header on the parent with no active-scenario marker. The scenario list is workspace-wide (other templates' children). A duplicate child name leaves the dialog open with no error. An edit to genset_1 capacity in the Guided drawer on DC did not persist (checked by API; not investigated). Not walked for hospital, IPP, staged templates.

## Acceptance

- A child scenario runs the same study and appears in Compare with its numbers matching the base for unchanged inputs.
- The active scenario is shown; duplicate names give an error.
- The scenario list is per project.
