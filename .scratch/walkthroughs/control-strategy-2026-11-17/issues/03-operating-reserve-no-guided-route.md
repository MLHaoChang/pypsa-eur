<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03: Operating reserve has no Guided route and sits under Solver settings › Network

Status: ready-for-agent
Impact: medium
Templates: Island Microgrid (and any off-grid or weak-grid site)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Guided. Walk sections 3, 5, 8 and 9 (including 8 › More settings).
2. Search for "reserve". There is no field, assumption row or link for the operating reserve.
3. Expert › Solver settings. The fields "Reserve for load (%)", "Reserve for solar output (%)" and "Reserve for wind output (%)" are under the **Network** tab. They are not under **Dispatch**.

## Actual

- Spinning or operating reserve is the main control-strategy input for an island. Guided never mentions it, and the Assumptions card does not list "Operating reserve: off".
- In Expert it sits under Network, next to the minimum renewable fraction. A user looking under Dispatch (unit commitment, MIP gap) does not find it.

## Expected

- Guided section 8 (island requirements, per the design) shows "Operating reserve: off (assumed)" with an "Edit ›" that opens the three share fields in place.
- In Expert the block sits under Dispatch, or the Dispatch tab carries a "›" link to it.

The engine side is correct. With load 10 %, solar 20 % and wind 20 %, `/api/results/operating_reserve` matches 0.1·load + 0.2·PV + 0.2·wind to 0 MW over all 168 hours. Results › Capacity expansion › Build shows "The reserve never bound in this run", with a "Reserve inputs ›" link back.

## Breaks

- `.scratch/project-workflow/design.md:402`, rule 7.
- `workspace-reconciliation.md:303` (Control strategy work group, section 8: island requirements).
- EXPERT-GUIDED-1.
