<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Business-case engine and P&L

Status: wontfix (superseded by ../../business-case/issues/05 (engine and P&L); owner BC-ALL-1, 2026-11-10)
Type: task
Blocked by: 02, 03
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 (B1–B8), §5 and §6, and GK B conditions BC1–BC8 in [01](01-design-plan.md) ## Comments, first. Send the TDD plan to GK B before building.

## What to build

1. `services/finance/business_case.py`: `build_business_case(ic_case, inputs,
   it_energy)` → a `FinanceCase` with `counterfactual=()`, the IC's templates and
   assets, the revenue templates (R1, R2 and the R3 flag, per DCBC-SCOPE-1), the facility and IT-hardware
   `AssetFinance` with their depreciation classes, and the non-energy opex lines.
   Refusals B5 (`it_capex_missing`, `facility_capex_missing`).
2. New streams in `cashflow.STREAM_CLASS` (spec §5).
3. `pnl(result)`: the P&L by year (spec §6), built from the per-stream operating
   ledger (`result.op` by `STREAM_CLASS` stream), never from op_inc's
   revenue/costs, which standalone mode clips to the net (`engine.py:183-185`; spec
   B8, GK B BC4). EBITDA ties to `net_total` per year; net cash reconciles to
   `project_post_tax` / `equity_post_tax` (B7).
4. KPIs: EBITDA margin, revenue per MW IT, break-even price (scalar solve on the
   revenue line, as `solve_ppa`).

## Tests (first, `tdd`; the TDD plan goes to GK B before building)

- BC1: the BC result has its own hash domain; a pin test that the IC report, its
  staleness hash and every other IC/manifest digest are byte-identical with the BC
  code present (spec B1, B3).
- B2: no business-case figure is read by the IC and vice versa.
- BC2: pass-through with margin 0 leaves EBITDA unchanged; EBITDA margin and revenue
  per MW IT are shown both gross and net of pass-through.
- BC3: working capital is absent from EBITDA, EBIT, tax and net income (pin); with a
  0 % discount rate the Δ working capital sums to 0 over life.
- BC4: P&L lines come from `op` by stream; EBITDA ties to `net_total` per year,
  including a year with net > 0 where gross revenue ≠ net (a P&L read from op_inc
  fails it).
- BC5: the tax line is the engine's tax, no second computation; each year ties to
  `project_post_tax` and `equity_post_tax` within a stated tolerance (1e-6 of the
  year's revenue), with a mutant that breaks the tie.
- BC6: a solve with unserved IT energy lowers revenue by exactly its billable share;
  VoLL appears nowhere in the BC (no line, input or text prices unserved energy).
- BC7: fail-closed typed refusals at the catching site for a missing revenue price,
  `it_capex_missing` and `facility_capex_missing`, never 0 or a default; units are
  real (kW-month vs kW-year, GPU-h).
- BC8: the break-even solve brackets its root and, with none in the bracket, refuses
  with a type, never extrapolates; with no debt DSCR reads n/a, never inf or 0.
- GK A notes: R1 GPU-hours = served IT MWh × 1000 ÷ kW per GPU × u_sold (no second
  u(t)); R3 metered pass-through uses PUE(T(t)) per snapshot on served IT; a units
  test pins both.
- Hand-checked 3-year case: revenue, D&A (straight-line, refresh), EBIT, tax, NI.
- `scripts/coord/mutants.py` on the new module; survivors listed in the PR.

## Comments

### 2026-11-10: review of the spec PR #1316

- GK B (40) F1/F2: B8 (P&L from `op` by stream, never op_inc) and the explicit
  BC1–BC8 tests above.
- GK A notes: the R1 GPU-hour units (× 1000, `u_sold` the sold share, never a second
  u(t)) and the R3 PUE(T(t)) per snapshot on served IT, both pinned by a units test.
