# ESS / UPS / converter control presets

Status: ready-for-agent
Type: task
Blocked by: none
Gated: yes (gridspine island schema)
Model tier: opus
Size: S

## What to build

- Named presets in the island sidecar: `grid_following`, `grid_forming_support`, `fast_frequency_response`, `voltage_support`. Each sets converter mode, droop, inertia and UPS behaviour.
- The schema change lands in gridspine (`schema/island.py`); pypsa-gui only selects a preset.
- 02 reads which grid services a project offers from the selected preset.

## Follow-up from DC 03 (response latency)

DC 03 derives each battery/UPS unit's response latency from its effective control
(`services/adequacy/smoothing.py` `LATENCY_S`, `response_latency`; all assumed) and
shows it read-only in the grid-readiness scorecard's "After battery/UPS" Source detail,
the unit's name linking to its asset (UX review, option 1). When this ticket's preset
UI lands, the asset's Controls block shows the preset and this derived latency
read-only, next to the preset that sets it: the one place to change it. The scorecard
note then says "set by its control preset".
