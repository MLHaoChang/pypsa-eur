<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — Mining concentrator run never finishes in a usable time and blocks the app

Status: needs-triage
Impact: high
Templates: eh_concentrator_staged (26,280 snapshots)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Run LOPF still running after 15 min; study still "Studying… | Abort" at 602 s while the header says "Idle". The Solve Queue shows a raw log only. API calls and project delete block (409 solver_in_flight, timeouts). Abort toast: "Could not abort the running simulation ... timeout of 2500ms exceeded". Verdict and export were not reached.

## Why it matters

One template cannot complete the journey; the single backend is unusable while it runs.

## Suggested fix

Default a representative horizon like the other templates, show stage and elapsed time, make Abort reliable, keep other requests responsive.
