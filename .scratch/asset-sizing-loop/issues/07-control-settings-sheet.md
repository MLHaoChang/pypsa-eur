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

- [ ] Tests first: each setting in the sheet traces to a sized capacity; a SOC band from E^smooth = 1.82 MWh in a 10 MWh unit gives 0.91 / 9.09 MWh bounds.
- [ ] A tier whose latency exceeds the band limit fails the latency check and is flagged.
- [ ] Export lands next to the dyr file; format documented.
- [ ] Sub-cycle items are listed "needs EMT".

## PRs

## Comments
