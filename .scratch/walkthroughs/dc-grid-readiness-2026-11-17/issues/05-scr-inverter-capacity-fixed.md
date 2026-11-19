<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05: The SCR's inverter capacity is a hidden template constant, so added battery inverters never lower it

Status: ready-for-agent
Type: task
Impact: high (the SCR can read Pass while the converter fleet behind the PoC has grown several-fold)
Templates: eh_datacenter, eh_datacenter_staged (any project whose PoC bus carries `eh_ibr_mva`)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: spec "Every assumed limit is labelled 'assumed'"; WALK-E2E-1 "results traceable to inputs"

## Repro

1. Create Data Center Energy Hub with IT load 30 and Backup mode Batteries (accepted, see 01). The project gets 9 battery strings of 5 MW on the MV bus.
2. Create a second one with Backup mode Gensets (no strings).
3. Compare "Short-circuit ratio at the PoC" in both scorecards (Guided › Site › Grid readiness, or Expert › Issues).

## Actual

Both read "Pass · Project: 8.333". The note says "fault level over inverter-based capacity (MVA); fault level assumed". The denominator is the PoC bus's `eh_ibr_mva` = 30 MVA, a template constant that overrides the sum of the assets (`services/adequacy/scr_gate.py:102`). It's labelled neither assumed nor sourced, it doesn't move when the backup mode adds 45 MW of battery inverters, and it's editable only as "IBR capacity" in Properties. When the override is blank, the fallback counts IBR generators only, never storage.

Hand check: 4 MW PV + 45 MW strings = 49 MVA of converters on the site side gives SCR 250 / 49 = 5.1, not 8.3. At a 60 MVA fault level (see 04), 60 / 49 = 1.2 (Warn, EMT recommended) against the 2.0 the scorecard shows.

## Expected

- The row shows the inverter capacity it used, labelled assumed when it comes from the template, with its source and a "›" to the field that sets it.
- Without an explicit value, the capacity is derived from the converter assets behind the PoC (generators and storage), or the row reads Unknown and names the missing input.
- When the backup mode adds strings, either the derived capacity includes them, or the row says that the stated capacity predates them.

## Pointers

`backend/services/adequacy/scr_gate.py:14-30, 101-125`; the SCR row in `backend/services/adequacy/grid_readiness.py`; `frontend/src/layout/properties/cardKit.tsx:1068-1086`. Needs a GK A plan.
