<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10: The Production tile omits bought-in product, so Made + Lost ≠ Delivered

Status: ready-for-agent
Impact: medium
Templates: Green Ammonia Hub (staged growth); any process template with a buy-in generator
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d). Shot: `../shots/10-production-no-buy-in.png`

## Repro

1. Projects › New project › From template › Green Ammonia Hub (staged growth). Expert › Run LOPF (32 s, optimal, objective €115.7 M).
2. Results › Summary › PRODUCTION (the same tiles appear in Guided › 10).

## Actual

The tiles read Made 672 kt, Delivered 785 kt, Lost 0 t, Lost production value €0, and Inventory at the end 15.9 t.
`/api/results/process_view` also has `buy_in: 112,402 t` (580,667 MWh_NH3, all in 2035), but no tile shows it. The user sees 785 kt delivered from 672 kt made, with nothing lost.
Key capacities lists "NH3 import 32 MW" and "NH3 shortfall 32 MW" with no tie to the tiles.

Hand check:
- Delivered: 50/100/150 t/d × 365 × 3/5/10 years = 54,750 + 182,500 + 547,500 t = 784,750 t. This matches.
- Made + bought in: 672,348 + 112,402 = 784,750 t. This matches.

## Expected

- A "Bought in" tile (t, whole horizon, and its cost at the buy-in price, €97/MWh_NH3 ≈ €501/t), so Made + Bought in + Lost = Delivered on screen.
- Key capacities use the same words as the tiles.

## Breaks

- WALK-E2E-1 (results traceable to inputs).
- `.scratch/grid-to-process/design.md:338-339`: buy-in (97 €/MWh_NH3) and lost production (800 €/t) are separate outcomes.
