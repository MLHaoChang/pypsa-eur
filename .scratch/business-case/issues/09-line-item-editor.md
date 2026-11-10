<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Line-item table editor

Status: needs-triage (blocked by 07, 08; UI plan to UX first)
Type: task
Blocked by: 07, 08
Gated: yes (UX; GK B for the figures)
Model tier: opus
Size: M

Read [the spec](../spec.md) §4, §7, `docs/agents/review-ux.md` and
`.scratch/project-workflow/design.md` first. Send the UI plan to UX before code.

## What to build

In the "Business case" view on the Investment tab, the line table: one row per
line, the S columns as selects (with only the units the source allows), the T
columns as typed cells, the per-year grid as an expanding row with paste from a
spreadsheet (BCA-SCOPE-1 Q2), "add line" with the offered rows, "apply preset", the locked
"from the energy model" rows (06) beside the table, provenance badges (stated /
assumed with source on hover), revenue cells empty and marked not established, the
hint text per BCA-SCOPE-1 Q1, and every refusal shown on its row and column (G9).

## Tests

Vitest for the table (select options per source, refusal placement, paste);
Playwright for apply-preset → edit → save; UX shots.
