<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Canvas overlays collide at about 1250 px width

Status: ready-for-agent
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
