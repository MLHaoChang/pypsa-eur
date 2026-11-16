<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 17 — Per-row reasons for a P&L row that is not established

Status: needs-triage
Gated: GK B
Type: enhancement
Parent: business-case 10 (the P&L and KPI view), GK B W1 (F4)

## Problem

The P&L view (10) explains a row that reads "Not established" from a client-side
map, `ROW_NEEDS` in `pypsa-gui/frontend/src/pages/results/investment/businessCaseReport.ts`.
It says only what the row is built from ("Not established. It needs EBIT and tax."),
never which input is missing for this case. The served row list,
`businessCasePnlRows.json`, is pinned to `report_body`'s `pnl.rows` by a backend
test, and a vitest pins the `ROW_NEEDS` keys to it.

## What to build

`report_body` serves a reason per row that is not established, naming the missing
input (a line, a linked output, an IC figure), and the view shows that reason in
place of the `ROW_NEEDS` text. The reason is a display key: it moves no digest or
staleness key.

## Acceptance

- Each not-established row in the stored report carries a reason that names the
  missing input; a backend test covers each row kind.
- The view shows the served reason and falls back to `ROW_NEEDS` only for a report
  stored before this change.
