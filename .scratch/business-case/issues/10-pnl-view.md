<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — P&L and KPI view

Status: ready-for-agent (blocked by 08; Opus; UX plan first, GK B for figures)
Type: task
Blocked by: 08
Gated: yes (UX; GK B for the figures)
Model tier: opus
Size: M

Read [the spec](../spec.md) §7, the DC [06](../../dc-business-case/issues/06-business-case-view.md)
it replaces and `docs/agents/review-ux.md` first.

## What to build

Below the line table: the run button, the P&L table by year (revenue by category,
energy cost, other opex, EBITDA, D&A, EBIT, interest, tax, net income, cash
bridge), the KPI row (gross and net of pass-through), break-even on a chosen
revenue line, `not_established` reasons. Both cases labelled by basis and never
shown as one total.

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-14: plan GO (GK B 56, UX 20/21)

UX conditions as on 09 (6: not-established KPIs read "Not established: <reason>"; 7: one
out-of-date mark per card; 8: the stage status region holds only stage words; 5: visible
disabled reasons on Run). GK B: Q1 = A (no Interest line), Q2 = A (`stream_class` in the
report body), and:
- F1 no derived totals: every total and subtotal is a `pnl.rows` value; the client never
  sums streams, not even per group; a group shows a subtotal only if the report serves
  it. A vitest with a mixed-sign stream pins that the total shown is `rows.revenue`.
  Streams are signed as `op.lines` (revenue +, cost −); `rows.revenue`/`rows.opex` clip
  per line, so streams need not add up to them.
- F2 `stream_class` added to `report_body` only, every existing field byte-equal (a test
  over the existing report fixtures); a report without it shows its streams under
  "Other" with a note, never a guessed class; a test that every stream `pnl()` can emit
  (CATEGORY_STREAM, STREAM_CLASS, BC_STREAM_CLASS) has a class.
- F3 no Interest line; "Net income is before financing (unlevered)" beside it;
  debt_service in the equity bridge as served.
- F4 a None row shows its reason, never 0; terminal is hidden only when it is exactly 0,
  a None terminal shows as not established.
