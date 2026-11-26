<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-26: let the user upload the TSO's contingency list for the campus planning screen

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/03-planning-criteria-strength-in-run.md`
Gated: yes (GK A for the engine call, GK B for the route)

## Why

GC-03 built the TSO contingency-list import (`gridspine/ingest/contingency_list.py`,
PSS/E `.con` subset and CSV) and `campus_study.planning_campus(..., contingencies=...)`
takes its result. The campus run's planning stage still screens the campus N-1 only:
no route stores a TSO list with the project, so TRGC 15 cannot yet be run against the
TSO's own list from the app.

## What to build

- A route that stores one `.con` (with its conmap when marked) or CSV list in the campus
  directory and reports its unmatched and unsupported elements with line numbers at upload.
- The planning stage passes the stored list to `planning_campus`; the scorecard row names
  the source and keeps failing closed on any excluded contingency.

## Done when

- An uploaded list with one unmatched branch shows it at upload and on the row, and the row
  never reads pass.
