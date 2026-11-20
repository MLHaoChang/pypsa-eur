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

## Comments

GK A (33) plan GO (2026-11-20), with S1–S8:

- S1 Connection: one reading, `flow_service.import_capacity` (#1772). No own filter and no p_nom fallback. Refused: `grid_connection_mw`/`connection_check` None plus the refusal sentence, never a number or "within".
- S2 Grid supply leaves the total only structurally: a generator on an import Link's grid-side bus that holds nothing else of the site's; otherwise counted, with the reason. A grid generator at the PoC with no Link is grid supply, labelled "unbounded connection". Both tested.
- S3 Buy-in and shortfall (by `process_role`) leave the total but get their own key, with energy. A delivered shortfall is its own report row; ammonia staged pinned.
- S4 Every sizing consumer is listed with what it reads, old and new. An old stored report renders and hashes byte-equal (pin test).
- S5 Peak = max over snapshots of summed import `p0`, positive direction, checked per period (EXCEEDS when any period does); unsolved gives None; the tolerance is stated and pinned on both sides.
- S6 `unit` from `_size_unit` (bus, or bus0 for a Link); `stage` = int(build_year) on multi-period only; old rows keep the unit fallback.
- S7 Real HiGHS tests for DC and ammonia staged. One targeted mutant per guard, each killed by a named test.
- S8 Two PRs: backend + fixture (GK A), then the frontend (UX) stacked on it.

Builder note: an export-only Link into the grid bus (IPP `grid_export`: bus1 = grid, `p_min_pu` ≥ 0) is not site equipment, so it does not stop the S2 split. A two-way Link does.
