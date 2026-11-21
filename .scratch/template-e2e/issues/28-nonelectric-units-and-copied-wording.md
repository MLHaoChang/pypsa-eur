<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 28 — Non-electric results use MW, and the H2 report carries data-centre wording

Status: needs-triage
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results → report for H2, ammonia, concentrator.

## What

Ammonia is shown in MW, never t/h or t/d; LCOH (EUR/kg: H2 1.60–2.59, ammonia 1.73) appears only in Expert and the report, not in Guided results; electrolyser and fuel cell show 'not counted'. The H2 report contains 'AI-training load step' wording copied from the data-centre template. The ammonia report horizon is 18 years against 2027–2035 in Guided. Concentrator results were not reached (see 12).

## Acceptance

- Results and report show non-electric flows in their own units and LCOH/product cost in Guided.
- Report wording is template-specific; the report horizon equals the Guided horizon.
