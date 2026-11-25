<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 28a — Guided shows no product cost per kg for the ammonia template

Status: ready-for-agent
Source: issue 28 (closed 2026-11-25; its remaining item, owner ruling)
Severity: misleads
Journey fit: results for the ammonia template.

## What

#2017 added a "Cost of hydrogen" tile (€/kg) to the Guided results card, read from the study report's TEA block (`report.tea.lcoh_eur_per_kg`), so a design with an electrolyser shows it. The ammonia template's own product, ammonia, has no cost per kg anywhere: the engine computes a hydrogen LCOH only (`services/results/lcoh.py`), so Guided shows the hydrogen figure for ammonia and no ammonia product cost. Issue 28 quoted 1.73 EUR/kg for ammonia; that figure is the hydrogen cost, not ammonia's.

## Acceptance

- The ammonia template's Guided results show the cost of ammonia per kg (or per tonne, in the unit the template's flows use), from a figure the study records, not derived in the browser.
- A design with no ammonia synthesis shows no such tile.
- Hydrogen's tile is unchanged.

## Also from the #2017 UX review (nits, not taken to avoid a CI restart)

- The shot harness `.scratch/te-28-shots/shoot.mjs` leaves "The flow could not be read from the server." and "Business case: could not be read" in the shots: mock `/api/projects/.../flow` and the business-case route in the next shots.
- The hydrogen tile writes the currency as a hard-coded `'EUR'` (`ResultsCard.tsx`, `money(lcoh.value, 'EUR')`): take the TEA block's currency once it reports one (it carries none today).

Blocked by: none. Gated: GK B2 (the figure's source), UX (the tile).
