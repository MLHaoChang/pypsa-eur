<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 28 — Non-electric results use MW, and the H2 report carries data-centre wording

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results → report for H2, ammonia, concentrator.

## What

Ammonia is shown in MW, never t/h or t/d; LCOH (EUR/kg: H2 1.60–2.59, ammonia 1.73) appears only in Expert and the report, not in Guided results; electrolyser and fuel cell show 'not counted'. The H2 report contains 'AI-training load step' wording copied from the data-centre template. The ammonia report horizon is 18 years against 2027–2035 in Guided. Concentrator results were not reached (see 12).

## Acceptance

- Results and report show non-electric flows in their own units and LCOH/product cost in Guided.
- Report wording is template-specific; the report horizon equals the Guided horizon.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Still true: 'AI-training load step' is still in `grid_readiness.py`, a data-centre phrase on every template. Slice (PR-SIZE-1): (a) report wording is template-specific; (b) non-electric flows in their own units (t/h, t/d) in results and report; (c) LCOH and product cost in Guided results; (d) the report horizon equals the Guided horizon. Gated: GK A and UX. Blocked by: none.

## Comments

- 2026-11-23 status sweep: landed in #1786 (f08b8d44); #1787 (ce38457d): sized-design table and report take their own units. Remains: AI-training load step wording in grid_readiness.py, LCOH and product cost in Guided, template-specific report wording.
