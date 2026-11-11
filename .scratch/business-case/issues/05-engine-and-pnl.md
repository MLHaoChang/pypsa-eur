<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Business-case engine, P&L and KPIs

Status: in progress (claude/bc-05-engine-and-pnl; GK B plan GO 2026-11-11)
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

## Comments

### 2026-11-11: GK B (48) plan GO, conditions C1–C9

Answers Q1(a) to Q5(a), as recommended:
- Q1: convert capex money year to base money with the capex class's own rate. Test a money year before and after base. A missing rate is not established, never 0 %.
- Q2: capex after COD is a P5 stage. A start_year past the horizon is refused `outside_horizon`, and the staged-incentive refusal fires through the BC path.
- Q3: a non-integral life leaves tax and every post-tax KPI not established, by name.
- Q4: break-even is on project_post_tax NPV at wacc_nominal; working capital is in the bridge only.
- Q5: there are no DC stream rows.

Conditions:
- C1: fresh copies of `depreciation_class_by_asset`, `replacement_capex` and `asset_cod`. BC1 asserts the IC's dicts are unchanged by identity and contents.
- C2: a capex line named like an IC asset, or any line named like an IC template key, is refused `name_duplicate`. Test each clash.
- C3: a share whose base holds a share is refused at build. share_of:capex on opex is base money escalated by the line's class; test it with escalation ≠ 0.
- C4: a linked line on a quantity the IC ledger prices is refused `double_count`, pinned by one test. "No VoLL anywhere" stays.
- C5: no non-finite KPI. A margin with revenue 0, revenue per MW with no capacity, and an IRR with no sign change each read n/a or not established. The KPI output states the capacity set and its unit.
- C6: the BC5 tie tolerance is 1e-6 × max(|revenue|, 1). The EBITDA = net_total tie has one order-sensitive case (#1249 M5).
- C7: break-even scales the named line only, and the other lines are byte-equal across trials. No sign change in the bracket reads `break_even_not_bracketed`. State the brentq tolerance, and test the re-run |NPV| ≤ tol × the line's revenue.
- C8: STREAM_CLASS gets new rows only. A frozen-copy pin covers the old rows and ENERGY_BILL_STREAMS, and the PR lists the readers.
- C9: the IC digests are unchanged by the BC import and by a BC build.
- Mutants: mutants.py, plus hand mutants on the clashes, the share cycle, the Q1 sign, refresh ≤, DSCR n/a and the KPI n/a guards. The killing tests are listed in the PR.
