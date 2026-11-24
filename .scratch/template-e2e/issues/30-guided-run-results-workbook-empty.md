<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 30 After a Guided study run, the results workbook is empty and Expert reads "Not run yet"

Status: ready-for-agent
Blocked by: none. Related: `provenance-graph/13` (an edit drops the last run to not_run), which may share the cause: the run kinds a Guided study writes vs the LOPF results Expert and the workbook read.
Source: TE2E-29 builder (session_01G7qCuk2dRcskiyXzKdfgdo), relayed by the coordinator 2026-11-21; filed in the gap review (`.scratch/gap-review-2026-11-21/README.md`). The `template-journeys.mjs` workbook step is xfail-keyed (`L.gap`) to this issue.
Severity: blocks a journey (a Guided user cannot export results)
Model: sonnet (diagnose first: `diagnosing-bugs` skill). Review: GK B.

## Problem

On `eh_site`, a Guided study run completes and its verdict shows. Then:
- Expert sidebar › Export › "Results workbook (Excel)" lists every tab as "No results for this view in this solve";
- the Expert header reads "Not run yet";
- Download File produces no file (no download event within 120 s).

Ask the TE2E-29 builder for the exact repro script if it is needed.

## User-visible impact

A user who works in Guided, the default path, cannot export the results workbook of the run they just made. Expert contradicts the Guided verdict.

## Question for the owner

Answered 2026-11-24 (owner decision TE2E-30-1): option 1, the study's own solve.


What should the workbook export after a Guided study run?
1. **(Recommended) The study's own solve**: the workbook reads the solve the Guided study produced (the reference-design dispatch), and the Expert header reads that run as the latest. One run, one set of figures, everywhere.
2. Only the network LOPF results: after a study run, the export says "This workbook holds the network run; run Run LOPF in Expert", with a link, and Expert's "Not run yet" names the study run beside it.
3. Both, as two workbook choices: "Study results" and "Network run".

## Acceptance criteria (option 1)

- [x] A failing test first: after a Guided study run on `eh_site`, the workbook route returns a workbook whose dispatch and capacity sheets are filled, and the flow/Expert run state reads the study run.
- [x] Download File gives a file, or a plain error with its reason. A request that would yield an empty workbook says so before any download starts.
- [x] The `template-journeys.mjs` xfail keyed to 30 is removed and the step passes.
- [ ] End to end in the real app: `eh_site` › Guided › run the study › Expert › Export › Results workbook › Download. The .xlsx opens, its headline equals the Guided verdict, and the Expert header names the run.

## Likely files

`pypsa-gui/backend/services/results_workbook/` (source run choice), the run-state read behind the Expert header (`services/flow_service.py` freshness), `pypsa-gui/frontend/src/layout/Sidebar.tsx` (export download), `pypsa-gui/frontend/scripts/template-journeys.mjs` (the xfail).

## Comments
- 2026-11-24 coordinator: owner chose option 1 (TE2E-30-1); Status → ready-for-agent. Found blocking guided-results 07's export check (#1944). Opens after template-e2e 11 (#1948) merges: both edit `template-journeys.mjs`.
