<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# KPI headline strip per project type

Status: needs-triage
Blocked by: 01
Type: feature (first slice of DASHBOARD-1)
Model: Sonnet. Reviewer: UX.

## What to build

A row of headline numbers at the top of Results for each project type, for example LCOE, CAPEX, curtailment and, for data centres, time to power. Each card shows its unit, the delta against the base case, a small trend line, the source run, and an "out of date" mark when its inputs changed. Values come from the run's own checked facts; nothing is recomputed in the frontend.
