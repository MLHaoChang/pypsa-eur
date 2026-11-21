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

- 2026-11-21, builder (session_01G7qCum3Y2tCVEL41nHaCmC): measured the 336 h, k = 2 study at 392 s. Dispatch takes 351 s of that: 3 windows, finished at 28 s, 347 s and 351 s, so window 2 is one MILP of 319 s. The bar cannot move once a minute without changing the solve. The owner was asked (a) per-window progress plus a clock, (b) plus the HiGHS gap, or (c) shorter windows; (a) is built.
- 2026-11-21, GK A plan GO with conditions P1–P8 (gridspine progress record):
  - P1: `on_window` stays "after a window is solved"; the start goes through a separate signal (`on_start(n_windows)` → `Progress.begin`), tested so that the total before window 1 equals the number of `on_window` calls.
  - P2: the record is best-effort. A failed write is logged once and never fails the run. The tmp file is unique and in the same directory. A test shows a failing write leaves the outputs unchanged.
  - P3: the throttle never drops the last state (done == total) or a `begin`.
  - P4: stale records are removed at run start and bound to the run by a nonce (`progress.run`), never by a timestamp alone. Tested with a newer foreign record.
  - P5: disk and error artifacts win. The record is overlaid only on the running stage of the same name. `done` is clamped to 0 ≤ done ≤ total, and the record never completes a stage. A corrupt file reads as no record.
  - P6: outdir consumers checked. The record is removed when the run ends (done or failed). A byte-identity test compares outputs with the record, with failing writes and without it.
  - P7: `Progress()` without a record writes nothing, and its callback events are unchanged.
  - P8: mutants on progress.py and status.py, with the survivors argued in the PR.
- 2026-11-21, UX 3 plan GO with conditions: a visible reason line; no flash while the kind loads; the other Run LOPF entry points agree; Expert never shows less than Guided; shots in Guided and Expert.
