<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 A planning → dynamics Study project shows the hub sidebar and gives no progress inside dispatch

Status: ready-for-agent
Blocked by: none (siblings: 06 fork kind, 07 run freshness)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W3 (shot `s2-panel`).
Model: sonnet. Review: UX (+ GK A if `gridspine/drivers/progress.py` changes).

## Problem

New project › Study (planning → dynamics, 336 h, k = 2):
- For about 3 s after opening, the panel is blank and Run study is disabled, with no hint (`GridspinePanel.tsx` ~150 `canRun`).
- The run took about 7.5 min, and the dispatch stage showed only "running 0/1" throughout (`gridspine/drivers/progress.py`, `routers/gridspine.py`).
- The project shows the full hub sidebar with red section dots and an enabled Run LOPF (`layout/sidebar/SectionRows.tsx`), although this project kind has no network canvas.

## User-visible impact

The user does not know whether the study is stuck, and is offered sections and a Run that do not apply to this project.

## Acceptance criteria

- [ ] While the panel loads, it shows a loading state, and Run study shows why it is disabled.
- [ ] Dispatch reports finer progress, for example per solved period or per hour block, with elapsed time, so the bar moves at least once a minute on the 336 h case.
- [ ] On a Study project, the sidebar shows only the sections that apply to the kind (from `project_kind`), and Run LOPF is hidden.
- [ ] End to end in the real app: New project › Study (336 h, k = 2) › Run study. The progress moves within dispatch. On completion the bundles list shows the selected hours, and a bundle downloads.

## Likely files

`pypsa-gui/frontend/src/pages/GridspinePanel.tsx`, `pypsa-gui/frontend/src/layout/sidebar/SectionRows.tsx`, `gridspine/drivers/progress.py`, `pypsa-gui/backend/routers/gridspine.py`.

## Comments
