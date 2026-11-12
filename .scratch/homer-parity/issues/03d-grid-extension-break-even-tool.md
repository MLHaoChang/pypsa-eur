<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03d — Grid-extension break-even: tool and route

Status: needs-triage
Model: Sonnet
Reviewer: GK B
Blocked by: the harness PRs #1427, #1429, #1431 landing
Type: task

## What

Expose `services/finance/grid_extension_breakeven.py` (03c) as a read-only tool and route.
`offgrid_npc` and its basis (currency, currency year, discount rate, horizon) are read from the stored 03a supply-architecture result by id, never typed by the model; `eur_per_km`, `fixed_eur`, `grid_supply_npc` and the grid basis are typed. A UI input needs the UX reviewer's OK first.

`grid_supply_npc` must be on the same discount rate and horizon as the 03a row; the service checks only currency and currency year, so the tool must check or declare the rest (GK B note on #1435).

## Acceptance
- [ ] A tool and a route return the service's status, `km` and basis; a basis mismatch refuses.
- [ ] Catalogue entry, route-parity declaration and release-note fragment.
