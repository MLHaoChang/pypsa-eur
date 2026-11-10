<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Data-centre business case: a standalone project P&L beside the investment case

Status: needs-triage (design; owner scope answers pending, §9)
Owner request: 2026-11-10 (coordinator brief, session `session_01G7qCujw1M4yEuv53K2qCpG`)
Research: [research.md](research.md)
Reviewers: GK B (finance engine, lead), GK A (DC load and engine parts), UX (the view, slice 06)

## 1. The ask

The tool already takes a data-centre load profile in and gives the energy side out:
CapEx, OpEx, backup, assets, grid compliance, the PowerFactory export, LCOE and an
investment case (IC). The owner asks for the **business case**: link the load to a
revenue stream (for example a user-entered price of compute per hour), add the
costs the energy model does not see (IT hardware, the building, staff,
depreciation, tax) and report the project's bottom line: revenue, EBITDA,
depreciation, EBIT, tax, net income and cash, with NPV, IRR, payback and DSCR.

## 2. What exists (verified on `f6e2c8aa2`)

| Piece | Where | What it gives the business case |
|---|---|---|
| IC engine | `services/finance/engine.py` `run_case` | timeline → operating cash → tax → debt → cash series and metrics; one engine for both bases |
| Ledger streams and escalation | `services/finance/cashflow.py:32-45` `STREAM_CLASS` | every cash line has a stream and an escalation class; new streams are rows here |
| Standalone mode | `engine.py:172-182` | with `counterfactual=()` the incremental net **is** the total net (the "SAM" basis, `engine.py:373-374`); the business case needs no second engine |
| EBITDA and cash | `engine.py:307-315` | `ebitda = inc_net + terminal`; project, equity, CFADS, tax, credits series |
| Depreciation and tax | `tax.py` (`DepreciationClass`, straight-line, declining balance), `tax_layers.py` | per-class depreciation and loss rules; IT hardware and building become two more classes |
| Debt | `debt.py` | tranches, DSCR, DSRA; reused unchanged |
| Revenue by stream | `revenue_streams.py` `revenue_by_stream` | the per-stream table the P&L groups |
| Chat tool | `chat_tools.py:2867` `run_investment_case`, route `POST /api/results/investment_case` (`routers/results.py:2239`) | the pattern the business-case tool follows |
| Results view | `frontend/src/pages/results/investment/InvestmentCaseView.tsx`, `RevenueByStream.tsx`, `FinanceInputsEditor.tsx` | the tab the business case sits beside |
| DC template | `project_templates/eh_templates.py:1502-1516` `_staged_commercial_and_finance` | cost-only `single_owner` value flows; IC is versus "the site as it stands today" (`case.py:584-590`) |
| DC load | `models/flex_archetypes.py:140` `DataCentreLoadSpec`; `services/dc_load_profile.py` | phases (`it_mw`, cumulative; `rack_kw`), `utilisation`, PUE(T); facility load = IT MW × u(t) × PUE(T) |
| DC revenue today | `.scratch/datacenter-grid-readiness/issues/06-flex-market-revenue.md` | reserve/ancillary settlement only (rules L6–L9) |
| Guided | `harness/workflows/investment-decision.md`; study-harness profile 10 (`.scratch/study-harness/issues/10-profile-datacenter.md`) | where a "Is this data centre worth building?" question plugs in |

Two facts the survey did not state and the design depends on:

1. **`it_load` is not the IT load.** `it_load` carries the *critical share* of the
   facility load (`eh_templates.py:626`), cooling carries the rest. The IT energy
   that earns revenue is IT MW × u(t), which `dc_load_profile.utilisation` already
   computes from the spec; facility ÷ PUE(T) gives the same number. Revenue reads
   the spec, never the network's loads.
2. **DC curtailment never cuts IT.** The curtailable load sits on the cooling bus
   only (`services/solver/dc_curtailment.py:4-10`), so curtailment does not reduce
   billable compute. Unserved energy (the involuntary slack) can.

There is no income statement anywhere today (`grep "income statement\|P&L"` finds
none in `services/`).

## 3. The central design: two bases, one engine, never summed

| | Investment case (exists) | Business case (new) |
|---|---|---|
| Question | Is this **energy design** worth it versus the site as it stands today? | Is this **data centre** worth building and running? |
| Basis | incremental: owner cash − counterfactual (`case.counterfactual`) | standalone: the project's total cash, `counterfactual=()` |
| Revenue | settlement lines only (reserve, export; DC 06 L9) | compute revenue + the same settlement lines |
| Energy cost | the difference against today's bill | the whole energy bill (the IC's total `op`, not `op_incremental`) |
| Capex | the energy assets | the energy assets + building/M&E + IT hardware (+ refresh) |
| Opex | the energy assets' FOM/VOM | + staff, maintenance, insurance, property tax, network, SG&A, other |
| Output | NPV/IRR/payback/LCOE of the increment | P&L by year + NPV/IRR/payback/DSCR/EBITDA margin/revenue per MW of the project |

