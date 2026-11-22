<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 Results › Capacity expansion has no Products tab for H2, ammonia and process sites (R8)

Status: ready-for-agent
Blocked by: none. Related: `template-e2e/28` (non-electric units, needs-triage), whose results half this closes for Expert.
Design: `.scratch/project-workflow/card-workspaces.md` §8 row R8 (X-H2). Owner decisions: RES-CARDS.
Source: gap review 2026-11-21, design map.
Model: sonnet. Review: UX; GK told (reads only).

## Problem

The capacity card's tabs are Build, Operation, Curtailment and Costs & prices. The process view and the product cost (LCOH, cost per tonne of ammonia) are not a results tab. The walks of 17 Nov saw ammonia only in MW and LCOH only in Expert and the report (`template-e2e/28`).

## User-visible impact

On a hydrogen or ammonia project, the product the site makes (tonnes, kg) and what it costs per unit are missing from Results.

## Acceptance criteria

- [ ] Capacity expansion › Products appears only when the network has process units or a product Load. It shows per product: output in its own unit (t/h, t/d, kg), utilisation, and product cost (EUR/kg, EUR/t) from the existing `process_view` / LCOH read. Values are read, never recomputed (a test against the report's figure).
- [ ] Guided section 10's headline for these templates shows the product cost with its unit.
- [ ] End to end in the real app: H2 hub template › run › Results › Capacity expansion › Products shows kg/h and EUR/kg. The ammonia template shows t/d and EUR/t. The figures equal the report's.

## Likely files

`pypsa-gui/frontend/src/pages/results/groups.ts`, a new `pypsa-gui/frontend/src/pages/results/ProductsTab.tsx`, the process-view read in `pypsa-gui/backend/routers/results.py` (read only), `pypsa-gui/frontend/src/pages/hubDesign/HeadlineStrip.tsx`.

## Comments
