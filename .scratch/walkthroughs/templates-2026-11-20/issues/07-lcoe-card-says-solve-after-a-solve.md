<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Guided Results: the LCOE card says "solve the project" right after a run

Status: needs-triage
Impact: medium
Templates: DC, H2, microgrid
Found by: template walk, 2026-11-20 (master 1f6905e3); raised by UX 3 review of #1777
Shot: ../shots/gdc-afterrun.png, ../shots/gm-afterrun.png

## What

Straight after Run study, Guided Results shows the card 'LCOE (real) — Not run yet — To get it, solve the project in Readiness and run, then run its investment case.' The project was just solved. What is missing is the investment case (which on DC cannot run, see investment-business-case 02), not a solve. Expert Summary after Run LOPF says something else again: 'Checked figures are off on this installation; an administrator can turn them on.'

## Why it matters

The card sends the user back to the step they just finished, and the two modes give two different reasons for the same missing number.

## Suggested fix

Say what is actually missing (the investment case, and the input it needs) and link to it; use the same sentence in Guided and Expert.