**How they coexist without double counting.**

- **B1. One ledger, two cases.** `services/finance/business_case.py` builds the
  business-case `FinanceCase` from the IC's own case (`dataclasses.replace`):
  the same templates and assets, `counterfactual=()`, plus the facility templates
  and facility assets of §4–§5. The energy lines enter once, from the same
  ledger; nothing is copied or re-priced.
- **B2. Never added together.** The two results are separate objects, separate
  hashes and separate views. No figure of one is an input of the other. The
  business-case NPV is not "IC NPV + something".
- **B3. Revenue lines are business-case only.** Compute, colocation and
  pass-through revenue lines exist only in the business-case templates; the IC
  never sees them (they would otherwise appear on the actual side with no
  counterfactual counterpart and inflate the increment). A test pins it.
- **B4. Not in the LP (L9).** Compute revenue is a post-solve settlement line from
  the stated IT series × price; it is never in the objective, never changes
  dispatch (`changes_dispatch=False`), and is never a value of lost load
  (owner decision 2026-10-15, `breakeven.py:11`). Unserved IT energy earns
  nothing and is reported as lost revenue *quantity*, not priced into the LP.
- **B5. Revenue never without capex (L8 analogue).** Each revenue model names the
  capex it needs (§4 table). A GPU-hour revenue line without IT hardware capex is
  refused (`business_case_refused:it_capex_missing`); a colocation line without
  facility capex likewise (`facility_capex_missing`). Capex is priced exactly once:
  IT hardware is owned by the project only under the compute models.
- **B6. Power pass-through nets, never doubles.** When the tenant pays the energy
  bill (pass-through), the pass-through revenue line equals the stated share of
  the energy-bill streams (`ENERGY_BILL_STREAMS`, `report.py:600`) × (1 + margin);
  the bill itself stays as cost. Gross presentation (revenue and cost both shown),
  EBITDA effect = margin only. A test pins EBITDA(pass-through, margin 0) =
  EBITDA(no pass-through) − bill + bill.
- **B7. Reconciliation.** P&L net cash ties to the engine's `project_post_tax` /
  `equity_post_tax` series year by year (the report's existing reconciliation,
  WP4.6b B2); the P&L is a presentation of the engine's numbers, never a second
  calculation.

## 4. Revenue: how the load profile drives it

All prices are real, in the case's base-year money, escalated by their own
indexation (contract class) or the `export` class; each is **user-entered with
no default** (§9 Q4), because prices are the case.

