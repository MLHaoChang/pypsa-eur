<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Business-case engine and P&L

Status: needs-triage (blocked by 02, 03)
Type: task
Blocked by: 02, 03
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 (B1–B7), §5 and §6, and GK B conditions BC1–BC8 in [01](01-design-plan.md) ## Comments, first. Send the TDD plan to GK B before building.

## What to build

1. `services/finance/business_case.py`: `build_business_case(ic_case, inputs,
   it_energy)` → a `FinanceCase` with `counterfactual=()`, the IC's templates and
   assets, the revenue templates (R1, R2 and the R3 flag, per DCBC-SCOPE-1), the facility and IT-hardware
   `AssetFinance` with their depreciation classes, and the non-energy opex lines.
   Refusals B5 (`it_capex_missing`, `facility_capex_missing`).
2. New streams in `cashflow.STREAM_CLASS` (spec §5).
3. `pnl(result)`: the P&L by year (spec §6) from the engine's result; it
   reconciles to `project_post_tax` / `equity_post_tax` (B7).
4. KPIs: EBITDA margin, revenue per MW IT, break-even price (scalar solve on the
   revenue line, as `solve_ppa`).

## Tests (first, `tdd`)

- B3: the IC result and its hash are unchanged with business-case inputs present.
- B2: no business-case figure is read by the IC and vice versa.
- B6: pass-through with margin 0 leaves EBITDA unchanged.
- B7: P&L cash ties to the engine's series every year.
- Hand-checked 3-year case: revenue, D&A (straight-line, refresh), EBIT, tax, NI.
- A missing price gives `not_established`, never 0.
- `scripts/coord/mutants.py` on the new module; survivors listed in the PR.
