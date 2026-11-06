<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The H2 hub's MILP invest finds no 220/110 kV transformer in the library

Status: ready-for-agent (Q-CL-1 answered 2026-11-05: add cited 220/110 kV units)
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
