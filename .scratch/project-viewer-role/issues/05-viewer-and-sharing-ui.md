<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 Sharing and the viewer role in the UI: role picker, read-only hints, Members only for those who manage

Status: done
Blocked by: 01 (PR 2a #1718: member role route and `my_access`)
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W4 (shots `members`, `member_save`, `workspace`).
Model: sonnet. Review: UX + GK B.

## Problem

01 gives the backend a viewer role and `my_access`. The walk found that the frontend has none of it:
- The members dialog (`layout/AssignMembersDialog.tsx`) ticks users and has no role picker, so no viewer can be made from the UI.
- Nothing reads `my_access`, so a viewer would see enabled edit controls and learn of the refusal only on Save.
- A member who cannot manage membership sees an enabled "+ Members"; the 403 appears only after Save.
- Sharing lives only in Expert (Project menu › Workspace panel; `layout/Sidebar.tsx` ~976 `!guided`), so a Guided owner cannot share.
- The Workspace panel still says "The backend holds one active network per process… share that slot" (`pages/WorkspacePanel.tsx`), which no longer holds now that the active project is per session (`services/active_project.py`).
- `test_viewer_role_gate.py` covers the routes, but no browser check covers the viewer's screen.

## User-visible impact

Owners cannot give read-only access. Viewers and members are offered actions that then fail.

## Acceptance criteria

- [ ] The members dialog sets each member's role (editor / viewer) through 01's route, and shows who the owner is.
- [ ] With `my_access` = viewer: Guided and Expert inputs, Run, Save, import and asset edits are disabled, each with one shared plain reason ("You can view this project; ask <owner> for edit access"). Results, reports and downloads stay available.
- [ ] "+ Members" shows only to users who may manage membership.
- [ ] A Guided owner can open sharing from the project header without switching to Expert.
- [ ] The stale shared-slot text is removed or corrected.
- [ ] End to end in the real app: the owner shares a project with user B as viewer. B opens it, sees the read-only reason, cannot edit in Guided or Expert, and can open results and download the report. The owner changes B to editor, and B can now edit after a reload. A Playwright script for this journey joins the CI browser scripts.

## Likely files

`pypsa-gui/frontend/src/layout/AssignMembersDialog.tsx`, `pypsa-gui/frontend/src/pages/WorkspacePanel.tsx`, `pypsa-gui/frontend/src/layout/Sidebar.tsx`, the project header, a `useMyAccess` hook beside the project store, a new `frontend/scripts/smoke-viewer-role.mjs`.

## Comments
