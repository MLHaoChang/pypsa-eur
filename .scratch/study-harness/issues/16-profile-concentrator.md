<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 16 — Harness profile: eh_concentrator_staged (planned template)

Status: ready-for-agent
Model: Opus
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

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage (waits for the template: grid-to-process issue 10)`. The template is shipped (grid-to-process 10, #869) and the counterfactual (11) and motor-start fields (18) are done. Files: `pypsa-gui/backend/services/run_profiles.py`, `services/run_gates.py`. Blockers: study-harness 07 (ready, a live session works on it) and grid-to-process 12 (12b, ready). Reviewer: GK A.
