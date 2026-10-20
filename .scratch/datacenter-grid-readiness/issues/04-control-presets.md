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
