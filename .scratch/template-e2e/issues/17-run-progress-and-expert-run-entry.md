<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 17 — Expert has two Run buttons, no readiness block, and no progress while waiting

Status: ready-for-agent
Impact: medium
Templates: all templates
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Header "Run LOPF" gives cost and capacities with no verdict; the verdict study sits under Results > Reliability > collapsed "ENERGY HUB REFERENCE DESIGN" > PACK SETTINGS > "Run study". Readiness is always "Errors 0 / Warnings 0 / ready to run", with advisories that never block (location, 4 red flags; 01). While waiting: only "Studying… | Abort"; the header reads "Idle"/"Optimal".

## Why it matters

A user cannot tell which run answers their question, whether it may start, or how long it will take.

## Suggested fix

One Run entry that runs the verdict study; Expert readiness shows the same to-check items as Guided; show stage x of N and elapsed time; header reflects the running study.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Overlaps issue 09 (study size) and issue 12 slice (c) (stage and elapsed time). Question for the coordinator: what is the Run entry? (1) Recommended: the header 'Run' runs the verdict study, Expert readiness lists the same to-check items as Guided, and stage x of N with elapsed time shows (reuse 12 (c)). (2) Keep two entries but label each by the question it answers, and add stage and elapsed time. (3) Only add stage and elapsed time (12 (c)) and drop the rest.

Decision (owner, 2026-11-20, TE2E-17-RUN-1): the header 'Run' runs the verdict study; Expert readiness lists the same to-check items as Guided; 'stage x of N' with elapsed time shows (reuse issue 12 slice (c), do it once). Acceptance: one Run entry that runs the verdict study; Expert readiness and Guided list the same items; stage and elapsed time show while it runs; the header reflects the running study. Gated: UX. Blocked by: none (shares 12 (c)).
