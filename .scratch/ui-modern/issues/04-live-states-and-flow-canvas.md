<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Live states and flow canvas

Status: in-progress (slice 1 merged, #1335; slice 2 in its PR; slice 3 not started)
Blocked by: 01
Type: feature
Model: Sonnet. Reviewer: UX.
Decision: UI-MODERN-1

Re-scoped from the screenshot audit (`audit.md` findings 2, 8, 9, 10) into three slices.

## Slice 1: first view, loading, empty states, Home theme

- Network canvas opens fitted to view, and stays fitted while the layout settles until the user first pans or zooms. Its carrier and voltage legend is open on first view. On the map modes the legend no longer overlaps the "No bus has a location yet" card.
- Loading: skeleton placeholders instead of blank panels; no wrong value while loading (the Grid connection select no longer reads "Strong" before "None"). Test: `SiteTypeSelect.test.tsx`.
- Empty states say what to do next, with a "›" link only where a GUI field really sets it: Results Summary Reliability row and Control row. The Site 3D empty state already names its one next step and links to the field that sets it.
- Home and the New project dialog follow the workspace theme.
- Not in this slice: finding 8 (the floating "Chat here" button and the repeating Guided banner), now issue 08, and the loud triple "Out of date" marks of finding 10, now issue 09.

## Slice 2: flow on the canvas and map

- Animated flow direction and line-loading colours during playback, switched off under `prefers-reduced-motion`.
- The schematic already had both (`utils/flowMotion.ts`). This slice brings the map level: lines, links and transformers carry the same moving dash, and the flow label gains a direction arrow that stays when motion is off.
- Both legends show a "Flow · loading" key while the results overlay is on, since the overlay replaces the voltage and carrier colours.

## Slice 3: solver progress

- Solver progress shown stage by stage.
