<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Business-case engine, P&L and KPIs

Status: ready-for-agent (blocked by 02, 04; Opus; GK B TDD plan first)
Type: task
Blocked by: 02, 04
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 (B1–B8 via the DC spec, G1, G8, G-C1, G-C6, G-C7) and
§7, the DC [04](../../dc-business-case/issues/04-engine-and-pnl.md) it replaces, and
GK B conditions BC1–BC8 (DC 01 ## Comments) and G-C1..G-C8 ([01](01-design-plan.md)
## Comments). Send the TDD plan to GK B before building.

## What to build

1. `services/finance/business_case.py`: `build_business_case(ic_case, inputs,
   outputs)` → `FinanceCase` with `counterfactual=()`, the IC's templates and
   assets, and each line compiled per G1 (revenue/opex → `TemplateLine`, key = line
   name, stream from category, `money_year` from the line; capex →
   `AssetFinance` + `DepreciationClass`, refresh = replacements). Linked
   quantities come from 04.
2. The category streams as `STREAM_CLASS` rows (spec §3 table).
3. `pnl(result)`: P&L by year from `op` by stream (BC4), never op_inc; EBITDA ties
   to `net_total`; net cash ties to `project_post_tax` / `equity_post_tax` (BC5).
4. KPIs: EBITDA margin gross and net of pass-through (BC2), revenue per unit of
   capacity, NPV, IRR, payback, DSCR (n/a without debt), break-even on a named
   revenue line (G8 on the BC8 rule).
5. The BC hash over `preset_id@version`, lines, `receivable_days` and the bound
   solve (G-C6).

## Tests (first, `tdd`)

BC1–BC8 as the DC 04 lists them, generalised to typed lines on `eh_site`; plus: a
test per category that it has its `STREAM_CLASS` row; a pin that the IC's stream
set and results are unchanged; a line with a non-base `money_year`; a `share_of`
line on revenue and on capex; refresh capex appears in the right years.

## Not in this slice

Double count, lost production and reference purchases (06), presets (07).

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.
