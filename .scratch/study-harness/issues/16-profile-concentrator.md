<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 16 — Harness profile: eh_concentrator_staged (planned template)

Status: needs-triage (waits for the template: grid-to-process issue 10)
Type: task
Blocked by: 07; g2p 10 (template), g2p 11 (counterfactual), g2p 12 and 18 (motor start)
Gated: yes (GK A)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_concentrator_staged"; `.scratch/grid-to-process/design.md` §4

## What to build

Profile `eh_concentrator_staged@1` once the template exists: mass balance to 1e-6, motor-start dip at the PoC, restart, N-1 on the site transformer, investment against "site as today"; KPIs lost tonnes, lost-production EUR, shed level, NPV by stage.

## Acceptance criteria

- [ ] Re-triaged against the shipped template; rules and facts confirmed with GK A.
- [ ] A run reaches hand-off on the demo data.

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`

## Comments
