<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-07 — Control-settings sheet

Status: ready-for-agent
Type: task
Blocked by: 03
Model: Sonnet
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

After the forward pass, write a control-settings sheet: ramp limiter (MW/min at the PoC), smoothing filter cut-off f_c and the tier split, SOC bands (backup and smoothing slices), GFM droop and H from `gfm_tuning.recommend_gfm_settings`, UPS walk-in and transfer settings with no disturbance counting (ERCOT NOGRR 282), the `select_preset` preset, and a latency check against the band (latency < 1/(2π f)). Export alongside the PSS/E dyr. Rows needing EMT are marked "needs EMT" as in `_check_dynamic`.

## Acceptance

- [x] Tests first: each setting in the sheet traces to a sized capacity; a SOC band from E^smooth = 1.82 MWh in a 10 MWh unit gives 0.91 / 9.09 MWh bounds.
- [x] A tier whose latency exceeds the band limit fails the latency check and is flagged.
- [x] Export lands next to the dyr file; format documented.
- [x] Sub-cycle items are listed "needs EMT".

## PRs

- AS-07 `services/control_settings.py`, tests `tests/test_control_settings.py` (branch `claude/as-07-control-settings-sheet`).

## Comments

- Figures: the formula (2 x (step + checkpoint) + oscillation, as AS-03 pins) gives E^smooth = 1.835 MWh, not RESULTS.md's rounded 1.82. In a 10 MWh unit that is bounds 0.9175 / 9.0825 MWh, not 0.91 / 9.09. The test pins both: `soc_band(10, 1.82)` gives 0.91 / 9.09 (the half-in rule), the formula energy gives 0.9175 / 9.0825.
- Backup MWh is shared over the backup stores by energy rating (the LP only constrains their sum).
- Filter cut-off f_c = min(f_ramp, f_osc) with f_ramp = R/60 / (2 pi S), first order, assumed; the tier crossover between several tiers is not split by frequency, only by power and energy share.
- Format: `services/control_settings.py` docstring and the `format` key of the exported JSON. Not wired into `campus_bundle` (a gridspine file); `write_sheet(sheet, dyr_path)` is the call a runner (AS-06) makes after the bundle.
