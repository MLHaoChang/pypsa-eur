<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07b The process view reads `eh_process` with `_as_bool`, so odd values disagree with the solver

Status: ready-for-agent
Blocked by: none
Source: GK B4 review of #1917 (residual, not in that PR).

## Problem

`pypsa-gui/backend/services/results/process_view.py:284` picks the process units with `eh_columns._as_bool`. The solver and the energy-flow Sankey (since #1917) use `process_chain.truthy`. A value such as "1.0" or "on" can raise there, and "y" or "t" can read as a process in one reader and not in the other.

## Acceptance criteria

- [ ] `compute_process_view` reads `eh_process` with the same `truthy` function as `energy_flows.py` (same import and fallback), so the three readers agree.
- [ ] A parametrised test: "true", "1", "1.0", "yes" give a process unit; "false", "0", NaN, "on", "y" give none and raise nothing.

## Likely files

`pypsa-gui/backend/services/results/process_view.py`, `pypsa-gui/backend/tests/test_process_results.py`.
