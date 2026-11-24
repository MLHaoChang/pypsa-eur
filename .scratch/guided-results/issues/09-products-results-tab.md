<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 Results › Capacity expansion has no Products tab for H2, ammonia and process sites (R8)

Status: done
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
- [x] End to end in the real app: the ammonia template (staged, Expert) shows t/d and the running share; the figures equal the report's. Not met as written: the H2 hub template has no product bus, so Products is hidden there and kg/h and EUR/kg do not show; no EUR/t exists in any read. Both go to issues 10 and 11 (owner to confirm).

## Likely files

`pypsa-gui/frontend/src/pages/results/groups.ts`, a new `pypsa-gui/frontend/src/pages/results/ProductsTab.tsx`, the process-view read in `pypsa-gui/backend/routers/results.py` (read only), `pypsa-gui/frontend/src/pages/hubDesign/HeadlineStrip.tsx`.

## Comments

### Builder, 2026-11-23: scope and placement as built

- Placement deviates from "a Products tab": Capacity expansion already has the maximum four card tabs (`groups.test.ts`), so Products is a view inside Costs & prices (`views: prices, economics, products`). Coordinator ruled A.
- Scope: output in the product's own unit (kg/h for hydrogen, t/d otherwise, plus the bus-unit amount), running share of each process unit, and for hydrogen the LCOH read verbatim (EUR/kg, same figure as Economics and the report). Utilisation is the unit's running hours over modelled hours, both read. Ammonia has output only: no EUR/t exists in any read, filed as `10-ammonia-cost-per-tonne-read.md`. The Guided section-10 headline is `11-section-10-product-cost-headline.md` (after te2e 21).
- GK B4 conditions (plan GO): one availability predicate (the tab shows when the process view lists a product bus, the same `products` empty_key the read uses); a failed or missing read shows "not available" with the reason, never 0; the LCOH is verbatim with unit and basis; the vitest expects hand-written literals; `process_view.py` reads `eh_process` with `process_chain.truthy` (the #1917 residual) with a parametrised test; the inventory, route-parity and control-matrix checks run at the trial merge.
