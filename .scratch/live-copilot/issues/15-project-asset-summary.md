<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-15 — "open project X and tell me what's in it"

Status: ready-for-agent inside LC-01, after LC-01B; can run beside LC-10
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-01B (exact project names), LC-01A (schema discovery)
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-13, OJ-14

## Problem

The owner's first core feature is "open a project and give a summary of its
existing assets". The pieces exist but no single bounded read answers it:

- `get_project_statistics` (`routers/projects.py:4610`) returns only bus,
  generator, line and snapshot counts and generator carriers. It omits loads,
  links, transformers, StorageUnits, Stores, installed capacity, storage energy
  and the time axis.
- `get_meta`, `list_components` and `get_component` can be stitched
  together, but a 3-bus project already needs 8–10 calls of the 25-call turn
  budget (`harness/budget.py:29`), and a large one cannot be summarised
  within it.
- Nothing states what the project has *not* got (no results, stale results,
  validation warnings, no time series for a variable generator).

## Scope

1. **One native read**, extending the statistics endpoint and its tool rather
   than adding a parallel one: per class count; installed capacity by carrier
   (`p_nom` MW, `e_nom` MWh, `s_nom` MVA) with extendable flags; storage
   power and energy; line count and total length; snapshot range, resolution
   and count; investment periods; solver mode; latest run (ID, status, finish
   time, current or stale); validation error/warning counts. Bounded: top-N
   per class by capacity with `has_more`.
2. **Works on a non-active project** (as `get_project_statistics` does
   today), so "what's in oj-grid-copy?" never switches the active project.
3. **Prompt rule**: after opening a project, read the summary before talking
   about it; two or three sentences, then offer next steps (results, run,
   edit). Every number comes from the read.
4. **Show it**: open the Overview panel (`ui_open_panel('overview')`).

## Not in scope

Results summaries (LC-03); plausibility (LC-20).

## Done when

OJ-13 and OJ-14 pass in Tier A with the fixture's hand-counted oracle; the
inventory and control matrix are regenerated; no new write authority.
