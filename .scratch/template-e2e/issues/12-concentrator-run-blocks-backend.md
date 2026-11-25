<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — Mining concentrator run never finishes in a usable time and blocks the app

Status: ready-for-agent; audit 2026-11-25: slice (c) stage and elapsed time landed (#1627, #1630); left: (a) shorter default horizon, (b) reliable Abort and responsive backend (no merged PR found)
Impact: high
Templates: eh_concentrator_staged (26,280 snapshots)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Run LOPF still running after 15 min; study still "Studying… | Abort" at 602 s while the header says "Idle". The Solve Queue shows a raw log only. API calls and project delete block (409 solver_in_flight, timeouts). Abort toast: "Could not abort the running simulation ... timeout of 2500ms exceeded". Verdict and export were not reached.

## Why it matters

One template cannot complete the journey; the single backend is unusable while it runs.

## Suggested fix

Default a representative horizon like the other templates, show stage and elapsed time, make Abort reliable, keep other requests responsive.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. Three parts, slice them (PR-SIZE-1): (a) a shorter default horizon for the concentrator, which is issue 09 fix (b) and changes results (GK A); unblocked by the owner's call TE2E-09-SMALLER-1 (see issue 09 (b), which carries it: do it there once); (b) Abort answers within its timeout and other requests (project delete, API calls) stay responsive during the run (GK B); (c) the run shows stage and elapsed time instead of a raw log (UX). Acceptance: while the concentrator runs, project delete and API calls respond; Abort stops it with no timeout toast. No slice is blocked now.

## Comments

- Seen again 2026-11-21 (template walk, master 6b421b06): Concentrator, Guided › Run study on a 4-core, 15 GB machine: still "Studying… (up to 30 calculation steps) · 28:40 elapsed" after the first LP (interior point 24.5 min, then crossover with 124,120 infeasibilities) and a second LP had started. I clicked Abort at 30:16: no toast, no label change, and the solver was still iterating 3 minutes later; I restarted the API to stop it. Expert header said "Idle" while it ran, and the Solve Queue did not list the Guided run. Shorter horizon works: Expert › Model Horizon › Snapshot window › "Same year per period", 6-hourly (4,380 snapshots) solved in 280 s (upper bound), 254 s after a load/bound edit. That build replaced the template's per-period years with one repeated year, and afterwards Capacity Bounds said "No investment periods configured" on a Multi-period project (possibly caused by the rebuild).

## Audit (2026-11-25)

- 2026-11-25 audit: #1627 and #1630 in master log (RunStrip.tsx); no merged PR found for horizon or Abort; code not re-run.
