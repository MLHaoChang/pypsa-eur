<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — The command palette's rows as a listbox

Status: needs-triage
Type: task
Found by: UX 2 on expert-catalogue 02a (#1804), accepted there as palette-wide follow-up

## What

Every ⌘K palette group renders its rows as plain buttons, and the highlight is component state the screen reader never hears. Make the result list a `role="listbox"` with `role="option"` rows (`aria-selected`, `aria-disabled` on rows that cannot run), the input a `combobox` with `aria-controls` and `aria-activedescendant` pointing at the highlighted row, and the group labels `role="group"` with `aria-label`.

## Where

`pypsa-gui/frontend/src/components/CommandPalette.tsx` (all groups, not only Parameters).

## Acceptance

- Arrowing through rows announces each row and whether it can run (screen reader check, plus a vitest on the roles and `aria-activedescendant`).
- Mouse, Enter and Esc behave as today; the existing CommandPalette tests pass unchanged.
