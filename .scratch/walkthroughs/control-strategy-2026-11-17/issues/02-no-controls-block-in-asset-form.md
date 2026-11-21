<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02: Converter control modes have no home in section 5; Guided cannot see or set them

Status: ready-for-agent
Impact: high
Templates: Island Microgrid, Data Center Energy Hub, every template with a battery, PV, wind or UPS
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Guided, section 5 › Storage › island_battery › "Edit ›".
2. Read the drawer: carrier, power, duration, discount rate, efficiencies, cyclic state of charge. There is no Controls block.
3. Press Expert › 8 Scenarios & optimisation › Grid study › Island. The control modes are only here: "Control mode of island_battery: gfm_droop", "Control mode of pv_plant: gfl_qu", and so on, with a raw JSON editor below.

## Actual

- The control mode, droop, current limit and inertia of each converter-coupled unit can be edited only in the Grid study panel (section 8, Expert).
- Section 5 lists them only as one assumed ledger line ("Island unit settings: … batteries droop 2 %, …"), whose Edit link is broken (issue 01).
- Guided has no route to them.

## Expected

Per the approved design, every converter-coupled asset (BESS, UPS, PV inverter, electrolyser rectifier) has a **Controls** block in its section-5 asset form. The island panel shows the preset read-only, with a link back. Guided shows the block at its depth (≤3 decisions per card) or names the field with a route.

## Breaks

- `.scratch/project-workflow/design.md:302` (DC-04 control presets: "A Controls block in the asset form of every converter-coupled asset. It is not edited in the island panel").
- `design.md:381` (B3 asset form: General › Sizing › Controls › Reliability).
- `design.md:402`, rule 7.
- `.scratch/project-workflow/workspace-reconciliation.md:303`: the Control strategy work group's inputs are "5: the Controls block of each converter-coupled asset; 8: island requirements".
- EXPERT-GUIDED-1.

## Note

This is a design slice that has not been built, not a regression. It needs a plan before building (it touches the asset form and `CampusIslandSection.tsx`). Issue 01's link fix can land first and point at the island panel.
