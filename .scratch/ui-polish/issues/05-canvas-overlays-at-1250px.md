<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Canvas overlays collide at about 1250 px width

Status: done
Type: layout
Found by: UX (9) review items; owner list DOCS-1.

## Problem

At a window about 1250 px wide the canvas overlays overlap or crowd each other. Reproduce at that width in Guided and Expert and fix the placement.

## Where to look

Network canvas overlay components under `pypsa-gui/frontend/src/`. Details: ask the coordinator.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: network canvas overlay components under `pypsa-gui/frontend/src/`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: M.
- Reproduce at 1250 px in Guided and Expert with a screenshot before and after.

### 2026-11-07: built

- At 1250 px with Properties open the canvas is ~710 px: the open legend card covered Bus 0 and the Gas node and crowded the left toolbar; the minimap and the assistant launcher stacked against Load 2.
- Fix in `TopologyCanvas.tsx` and `App.tsx`: the legend starts folded below a 760 px canvas (a saved choice still wins), the minimap hides below 760 px (was 640), and the companion launcher anchor drops to `bottom-14` when the minimap is hidden.
- Shots: `.scratch/ui-polish/shots/uip05-{guided,expert}-{before,after}-{light,dark}.png`.
