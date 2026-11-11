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
