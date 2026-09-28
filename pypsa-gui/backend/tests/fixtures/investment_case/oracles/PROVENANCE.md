# Billing oracles — provenance (IC P2)

Plan: `docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md` § Oracles.

Source repository: **REopt.jl** (https://github.com/NREL/REopt.jl), commit
`6d4328916b6801e29da0b09e4ca1ccbccbf37ac9`, licensed under the Apache License,
Version 2.0. REopt's `NOTICE` is reproduced in `REOPT_NOTICE` (Copyright 2023
Alliance for Sustainable Energy, LLC). Files here are marked:

- **verbatim** — copied unchanged from the REopt repository;
- **derived** — inputs copied from a REopt test body, or a hand translation of
  a verbatim file into this tool's `Tariff` model (a modification under
  Apache-2.0 §4(b));
- **self-authored** — written for this project.

| File | Kind | Source | Expected value |
|---|---|---|---|
| `r2_tiered_tou_demand.urdb.json` | verbatim | `test/scenarios/tiered_tou_demand.json` → `ElectricTariff.urdb_response` | — |
| `r2_tiered_tou_demand.tariff.json` | derived (hand translation of the above) | same | demand `12 × (50 × 0 + (P − 50) × 12)`, `P = 1e6 / 8760` kW flat (runtests.jl L2025–2037); energy not pinned by REopt |
| `r4_blended.tariff.json` | derived (from `test/scenarios/no_techs.json` blended rates) | "Blended tariff" L384–391 (R4a); "Fifteen minute load" L537–545 (R4b) | R4a: energy `0.10 × 10000 = 1000.00`, demand `12 × 10 × 10000/8760`; R4b (15-min, 35,040 × 1 kW, 2017; REopt asserts only annual kWh — the bill is **formula-derived**): energy `876.00`, demand `120.00` |

The URDB `max` of a tier is cumulative (URDB definition); REopt reads it as a
width. The readings agree for two tiers, the only tier cases pinned here.
