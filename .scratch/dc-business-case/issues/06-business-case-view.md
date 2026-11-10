<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Business-case view

Status: wontfix (superseded by ../../business-case/issues/09 (line-item editor) and 10 (P&L view); owner BC-ALL-1, 2026-11-10)
Type: task
Blocked by: 05
Gated: yes (UX; GK B for the figures shown)
Model tier: opus
Size: M

Read [the spec](../spec.md) §6–§7, `docs/agents/review-ux.md` and
`.scratch/project-workflow/design.md` first. Send the UI plan to UX before code.

## What to build

On the Investment tab, a "Business case" view beside the investment case (per
§9 Q3): an inputs editor following `FinanceInputsEditor.tsx`; the P&L table by
year (revenue, energy cost, other opex, EBITDA, D&A, EBIT, interest, tax, net
income, cash bridge); the KPI row; the break-even price. The two cases are
labelled by their basis ("versus the site today" / "the data centre on its own")
and are never shown as one total.
