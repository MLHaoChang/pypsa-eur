<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Grid readiness counts differ between the Guided step and the report

Status: needs-triage
Impact: medium
Templates: DC, microgrid
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/edc-rep.png

## What

DC: Guided step 3 says 'Grid readiness: 6 things to look at, 13 not yet known · 2 pass after battery/UPS'; the report (built after the run) says '6 red flag(s), 12 unknown, 17 on an assumed limit'. Microgrid: Guided '0 things to look at, 7 not yet known'; report '0 red flag(s), 6 unknown, 4 on an assumed limit' with one row 'left out as not applicable' (the PoC short-circuit ratio). On the island Guided also shows 'Grid strength: not needed for this site type' yet counts 7 unknown.

## Why it matters

Guided and Expert are meant to agree on every verdict and count (the 2026-11-17 DC walk found they did). A count that moves by one between screen and document makes the reader doubt both.

## Suggested fix

Count the same rows in both: leave out rows that are not applicable (as the report does) on the Guided step too, or say why they differ.
