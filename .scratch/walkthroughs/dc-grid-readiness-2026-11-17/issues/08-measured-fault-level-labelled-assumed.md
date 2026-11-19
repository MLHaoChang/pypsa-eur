<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08: A fault level saved as "Measured" is still shown as "(assumed)"

Status: ready-for-agent
Type: task
Impact: medium (misleads about which inputs are confirmed)
Templates: eh_datacenter (Guided field drawer and Expert Connection limits)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: spec "Every assumed limit is labelled 'assumed'" (and only those); WALK-E2E-1 "plain words"

## Repro

1. Create Data Center Energy Hub with defaults. Guided › 3 Connection & grid › Assumptions › Fault level at the PoC › Edit ›.
2. Enter 60, Source Measured, a note. Save fault level. Press Ctrl+S.
3. Read the drawer and the Assumptions list.

## Actual

- The Assumptions list correctly drops the fault level ("3 defaults not yet confirmed").
- The drawer still reads "Fault level: 60 MVA (assumed)". Its SCR row stays `assumed: true` with "fault level as entered", because the 3.0 SCR threshold is a screening rule. The two "assumed" causes share one chip next to the fault level.

## Expected

The fault level's chip follows its own source ("Measured"). The SCR row labels the screening threshold as assumed on the Limit, not on the user's measured value.

## Pointers

`frontend/src/components/gridReadiness/ConnectionLimits.tsx` (the heading line's chip); the `scr` row's `assumed` in `backend/services/adequacy/grid_readiness.py`.
