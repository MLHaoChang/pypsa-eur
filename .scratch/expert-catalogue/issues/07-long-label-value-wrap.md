<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Card rows: a long label pushes the value onto the next line

Status: ready-for-agent
Type: task
Gated: yes (UX)
Depends on: DGS-EXTRAS-1 (#1765); lands with slice 03 (Expert panels and mode parity).

## What

In the Expert asset card's read view, a label too long to share the 300 px row with its value pushes the value onto its own line, aligned left. Every other row keeps the value right-aligned on the label's line. Seen on the slack Generator's four short-circuit rows: "Maximum/Minimum short-circuit power" and "R/X ratio at maximum/minimum short circuit". The shots are in `.scratch/expert-catalogue/shots/dgs-extras/gen-slack-*.png`. UX 26 ticketed it on #1765.

## Acceptance

- A long label wraps within its own column, and the value stays right-aligned on the row, as on the short rows. Alternatively, the card uses one stacked layout for every row, so no row is the odd one out. UX picks.
- The "(?)" help icon stays visible at 300 px for every row.
- Shots of the slack Generator card in light and dark, before and after.

## Comments
