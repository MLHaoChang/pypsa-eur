<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-15 — a constant step per run (sampled multi-hour windows)

Status: ready-for-agent (Q-RP-15 answered 2026-11-06)
Type: task
Baseline: master after #606 (RP-03)
Raised by: the engine gatekeeper, reviewing #606, 2026-10-22

RP-03 averages a profile over each step when the whole snapshot index has ONE constant whole-hour step.
Sampled windows with a multi-hour step inside each run (for example two 3-hourly weeks) used to be read
as a window of one-hour runs and sampled at the first hour of each step. They are now refused as
`step_irregular`, because the gaps between runs are not the step.

To decide: accept a constant step k inside each run, with any gap between runs at least k hours. Each
snapshot then takes the mean of its own k hours, and the window record keeps its runs. The plan for
RP-03 said "constant (or constant per run)"; v1 shipped the constant case only.

Acceptance (once decided): two 3-hourly weeks in different months apply as step means over each
snapshot's three hours; a run whose own step differs from the others is still refused by name.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage`. Owner question Q-RP-15. Should RP-03 accept a constant step k inside each run of a sampled window? (1, Recommended) Yes: constant step per run, any gap between runs at least k hours, each snapshot takes the mean of its own k hours, a run with a different step is refused by name; it restores the two 3-hourly weeks case the plan promised. (2) No: keep `step_irregular`, document it and wait for a user who needs it. Once answered (1): ready-for-agent, Sonnet, files the RP-03 step-mean code in `pypsa-gui/backend/services/` profiles, reviewer GK B.

2026-11-06, owner answer Q-RP-15 (coordinator card): "Accept constant step k (Recommended):". Role ready-for-agent.
