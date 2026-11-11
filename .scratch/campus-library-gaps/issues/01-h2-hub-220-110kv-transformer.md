<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The H2 hub's MILP invest finds no 220/110 kV transformer in the library

Status: done (#1173; losses check recorded in issue 02, follow-up issue 03)
Type: task
Blocked by: None

Found by the gatekeeper's review of #317 (2026-10-15). It is out of that PR's scope.

## What was seen

On `eh_h2_hub` ± staged, the campus invest with `method="milp"` fails. The PCC
transformer `GRID_IMPORT` is 220/110 kV, and the shipped electrical asset
library (`gridspine` campus library, read by `load_asset_library`) has no unit
at that voltage pair. The least-cost path reports the transformer need as
unresolved.

## What to do

1. Confirm which voltage pairs the shipped library covers, and whether the H2
   hub's 220 kV PCC is meant to be in scope for a campus study.
2. Either add cited 220/110 kV units to the library, or have the MILP path
   carry an unresolvable need as the least-cost path does (unresolved, not a
   failure).

## Done

`eh_h2_hub_staged` completes `invest` with `method="milp"`, or reports the
transformer as unresolved with the reason, as the least-cost path does.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage`. Owner question Q-CL-1 (GK A, gridspine). The H2 hub MILP invest finds no 220/110 kV unit. Which fix? (1, Recommended) Let the MILP path carry the transformer need as unresolved, like the least-cost path: no uncited data enters the library, and both methods then behave alike. (2) Add cited 220/110 kV units to the shipped campus library: the MILP then completes, but needs sources for each rating. (3) Both: unresolved for any pair still missing, plus cited 220/110 kV units. Once answered: ready-for-agent, Opus, files `services/` campus invest MILP path and the gridspine campus library, reviewer GK A.

2026-11-05, owner answer Q-CL-1 (coordinator card): "Add cited 220/110 kV units". The library gains sourced 220/110 kV transformer units (not the recommended "carry as unresolved"). GK A reviews.

### 2026-11-06: CL-01 plan GO (GK A), answers and conditions

- Q1 (a) + (c): impedance and losses on the new units follow `producers/campus.py`'s `_TRAFO_CLASS` >100 MVA
  rule (vkr 0.3, pfe 1 kW/MVA, i0 0.1), tagged `assumed` with the note "no public source; producers/campus.py
  class rule". A test keeps them equal to the rule. Follow-up: `02-220-110-kv-losses.md` (needs-info),
  the Ecodesign 548/2014 + 2019/1783 Tier 2 PEI check.
- Q2 (b): new defaults pack version 2026-11-06 = 2026-10-30 plus the 15 transcribed rows (source, URL,
  "linspann" scope note). It pins the new YAML sha. A test keeps every 2026-10-30 row byte-equal, and the
  shipped versions are untouched. The rows are also in the research doc's addendum.
- Q3 (c) (amends condition 1): R10 sizes only. 200/250/315 MVA are straight reads of R-NR-T-8-3/4/5.
  125 and 160 MVA are log-log interpolated between R-NR-T-8-1/2 and R-NR-T-8-2/3 (the file's method), with
  both anchors and their SEK in the note. A test recomputes them. 120 and 150 MVA are anchors only.
- Conditions: hv_kv, lv_kv, s_mva and vk 12.5 are `assumed`, and the vk note says IEC 60076-5 Table 1 gives a
  minimum. Acceptance: both hubs complete milp and least_cost. Every other template's campus invest output is
  compared before and after. Root and pypsa-gui/backend tests run as separate pytest runs.
- Correction to the plan's "2x": the PCC group has a single unit, so N-1 does not apply (GK A accepted).
  `eh_h2_hub_staged` buys 1x TR_220_110_125 (need 27.3 MVA, 32.8 with the margin) and `eh_h2_hub` buys
  1x TR_220_110_160 (need 104.5 MVA, 125.4 with the margin > 125), with both methods. A twin case (the
  hub's PCC as two parallel units) covers the N-1 arm: 2x TR_220_110_160, because a survivor carries 125.4 MVA.
