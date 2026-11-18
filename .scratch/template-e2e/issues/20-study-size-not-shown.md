<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 20 — The size of what the study chose to build is not shown in results

Status: needs-triage
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: results → decide. A user cannot justify an investment without the MW/MWh to build.

## What

Guided and Expert results say 'sized by the study' and never list the chosen capacities (DC genset/battery, microgrid diesel/solar, H2 electrolyser, fuel cell, H2 store, per-stage build). Only the report lists capacities, and those are wrong in places: a placeholder grid of 100–1,000 MW is counted (DC 500 MW against a 40 MW connection, total 580.7 MW; island microgrid grid 100 MW; hospital grid 80 MW against 8 MW), the ammonia shortfall (32.3 MW) is counted as capacity, and no battery/UPS, electrolyser, fuel cell or H2 store row exists (DC staged, microgrid staged, h2 hub, hospital). IPP Expert shows battery +32.2 MW/64.5 MWh correctly, so the data exists. Reproduces on every template walked.

## Acceptance

- Guided results and the report list every built asset with MW (and MWh, t/h where non-electric), per stage for staged templates.
- Placeholder grid capacity and shortfall are not counted as installed capacity; the total is checked against the connection limit.
- Guided, Expert and report show the same figures (test per template).