| Model | Quantity from the load | Line | Needs capex | Research |
|---|---|---|---|---|
| **R1 GPU / compute-hour** | billable GPU-hours = IT MWh(t) × u_sold ÷ kW per GPU (all-in server kW per GPU, so racks, CPUs and networking are inside) | GPU-hours × price per GPU-hour; reserved share at the contract price, the rest at on-demand × sold utilisation | IT hardware (owned) + facility | research §1a |
| **R2 Colocation / wholesale lease** | contracted IT kW per phase (the spec's `it_mw`) × lease-up curve | kW × price per kW-month × 12, escalator, take-or-pay (independent of u(t)) | facility only (tenant owns IT) | research §1b–c |
| **R3 Power pass-through** | the energy-bill streams | share × bill × (1 + margin), or metered IT kWh × PUE × rate | none beyond R2 | research §1d |
| **R4 Other services** | contracted kW or rack count (`it_mw` ÷ `rack_kw`) | per kW-month or per rack-month (cross-connects, remote hands) | none | research §1e |

- **IT energy series.** IT MWh(t) = Σ phases in service IT MW × u(t) ×
  weight(t) − unserved IT share, per period, from `dc_load_profile` (GK A owns the
  read). It is the same series the facility load was built from, so the energy
  bill and the revenue see one load.
- **Lease-up.** A per-phase ramp (share of contracted kW billed in years 1..k,
  default 100 % from COD unless stated). Under R1, utilisation is already in u(t);
  a separate "sold share" says how much of the utilised capacity is sold.
- **SLA credits.** A stated share of revenue (default 0, user-entered), a negative
  revenue line, so availability risk is visible; it is not derived from the
  adequacy result in v1 (that would price lost load).

## 5. Costs beyond energy

| Line | Stream / class | Basis | Depreciation |
|---|---|---|---|
| Building, shell and M&E fit-out | asset `dc_facility`, capex | per MW IT, phased with the COD phases | straight-line, user life (default from research) |
| IT hardware (R1 only) | asset `it_hardware`, capex | per GPU or per MW IT, refreshed every *n* years | straight-line over its life (§9 Q1) |
| Staff | `fom` (`opex`) | per MW-year or FTE × cost | — |
| Maintenance (non-energy) | `fom` | share of facility capex per year | — |
| Insurance | `other` (`opex`) | share of capex per year | — |
| Property tax | `tax` stream is levies on the bill; a new `property_tax` stream, class `opex` | share of asset value | — |
| Network / bandwidth | `other` | per MW-year | — |
| SG&A | `other` | share of revenue | — |
| Working capital | new: a change-in-working-capital line | receivable days on revenue | — |

New streams (`compute_revenue`, `colocation_revenue`, `power_passthrough`,
`services_revenue`, `sla_credit`, `property_tax`, `working_capital`) are rows in
`STREAM_CLASS`; revenue streams are contract or `export` class, cost streams
`opex`. Each new non-energy cost has a researched default with its source and
year, marked assumed (as G2's region-tagged defaults); prices of revenue have
none.

## 6. Outputs

- **P&L by year**: revenue by model; energy cost (the bill and the energy assets'
  O&M); non-energy opex by line; **EBITDA**; depreciation by class (energy assets,
  facility, IT hardware); **EBIT**; interest; tax; **net income**; then the cash
  bridge (+ D&A − capex − Δ working capital + debt flows) to the engine's cash
  series (B7).
- **KPIs**: EBITDA margin, revenue per MW IT per year, NPV (project and equity),
  IRR, payback, min/avg DSCR, the IC's LCOE carried as the energy cost per MWh,
  and **break-even prices**: the GPU-hour or kW-month price at NPV = 0 (one
  scalar solve on the revenue line, as `solve_ppa` does).
- **Not established, never zero**: a missing price, life or capex gives
  `not_established` with the reason (plan C12 rule).
- **Workbook**: a "Business case" sheet, from the stored business case else
  `not_established` with "run the business case" (as RW-KPI-1 for the IC).

## 7. Workflow

- **Results**: a "Business case" view beside the investment case on the
  Investment tab (owner choice, §9 Q3): inputs (revenue model, prices, facility
  and IT capex, opex lines), then the P&L table and KPIs. UX reviews it.
- **Chat**: tool `run_business_case` (route `POST /api/results/business_case`),
  same lifecycle as `run_investment_case`; `get_business_case` reads the stored
  result. Catalogue and route-parity declarations per the steward rules.
- **Guided**: a second question in `investment-decision`, "Is this data centre
  worth building?" (`dc_business_case`): asks the revenue model and the prices
  with `ask_user` (never invented), offers researched defaults for the cost
  lines, runs IC then business case, and gives the verdict with the break-even
  price as the driver that could flip it. It plugs into study-harness profile 10
  once that lands (dependency, not this package's files).

## 8. Out of scope (v1)

- Valuing lost load or downtime in the LP (owner decision 2026-10-15).
- A GPU/server catalogue with vendor specs (unless §9 Q1 picks it).
- Market-price forecasting for compute; prices are user-entered.
- REIT metrics (FFO/AFFO) and lessee/lessor lease accounting (ASC 842 / IFRS 16)
  beyond a straight revenue line; research notes them.
- Changing the IC: its basis, numbers and hash stay as they are (a pin test).

## 9. Owner scope choices

Pending; sent to the coordinator 2026-11-10. Answers are recorded here.

- **Q1 IT hardware capex**: pending.
- **Q2 Revenue models first**: pending.
- **Q3 Where the P&L lives**: pending.
- **Q4 Price defaults**: pending.

## 10. Slices (one PR each, PR-SIZE-1)

| # | Slice | Reviewer | Blocked by |
|---|---|---|---|
| 01 | [Design plan](issues/01-design-plan.md) (this spec) | GK B, GK A | — |
| 02 | [Business-case inputs model](issues/02-inputs-model.md) | GK B | 01 |
| 03 | [IT energy series for revenue](issues/03-it-energy-series.md) | GK A | 01 |
| 04 | [Business-case engine and P&L](issues/04-engine-and-pnl.md) | GK B | 02, 03 |
| 05 | [Route, chat tool and workbook sheet](issues/05-route-tool-workbook.md) | GK B | 04 |
| 06 | [Business-case view](issues/06-business-case-view.md) | UX, GK B | 05 |
| 07 | [Guided question](issues/07-guided-question.md) | GK B | 05, study-harness 10 |
