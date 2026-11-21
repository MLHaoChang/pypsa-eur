<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 A viewer still sees an enabled "Run again", and is told "another user is editing"

Status: needs-triage
Blocked by: 05 (the frontend has no `my_access` yet)
Source: E2E walk for 02 (PR #1806). The bug is on master.

## Problem

02 makes the backend refuse a viewer's run, but the UI still offers it:

- `pypsa-gui/frontend/src/pages/decision/RunStep.tsx:34`: "Run again" shows whenever a run exists and is not running, and is disabled only by `busy`.
- The same holds for `pages/results/LoadFlow.tsx:62-65` (`loadflow-run-again-btn`) and the requeue action in `pages/SolveQueuePanel.tsx:311-330`.
- Nothing in the frontend reads `my_access` yet (05). The read-only pattern in use is `readOnly`/`readOnlyReason` via `evaluateMutation` (`pages/mapLayoutStore.ts:215-218`).

## Reach today

On master, decision studies run only in local mode (`services/study/__init__.py:17`), so RunStep's "Run again" never meets a viewer yet. LoadFlow and the solve queue do run in multi-user mode.

## Reproduced (master e63ad7607, multi-user mode, 2026-11-21)

A viewer (`project_memberships.role='viewer'`, set in the DB because no UI or route sets it yet; see 05) opens a solved project and goes to Results → AC power flow & grid → Load flow:
- At project open, `POST /api/projects/{name}/lock` is refused with 403 `project_view_only`. The app then reads this as a lock held by someone else: the banner says "Read-only: Another user is editing this project", but the lock is null.
- `loadflow-run-again-btn` "Run again" is enabled. Clicking it sends no request and shows the toast "Read-only: another user is editing this project." So the backend gate is never reached, and the reason given is wrong.
- Shots: `../shots-06-08/vr08-01-viewer-run-again.png` and `vr08-02-viewer-run-again-refused.png`.

## Acceptance

- A viewer's read-only banner and toasts give the view-only reason ("ask the owner for edit access"), not "another user is editing".
- For a viewer, every run and re-run control is disabled with the view-only reason, and the backend refusal stays as the safety net.
- Shot as a viewer in the real app.
- Probably folds into 05; triage decides.
