<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 17 — Expert has two Run buttons, no readiness block, and no progress while waiting

Status: needs-triage
Impact: medium
Templates: all templates
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Header "Run LOPF" gives cost and capacities with no verdict; the verdict study sits under Results > Reliability > collapsed "ENERGY HUB REFERENCE DESIGN" > PACK SETTINGS > "Run study". Readiness is always "Errors 0 / Warnings 0 / ready to run", with advisories that never block (location, 4 red flags; 01). While waiting: only "Studying… | Abort"; the header reads "Idle"/"Optimal".

## Why it matters

A user cannot tell which run answers their question, whether it may start, or how long it will take.

## Suggested fix

One Run entry that runs the verdict study; Expert readiness shows the same to-check items as Guided; show stage x of N and elapsed time; header reflects the running study.
