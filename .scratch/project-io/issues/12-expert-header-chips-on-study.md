<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 The Expert header shows bus/line/asset chips and component search on a Study project

Status: needs-triage
Blocked by: none
Source: follow-up from 08 (shot `.scratch/project-io/shots-08/04-dispatch-window-1-of-3.png`).

## Problem

On a planning → dynamics Study, Expert's header still shows "0 buses · 0 lines · 0 assets" and the "Search components…" box. A Study has no network canvas, so they always read zero and the search finds nothing. Guided's header has neither.

## Acceptance criteria

- [ ] On a Study project, Expert hides the component chips and search, and does not flash them while the project kind loads.
- [ ] A non-Study project keeps them; a vitest covers both.

## Likely files

`pypsa-gui/frontend/src/layout/AppHeader.tsx`, `pypsa-gui/frontend/src/hooks/useOpenProjectKind.ts`.
