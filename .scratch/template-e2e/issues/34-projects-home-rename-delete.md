<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 34 Projects home cards offer only Open: delete is reachable only as Expert › Scenarios › "Delete this scenario"

Status: ready-for-agent
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W1 (shot `p01-projects`).
Severity: friction
Model: sonnet. Review: UX.

## Problem

On /projects each card offers Open only. Rename works from the project header, but to delete a project the user must open it, switch to Expert, go to Scenarios and use the trash icon (`pages/ScenariosPanel.tsx` `deleteMut` and the ConfirmDialog, ~807–850).

## Acceptance criteria

- [ ] Each card has a menu: Open, Rename, Duplicate, Delete. Delete reuses the Scenarios confirm text ("This removes its files from disk") and route. Items the user may not do (not the owner, or a viewer once `project-viewer-role/05` lands) are hidden.
- [ ] Deleting the project that is open in the session closes it cleanly; no "Unnamed Network" is left.
- [ ] End to end in the real app: rename a project from its card, duplicate it, and delete the copy, all from /projects.

## Likely files

`pypsa-gui/frontend/src/pages/ProjectsHomePage.tsx`, `pypsa-gui/frontend/src/pages/ScenariosPanel.tsx` (reuse the delete mutation).

## Comments
