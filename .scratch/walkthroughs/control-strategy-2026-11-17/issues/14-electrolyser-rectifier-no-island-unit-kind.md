<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14: An electrolyser rectifier has no island unit kind, so it can have no Controls block

Status: needs-triage
Impact: medium
Templates: every template with an electrolyser (hydrogen hubs, Energy Hub sites with H2)
Found by: CS-B PR 3 (issue 02) planning, 2026-11-25

## What

Issue 02 asks for a **Controls** block in the section-5 asset form of every converter-coupled asset, the electrolyser rectifier among them (`.scratch/project-workflow/design.md:302`, DC-04 control presets). The Controls block reads and writes the island sidecar's `units[<component>]`. `gridspine/schema/island.py` has no kind for an electrolyser rectifier: `CONVERTER_KINDS` is `bess, pv, wind, fuel_cell` and `UNIT_KINDS` adds `genset, ups, load`. An electrolyser cannot be an island unit, so it gets no control mode and no block.

## Expected

- gridspine's island schema has a unit kind for an electrolyser rectifier (a converter load: which control modes it may run, the fields each needs, and whether any preset applies), with the island LP and the frequency and GFM studies reading it.
- Then the Controls block from issue 02 shows on the electrolyser's asset form without frontend changes (it renders from the GET payload's `controls`, `control_fields` and `unit_controls`).

## Breaks

- `.scratch/project-workflow/design.md:302` (DC-04: "every converter-coupled asset").
- Issue 02's Expected, rectifier part (moved here from issue 02).

## Note

A gridspine schema change: GK review. Engineering questions to settle first: grid-following only, or can it offer frequency response (load-side FFR)?
