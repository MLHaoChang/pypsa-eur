<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 The energy Sankey has no "Process <name>" node, so an NH3 site loses its electricity side (A1b)

Status: ready-for-agent
Blocked by: none (grid-to-process/07 is done; design.md §9 lists A1b as "After GTP-07")
Design: design.md:458 (§9 Follow-ups), option (b) from the #423 review.
Source: gap review 2026-11-21, design map.
Model: sonnet. Review: UX.

## Problem

`pages/results/EnergyFlowsTab.tsx` (from 01, #423) draws carriers to loads. On a site whose electricity feeds a process, such as ammonia, the electricity flows disappear into process carriers, and the user cannot see how much electricity the process takes.

## Acceptance criteria

- [ ] Flows into a process unit end at one "Process <name>" node. Its electric input shows in MW/MWh, and its outputs stay in their own units after the node.
- [ ] The energy-flows export (CSV) carries the node.
- [ ] A vitest on a two-process fixture; no change for a site with no process units.
- [ ] End to end in the real app: the ammonia template › run › Results › Operation › Flows shows "Process <name>" with the electricity into it, and the CSV export holds the same row.

## Likely files

`pypsa-gui/frontend/src/pages/results/EnergyFlowsTab.tsx`, the energy-flows read in `pypsa-gui/backend/routers/results.py` (or its service).

## Comments
