# Design — Edge Investment Case: commercial layer, project finance, participants and flexibility archetypes (v1)

**Status:** design, revised 2026-09-26 after an independent adversarial review (verdict PASS WITH CONDITIONS; all spec-level conditions applied in this revision, plan-level conditions carried into §13/§16). Companion research: `docs/superpowers/notes/2026-09-26-edge-client-feature-benchmark.md`, `docs/superpowers/notes/2026-09-26-competitive-landscape-edge-financial-modelling.md`.
**Decided with the product owner on 2026-09-26** (two question rounds; answers pinned in §2).

**Goal.** Turn the reliability-first Energy Hub design engine into a tool that supports
**investment decisions at the grid edge**: a hyperscaler, data-centre or energy-hub developer,
IPP or their consultant enters the client's load, the grid connection *as a contract*, the
tariffs, contracts and markets the assets are exposed to, the parties who pay and are paid, and
the financing and tax structure — and receives, beside the existing certified reliability
design, per-participant cashflows, project and equity returns, debt metrics, tax-equity
allocations, the PPA price that clears a target return, and a defensible revenue number that
does not rely on perfect foresight. Everything is provenance-flagged in the style of the EH
`ReferenceDesignReport`. The system-scale capability underneath (PyPSA-Eur) stays intact.

**Positioning (pinned).** Edge-first, system-capable. The tool is not trying to be PLEXOS; it is
trying to be the only edge tool that does Gridcog-class commercial and financial modelling *and*
probabilistic adequacy, N-1 redundancy, grid-strength gates and sector coupling in one model.

---

## 1. What already exists (reuse, do not rebuild)

| Capability | Role in this design |
|---|---|
| PyPSA network, multi-period vintages, per-vintage bounds | Physical model; grid arriving in year N; augmentation vintages |
| `extra_functionality` wrappers (`services/solver/objective.py`: CAPEX budget, curtailment cost, objective scale) | Pattern for the peak-demand-charge variables and group-capacity constraints |
| `solve_strategy="rolling"` (PyPSA `optimize_with_rolling_horizon`) and the `myopic.py` driver in `services/solver/` | **Pattern only** for the realistic-controller mode (§9): PyPSA's rolling helper has no per-window input hook and is forbidden with multi-period, so realism gets its own driver beside `myopic.py` |
| `periodized_costs.py` (annuity, PV factors, overnight ↔ annualised) | Source of asset capex, build year, lifetime for the cashflow engine |
| `services/results/asset_economics.py`, `cost_breakdown.py`, `objective_decomposition.py` | Physical quantities and reconciliation; the finance layer consumes, never re-derives, dispatch |
| EH archetype packs (`models/energy_hub.py`), `EHStudyRunner`, `ReferenceDesignReport`, completeness enum `ok | not_established | skipped`, `assumptions_hash` | Pattern for jurisdiction/market packs, flexibility archetype builders and the `InvestmentCaseReport` |
| Adequacy stack (ENS cap, COPT, MC LOLE, redundancy, DtC, SCR gate) | Unchanged; the investment case sits beside the reliability certificate |
| Campaign budget, solve queue, scenario tree (`scenario_type`), Compare | Substrate for the scenario matrix and finance-only re-evaluation |
| User time-series store, snapshot manager with `freq`, representative weeks | 15-minute snapshots and interval meter data |
| Trustworthy-numbers doctrine (ADR-0001), metric registry, `objective_decomposition.py` gap gate | Every financial figure is `null`+flag when unresolvable, never 0; every new objective term gets a `cost_breakdown` row so the LP-vs-breakdown gap stays 0 |
| `RESULT_STATE_KEYS` (`services/project_context.py`) → `results_state.pkl` in the project bundle | Persistence path for the `InvestmentCaseReport` and cached billing frames |
| Copilot tools, confirmation tiers, `build_study_report` | Guided path for clients; report narration |
| Multi-tenant projects, ACL, locks, bundles, xlsx export | Unchanged; the case lives in the project bundle |

---

## 2. Decisions (pinned)

| # | Decision | Source |
|---|---|---|
| 1 | **Finance depth = full project finance including tax equity.** Per-participant cashflows, NPV/IRR/payback, debt (term loans, DSCR sculpting, DSRA, fees), corporate tax, depreciation, incentives, and **partnership-flip tax equity**; sale-leaseback and inverted lease are schema-ready, implemented after flip. | Owner Q1 |
| 2 | **Jurisdictions v1 = EU (DE, NL) and North America (US federal + Canada federal).** Shipped as dated, hashed **packs**; a missing rule yields `not_established`, never a silent default. State/provincial packs are slots, not v1 content. | Owner Q2 |
| 3 | **Dispatch realism in v1: rolling horizon with forecast error** as a first-class *valuation* mode, implemented as its own `solve_strategy="realistic"` driver (§9), not as an extension of PyPSA's rolling helper. Design/sizing still uses perfect foresight; valuation re-dispatches the fixed design, flattened to one representative year per investment period, under imperfect forecasts. Perfect-foresight revenue is always reported as the labelled upper bound beside it. | Owner Q3, review F1/F11 |
| 4 | **Participants & value flows are first-class in the v1 UI.** Every cashflow line carries `participant`, `counterparty`, `value_stream`, `tariff_item`. Templates: single owner, BTM PPA, landlord/tenant, DSO/developer, energy-hub members with allocation keys. | Owner Q4 |
| 5 | **Flexibility archetype builders in v1: data-centre load, BESS (degradation/augmentation/warranty), EV fleets & charging hubs, thermal/industrial process flex.** Each is a pack that emits PyPSA components plus commercial and finance metadata. | Owner Q5 |
| 6 | **Users: consultants first; client self-serve later** through the copilot. New capability surfaces as an **Investment Case** panel beside the EH reference-design panel, plus a Library, a Tariff builder and a Participants designer. Headless API and chat tools ship with each phase. | Owner Q6 |
| 7 | **Tariff depth = billing-grade engine**: per tariff item, 15-minute settlement, seasonal/TOU periods, tiers, ratchets, demand charges, network/retail/certificate items. Implemented as **two passes**: a convex *dispatch-grade* approximation inside the LP, and an exact *billing pass* that rates the resulting dispatch; the report shows both and their gap. | Owner Q7 |
| 8 | **Scale: edge in v1, large-system compatible by design.** Contracts and the cashflow engine are per-asset and vectorised; a PyPSA-Eur-scale acceptance test is a later gate (§14 P8), not v1. | Owner Q8 |
| 9 | **The LP objective is the single-entity site cost at the point of connection.** Import energy, export revenue, capacity fee and demand charges are priced from the PoC owner's view (§5), so the optimiser minimises *the client's bill plus annuitised asset cost*, not a social-planner system cost. Participant splits (§7) never enter the objective; multi-party co-optimisation (Gridcog's DSO+developer NPV) is a **later** option behind a flag. Every new objective term has a matching `cost_breakdown` row so `objective_decomposition` reconciles to gap 0. | Research §5.2, review F2/F15 |
| 10 | **Finance is a post-processor.** It consumes solved dispatch, capacities and build years; it never mutates the network. Its only feedback into the LP is the WACC ↔ `discount_rate` reconciliation check (§6.6). | Research §5.2 |
| 11 | **Physical quantities have one source.** The finance layer reads energy, capacity and prices through the same seams the Economics tab uses (`result_df`, `periodized_capital_costs`), so the two can never disagree (trustworthy-numbers rule). | ADR-0001, `specs/2026-08-01-trustworthy-numbers-design.md` |
| 12 | **Time axis of the cashflow model is annual** from financial close to end of life, with construction phasing; operating years are built from the modelled representative year(s) with escalation and degradation. Sub-annual finance is out of scope. | This spec |
| 13 | **Currency**: one project currency (EUR or USD/CAD) chosen per project; FX is an input series, not a model. | This spec |
| 14 | **Oracles**: NREL SAM (single-owner PPA and partnership flip) and REopt.jl (US tariff/incentive/MACRS) are the external test oracles; billing-engine correctness is proven against hand-rated bills. | This spec |
| 15 | **Report name = `InvestmentCaseReport`**, assembled only by one assembler, linked to a `ReferenceDesignReport` when one exists. Sections may be `not_established` / `skipped`. | EH decision 12/16 pattern |
| 16 | **Study budget**: an investment-case run is one solve (design) plus N valuation re-dispatches plus finance-only re-evaluations; it shares the campaign budget substrate (`DEFAULT_BUDGET_SOLVES`). | EH decision 17 pattern |
| 17 | **Series store.** Price curves, forecast-vs-actual pairs, meter history and market series live in a **Library series store** (org-scoped, versioned, persisted beside the project), not in the component-bound `_user_ts` store; `lp_bindings` materialises them into component `_t` attributes at solve time. | Review F4 |
| 18 | **15-minute settlement requires weightings from frequency.** `set_snapshots` and profile templates derive `snapshot_weightings` from `freq` (0.25 h for `15min`) instead of defaulting to 1.0; hourly-hardcoded paths (`_annual_hourly_reference`, sample weeks, `HOURS_PER_YEAR` uses) are audited in P1. | Review F3 |
| 19 | **Sensitivity scenarios** add `"sensitivity"` to `_SCENARIO_TYPES` (backend) and `SCEN_TYPES`/`SCEN_TYPE_LABEL`/`TAG_RE` (frontend); no DB migration (plain string column). | Review F6 |
| 20 | **No external data curated in-tree.** Tariff databases, price forecasts and interconnection data arrive through import schemas (§11.4); the Library stores what the user brings plus the shipped packs. | Research §5.4 |

---

## 3. Architecture

```
                 ┌────────────────────────── Library ──────────────────────────┐
                 │ tariffs · contracts · price series · market packs ·          │
                 │ jurisdiction packs · degradation curves · EV utilisation ·   │
                 │ meter data · costing assumptions                             │
                 └───────────────┬─────────────────────────────┬───────────────┘
                                 │                             │
   Flex archetype builders ──►  PyPSA network  ◄── Commercial layer (A): PoC link prices,
   (D: DC / BESS / EV / thermal)     │              capacity fee, connection agreement,
                                     ▼              peak-demand variables, group caps
                         Design solve (perfect foresight, system cost)
                                     │  capacities, build years
                                     ▼
                Valuation re-dispatch (E): perfect foresight  |  rolling horizon + forecast error
                                     │  dispatch per mode
                                     ▼
                     Billing pass (A2): exact rating of every tariff item, 15-min
                                     │  cost/revenue lines per asset, interval, item
                                     ▼
                Participants & value flows (C): assign every line payer → payee
                                     │
                                     ▼
                Finance engine (B): annual cashflows per participant, capex phasing,
                escalation, degradation, debt, tax, depreciation, incentives, tax equity
                                     │
                                     ▼
                Uncertainty (F): scenario matrix, finance-only re-evaluation, P50/P90
                                     │
                                     ▼
                InvestmentCaseReport (G) ⟷ ReferenceDesignReport · xlsx · chat narration
```

Module placement follows the backend conventions (`.cursor/skills/gui-backend-change`):

| New module | Owns |
|---|---|
| `models/commercial.py` | Tariff, TariffItem, Contract*, ConnectionAgreement, PriceSeriesRef, MarketPack, Participant, ValueStream, AllocationKey |
| `models/finance.py` | FinanceInputs, DebtTranche, TaxPack, DepreciationSchedule, Incentive, TaxEquityStructure, CashflowLine, InvestmentCaseReport |
| `models/flex_archetypes.py` | DataCentreLoadSpec, BessSpec, EvFleetSpec, ThermalFlexSpec |
| `services/commercial/tariff_engine.py` | Billing pass: rate a dispatch against a Tariff at settlement resolution |
| `services/commercial/lp_bindings.py` | Dispatch-grade LP approximation: PoC prices, capacity fee, peak-demand variables, ratchet, group cap, connection envelope — as an `extra_functionality` wrapper composed in `objective.py` |
| `services/commercial/contracts.py` | Contract settlement (PPA variants, CfD, DR availability, lease, EaaS) on rated quantities |
| `services/commercial/participants.py` | Value-flow assignment, conservation check, templates |
| `services/finance/cashflow.py` | Annual cashflow engine (pure, vectorised, pandas) |
| `services/finance/debt.py`, `tax.py`, `incentives.py`, `tax_equity.py` | Sub-engines |
| `services/finance/metrics.py` | NPV, IRR (robust bracketing), payback, DSCR/LLCR/PLCR, solve-for-PPA |
| `services/finance/packs/` | `eu_de.py`, `eu_nl.py`, `us_federal.py`, `ca_federal.py` (dated, hashed) |
| `services/finance/report.py` | `assemble_investment_case_report` (the only assembler); persisted under `investment_case_report` in `RESULT_STATE_KEYS` |
| `services/library/series_store.py` | Standalone versioned series store (decision 17); `TimeSeriesRef` resolution |
| `services/results/billing.py`, `cfe_score.py` | Thin `compute_billing` / `compute_cfe_score` (seam-tested like every `/results/*` handler); `tariff_engine.py` is the engine underneath |
| `services/flex/dc_load.py`, `bess.py`, `ev_fleet.py`, `thermal_flex.py` | Archetype builders emitting components + metadata |
| `services/solver/realistic_dispatch.py` | `solve_strategy="realistic"` driver: own window loop over `n.optimize(window_sns)`, forecast-error injection, settlement of realised deviations, seeds. Same DAG position as `myopic.py`; never imports `solver_service` |
| `services/finance/scenario_matrix.py` | Scenario axes, finance-only re-evaluation, P50/P90 |
| `services/finance/investment_case_runner.py` | `start_investment_case`, `InvestmentCaseRequest` — same injected-dependency pattern as `services/adequacy/eh_study_runner.py` (`solver_state=`, `state_update=`, `publish_study=`); thin handler in `routers/results.py`; study key registered in `STUDY_KEYS`/`ABORTABLE_STUDIES` |
| `routers/library.py` + Alembic migration | Library CRUD (tariffs, contracts, series, packs); org-scoped tables with a new org-level access rule (existing `project_acl.py` is project-tree scoped) |

Leaf modules never import routers. Everything under `services/finance/` is pure: network in,
frames out.

---

## 4. Contracts (data model, normative names)

### 4.1 Commercial

```python
Participant(id, name, role: Literal["site_owner","developer","investor","lender",
            "tax_equity","dso","tso","retailer","tenant","landlord","hub_member",
            "offtaker","other"], currency)

ValueStream(id, kind: Literal["energy_import","energy_export","network_capacity",
            "network_energy","demand_charge","retail_fixed","ancillary","dr_availability",
            "dr_activation","ppa_settlement","cfd_settlement","certificates","lease",
            "eaas_fee","fuel","fom","vom","capex","incentive","tax","debt_service","other"])

TariffItem(id, kind: Literal["energy","demand","capacity","fixed","certificate","tax_levy"],
           unit: Literal["per_kwh","per_kw_month","per_kw_year","per_month","per_kva_year"],
           periods: list[TariffPeriod],          # season × weekday × time window
           tiers: list[Tier] | None,             # (threshold, rate) — increasing rates = convex
           ratchet: Ratchet | None,              # (lookback_months, share)  e.g. 11, 0.9
           settlement: Literal["15min","30min","h"],
           measured_on: Literal["import","export","net","peak_import"],
           direction: Literal["cost","revenue"])

Tariff(id, name, jurisdiction, dso_or_retailer, valid_from, valid_to, items, pack_hash)

ConnectionAgreement(kind: Literal["firm","non_firm_static","non_firm_dynamic","fca"],
                    import_cap_mw, export_cap_mw,
                    envelope: TimeSeriesRef | None,        # dynamic operating envelope
                    curtailment_hours_per_year: float | None,
                    curtailment_compensation_eur_per_mwh: float | None,
                    capacity_fee: TariffItem | None,
                    available_from: date,                   # speed-to-power
                    group: str | None)                      # energy-hub group contract id

Contract = PpaContract | CfdContract | DrContract | LeaseContract | EaasContract | RetailContract
PpaContract(kind: Literal["pay_as_produced","baseload","as_consumed_btm","sleeved"],
            price, indexation, volume_cap, floor/cap, tenor, seller, buyer, asset_ids)
CfdContract(strike, reference_price_series, tenor, asset_ids)
DrContract(availability_eur_per_mw_year, activation_eur_per_mwh, max_events, max_duration_h,
           notice_h, asset_ids or load_ids)

MarketPack(id, region: Literal["DE","NL","GB","US_ERCOT","US_PJM","CA_ON",...],
           price_series: dict[str, TimeSeriesRef],   # day_ahead, imbalance, afrr_cap, ...
           products: list[AncillaryProduct], pack_hash, valid_year)
```

Every `TimeSeriesRef` resolves through the Library series store (decision 17) and carries
`source`, `vintage_year`, `provider`, `version`. The component-bound `_user_ts` store is not used for
commercial series.

### 4.2 Finance

```python
FinanceInputs(currency, financial_close, cod_by_asset: dict[str, date],
              construction_months_by_asset, capex_phasing: list[float],
              contingency_share, escalation: {opex, fuel, tariff, ppa}, degradation_by_asset,
              replacement_capex: list[(year, asset, amount)], terminal_value: TerminalValueRule,
              discounting: {wacc_nominal, cost_of_equity, inflation},
              debt: list[DebtTranche], tax_pack_id, incentives: list[Incentive],
              tax_equity: TaxEquityStructure | None, participants: list[Participant])

DebtTranche(kind: Literal["term_loan","mini_perm","construction","mezzanine"],
            amount | gearing, rate, tenor_years, sculpting: Literal["annuity","dscr_target","level"],
            dscr_target, dsra_months, upfront_fee, commitment_fee, grace_years)

TaxPack(jurisdiction, corporate_rate, depreciation: dict[asset_class, DepreciationSchedule],
        loss_carryforward_years, interest_deductibility_cap, pack_hash, valid_from)
DepreciationSchedule(method: Literal["straight_line","declining_balance","macrs","bonus"],
                     years | rate | macrs_class, bonus_share)
Incentive(kind: Literal["itc","ptc","grant","accelerated_depreciation","cfd","capacity_payment"],
          rate | amount, eligibility: EligibilityRule,   # dated begin-construction / in-service tests
          phase_out: list[(date, share)], feoc_flag: bool | None)
TaxEquityStructure(kind: Literal["partnership_flip","sale_leaseback","inverted_lease"],
                   te_share_pre_flip, te_share_post_flip, target_flip_irr, flip_year_cap,
                   cash_share_pre_flip, cash_share_post_flip, dro_cap)

CashflowLine(year, participant, counterparty, value_stream, tariff_item | None, asset | None,
             amount, provenance: {source, mode: "pf"|"realistic", pack_hash})
```

### 4.2a Naming as implemented (WP0.1 review, code stands)

The contracts landed with unit-bearing names, per house style: `PpaContract.indexation_pct_per_year`,
`volume_cap_mwh_per_year`, `tenor_years`, `reference_price`, `changes_dispatch` (the flag §5 calls
`ppa_changes_dispatch`); `CfdContract.reference_price`, `tenor_years`; `FinanceInputs` flattens
`discounting` into `wacc_nominal`, `cost_of_equity`, `inflation` (all `None` until supplied — ADR-0001);
`TaxPack.source`; `Provenance.seed`. `JurisdictionPack` carries `country` (ISO code, the join key to
`Tariff.jurisdiction`) and `valid_to`, and registers dated versions per jurisdiction; its hash is the `pack_hash` property (not a free function) and its `rules` mapping is read-only after load. Headline figures
(`project_irr_*`, `npv_at_wacc`, `min_dscr`, `flip_year`, …) are **hoisted to the report root** and
`completeness` is a flat `dict[section, status]` beside `sections` (the EH house shape); §4.3 below is
read with that in mind.

### 4.3 Report

```python
InvestmentCaseReport(
  case_id, reference_design_id | None, assumptions_hash, packs: dict[str, hash],
  design: {sizing, cost_at_target_eur, excludes_shed_cost},
  commercial: {tariff_bill_by_item, connection_agreement, contracts_settled},
  dispatch_modes: {perfect_foresight: RevenueSummary, realistic: RevenueSummary, haircut_pct},
  participants: {pid: {cashflows: [CashflowLine], irr, npv, payback}},
  project: {irr_pre_tax, irr_post_tax, npv_at_wacc, lcoe_finance_consistent, ppa_price_for_target_irr},
  debt: {min_dscr, avg_dscr, llcr, plcr, tenor, gearing},
  tax: {taxable_income_by_year, credits_by_year, depreciation_by_year, pack_hash},
  tax_equity: {flip_year, te_irr, sponsor_irr, allocations_by_year} | None,
  uncertainty: {axes, p50, p90, tornado} | None,
  gates: {wacc_vs_discount_rate_consistent: bool, billing_vs_lp_gap_pct, conservation_ok},
  completeness: dict[section, ok|not_established|skipped], pipeline: [StageRecord])
```

Export keys are stable; xlsx export writes one sheet per section plus a `CashflowLines` sheet.

---

## 5. Commercial layer → PyPSA (normative mapping)

The LP sees a **dispatch-grade** convex approximation; the billing pass (§5.5) rates exactly.

| Construct | Dispatch-grade LP expression | Notes |
|---|---|---|
| Energy import tariff (TOU/indexed) | time-varying `marginal_cost` adder on the PoC import Link — on **every** charged import Link of a group contract — applied transiently for the solve (§5.1) | 15-min snapshots when tariff settlement is 15-min; else hourly with `snapshot_weightings` |
| Export price / price-taker sales | a second, one-way `poc→grid` export Link with `marginal_cost` adder = −price (transient) | price series pinned from the Library (P1) / MarketPack |
| Connection capacity fee €/MW/yr | **explicit objective term** `fee · p_nom` on the PoC Link (not `capital_cost`), scaled to the operating time the snapshots represent; `p_nom` extendable within `[0, import_cap]` | the optimiser sizes the connection; binds `poc_link` only (a group's other extendable members warn `group_fee_bypass`) |
| Monthly peak demand charge | variables `ic_peak_import[key]` per (item, window, investment period, local month); the settlement-interval mean of the metered flow ≤ peak (net meters: import − export, floored at 0); objective `+ Σ w_obj · rate · ic_billed_demand[key]` | `extra_functionality` wrapper in `lp_bindings.py`; convex; a group is metered on its members' combined import |
| Ratchet (share ρ of max over lookback L) | a billed variable `ic_billed_demand[m] ≥ ic_peak_import[m]` and `≥ ρ · ic_peak_import[k]` (prior ACTUAL peaks, never billed ones) for each lookback month in the dispatch; a month before the horizon is seeded from meter history (first investment period only) | linear; an unknown lookback month adds no constraint and is disclosed `ratchet_seed_missing` |
| Tiered rates | convex when rates increase with volume: stacked volume variables `ic_tier_q` per month; otherwise **flag** `nonconvex_tier`, price in the LP at a single tier (the first tier in P1, §5.3) and bill exactly in the billing pass | disclosed |
| Non-firm static cap | `p_max_pu` on the PoC Link = cap/ p_nom | |
| Dynamic operating envelope / FCA | time-varying `p_max_pu` from envelope series; curtailment hours as a stress-class entry; compensation in Contracts | |
| Energy-hub group contract | one customer under one tariff: `Σ members p_import[t] ≤ group cap` per snapshot (`ic_group_cap`); every member carries the energy adders; demand and tiers on the group meter | `lp_bindings.py`; members are one-way import Links on the PoC's grid bus, `poc_link` among them; net energy items with export are refused in P1 |
| Grid arriving in year N | PoC Link vintage with `p_nom_max = 0` before N (existing per-vintage bounds) | speed-to-power |
| Curtailment obligation (SB6-style, Irish CRU) | mandatory load reduction in named hours: time-varying `p_set` scaler on the curtailable load share | |
| DR contract | DSR resource (existing) with activation cost; availability payment in Contracts | no double count (FMEA §4.4) |
| PPA / CfD | **no LP change** (settlement only) unless `ppa_changes_dispatch=True`, in which case pay-as-produced price replaces the export price for the contracted asset | disclosed |

### 5.1 Where the bindings live in the solve
`lp_bindings.py` exposes `_wrap_with_commercial_bindings(network, user_fn, cfg, log_queue=None)` with the
same closure/chaining shape as `_wrap_with_capex_budget`; `run_simulation` composes it after the reserve
margin wrapper and **before `_wrap_with_objective_scale`** (scale must stay last). Each objective term it adds
has a matching row in `cost_breakdown.py` (`network_capacity`, `demand_charge`, `energy_import`,
`energy_export`), so `objective_decomposition.gap_pct` stays 0 — this is a P1 acceptance test. Rows are
recomputed from **persisted** data (materialised `links_t.marginal_cost`, `p_nom_opt`, and a
`last_commercial_terms` entry in `RESULT_STATE_KEYS`), never from transient `n._*` stashes, so the gap is
0 before and after a project reload. **As implemented (P1 WP1.3/WP1.4a reviews):** every commercial transform is TRANSIENT (applied for the solve,
undone after it — the user's `marginal_cost`, `capital_cost` and availability are never modified on disk); what
the rows need is committed after a successful solve only (`links_t["ic_energy_price"]`, `n.meta["ic_poc_links"]`,
`n.meta["ic_connection_fee"]`), and `services/commercial/cost_rows` folds the rows into `cost_breakdown` as the
component "Commercial" and into `cost_totals.horizon_system_cost`. The connection fee is an explicit objective
term on the PoC Link's `p_nom`, not `capital_cost`. New linopy variables added by the bindings are **dash-less with an
`ic_` prefix** (`ic_peak_import`, `ic_billed_demand`, `ic_tier_q`, constraint `ic_group_cap`), because
PyPSA's `assign_solution` parses `Component-attr` names and skips dash-less ones cleanly.

The committed records (all ride `network.nc`): `links_t["ic_energy_price"]` (the €/MWh added per charged Link);
`n.meta["ic_poc_links"]` (import, export, `import_members`, priced Links, `energy_hash`);
`n.meta["ic_connection_fee"]` / `["ic_connection_fixed_fee"]` (with `agreement_hash`);
`n.meta["ic_demand_peaks"]` (per key: actual peak read from the solved dispatch, billed demand, rate) and
`["ic_demand_info"]` (items, months not established, partial months, notes, a hash of the items, clock, meter
history, floors and axis); `n.meta["ic_tier_volumes"]` (with the items hash); `n.meta["ic_group"]` (members,
cap, energy shares). The rows compare each hash with the current config: an edit without a re-solve is
`config_changed_since_solve`; a term the config names but the solve did not bind is None plus a
`*_not_established` flag (ADR-0001). The dispatch strategy that will run decides the refusals: demand, tiers
and a capacity fee are refused with rolling or multi-period myopic dispatch in P1 (P6 carries the running
peak), and the preflight states the same refusals before any solver time.

### 5.2 Peak variables under partial coverage
- **Representative periods**: a billing month with no sampled snapshots gets no `P_peak[m]`; its demand
  charge is `not_established` in the report, never weighted from neighbours. When a period's weights represent
  a whole year (Σw ≈ 8760 h), every calendar month of that year is in scope.
- **Partial months (decided in P1 WP1.5a review)**: a month the snapshots cover only in part is charged the
  FULL monthly demand charge, in the LP and in the billing engine alike (that is how the bill works), and is
  disclosed (`demand_partial_months` in the terms and rows; `demand_on_partial_month` in the bill). A short
  horizon therefore over-weights demand against energy; the preflight (WP1.8) warns.
- **Demand intervals**: the peak is on the item's `settlement` interval mean (e.g. hourly demand on 15-min
  dispatch), in the LP (`Σ w·p − W·P_peak ≤ 0` per interval) and in the engine.
- **Windowed dispatch (§9)**: `P_peak[m]` is re-created per window with a lower bound equal to the running
  month maximum already committed, so the controller cannot "forget" a peak it has set.

### 5.3 Non-convex tiers
Decreasing marginal rates (the common NL Energiebelasting and C&I volume case) are non-convex in a
minimisation. Such items are flagged `nonconvex_tier`; the LP prices them at the rate of the tier the meter
history (or the previous iteration) predicts the month will land in, disclosed in the report; the billing
pass bills them exactly. **As implemented in P1:** the LP prices them at the FIRST tier (no volume history
is modelled yet); the history-predicted tier is P2 WP2.1 / WP2.3.

### 5.5 Billing pass (`tariff_engine.py`)

Input: dispatch at settlement resolution (resampled from LP resolution with a disclosed
`resampling` note when coarser), the Tariff, meter history for ratchets. Output: a frame
`(interval, tariff_item, quantity, rate, amount)` and monthly/annual bills. Rules: tiers are
applied on cumulative monthly volume; demand charges on the maximum of `measured_on` within the
period; ratchets on the lookback maximum; fixed items pro-rated. Every amount is exact and
traceable to one item. The gap between LP cost and billed cost is reported **per item kind** (`energy`,
`demand`, `tiers`, `capacity`, `fixed`, `contracts`) and per investment period, on the unweighted
period-year amounts, by `services/commercial/gap.py` (P2 WP2.3). The LP side is recomputed from the solve's
committed records on the same dispatch. Each explained difference is a cause with a **computed** amount:
`fixed` and `not_in_lp` (the billed amount of an item the LP leaves out); `nonconvex_tier` (billed − LP
for an item priced at one predicted tier); `tier_allocation` (windowed tiers: the engine's proportional
allocation − the LP's optimal one); `net_split_by_direction` (a net item the LP charges on one side);
`settlement_only` (a contract with no LP term); `months_not_established` and `partial_months`
(disclosures, amount 0: both sides compare the same sampled months); `ratchet_seed` (amount `null` plus a
flag); and `config_changed_since_solve` / `lp_recipe_changed` (the whole difference). **`resolution` is
a disclosed risk, not a computed cause**: the LP and the bill read the same dispatch at the same
resolution, so a finer real load cannot show as a gap. When the axis is coarser than a demand item's
settlement, the payload carries `resolution_risk` with the preflight warning. What no cause explains is
`unattributed`. Above the threshold (default 5 %, configurable) it raises the `billing_gap_unexplained`
warn gate. There is no general "billed ≥ LP" invariant.

---

## 6. Finance engine

### 6.1 Time axis
Years from `financial_close` to `max(cod + lifetime)`; construction years carry capex drawdowns
and interest during construction; operating years replicate the valued representative year with
escalation, degradation and contract indexation; replacement capex and terminal value at the
scheduled years.

### 6.2 Operating cashflows
From the billing pass and contract settlement per participant, per value stream, per year.
Fuel is a separate stream once heat rate and fuel price are split (§8 BESS/gensets require it).

### 6.3 Debt
Tranches sized by gearing or **sculpted to a DSCR target** on CFADS; annuity or level
alternatives; DSRA funding/release; fees; IDC capitalised. Sculpting is **circular** (debt service ←
CFADS ← tax ← interest ← schedule; IDC ← drawdowns ← debt amount): solve by fixed-point iteration on
(debt amount, schedule) with relative tolerance 1e-6 and at most 50 iterations; non-convergence sets the
`debt` section `not_established` with the residual. CFADS is defined **post-tax, pre-financing** (SAM's
convention) and the definition is printed in the report. Outputs: schedule, min/avg DSCR, LLCR, PLCR.

### 6.4 Tax and depreciation
Per jurisdiction pack: corporate rate; depreciation methods (straight-line, declining balance,
MACRS classes, bonus); loss carry-forward; interest deductibility caps where the pack defines
them. Taxable income and tax by year per participant.

### 6.5 Incentives
Dated eligibility rules (begin-construction / placed-in-service tests, phase-out tables, FEOC
flag) so the US pack encodes OBBBA as data with dates, not code. CfD and capacity payments are
contracts (§4.1) and flow as revenue.

### 6.6 Returns and consistency
Project IRR (pre/post tax), equity IRR per participant, NPV at WACC, payback, finance-consistent
LCOE, **solve-for-PPA-price** (bisection on the PPA price to a target equity IRR). Gate (real/nominal aware): the LP applies `cfg.discount_rate` (nominal) inside the annuity on
real-priced costs, and — only when `auto_discount_periods` is on — a Fisher real rate for cross-period
PV (`services/solver/assumptions.py` L641–698). The gate therefore compares `wacc_nominal` to
`cfg.discount_rate` **and** finance `inflation` to `cfg.inflation_rate`, and the report states the annuity
basis (nominal rate on real costs) and the PV basis (real) separately; a mismatch sets
`wacc_vs_discount_rate_consistent=false` and names the number the sizing used.

### 6.7 Tax equity (partnership flip)
Oracle: **SAM "Single Owner" for P4 and SAM "Partnership Flip with Debt"** (and without debt as a
second case) for P7. `TaxEquityStructure` carries, beyond the shares in §4.2: `itc_share_te` (credit
allocation separate from income allocation), `developer_fee`, `target_irr_basis` (after-tax cash plus
tax benefits, SAM's test), capital-account tracking with the DRO cap, ITC recapture schedule, and
`debt_in_structure: bool`. Flip year is the first year the TE investor's after-tax IRR reaches
`target_flip_irr` (capped at `flip_year_cap`). Outputs per participant. Documented deviations from SAM
are listed in the P7 findings note. Sale-leaseback and inverted lease: schema present, implementation
after flip parity.

---

## 7. Participants and value flows

Every `CashflowLine` names payer and payee. **Conservation rule**: for internal streams the sum
over participants is zero; external streams (grid, market, tax authority, lender) have an
explicit external counterparty. The panel shows a Sankey per year and a per-participant table.
Templates (pack-like, hashed): `single_owner`, `btm_ppa` (developer owns asset, site buys
as-consumed), `landlord_tenant` (landlord capex, tenant pays lease + energy), `dso_developer`
(DSO pays network-support availability), `energy_hub` (members share a group connection;
allocation keys: by contracted capacity, by peak contribution, by energy). Multi-party
co-optimisation is deferred (decision 9).

---

## 8. Flexibility archetype builders

Each builder is a pack (`archetype_hash`), takes a spec, emits PyPSA components with
`eh_*`/`ic_*` custom attributes, plus the commercial/finance metadata (asset class for
depreciation, degradation curve, contract eligibility).

| Builder | Spec | Emits | Notes |
|---|---|---|---|
| **Data-centre load** | phases `[(cod, it_mw)]`, utilisation profile kind (`ai_training`, `inference`, `mixed`), PUE curve vs outdoor temperature, UPS/distribution loss, redundancy tier (`N`,`N+1`,`2N`), critical share, curtailable share + max duration, workload-shift window, gensets (MW, fuel, run-hour cap, permit), waste-heat export | Load (critical) + Load (curtailable) with `eh_critical`, shift Store + Links, cooling Link with time-varying efficiency, genset Generators with `e_sum_max`, waste-heat Link | 24/7 CFE hourly-matching metric added to results |
| **BESS** | power, energy, RTE, DoD, cycle/throughput limit, calendar + cycle fade curves, augmentation policy, warranty limits, AC/DC coupled | StorageUnit or Store+Links per vintage; per-period `e_nom` fade via investment-period scaling; augmentation as new vintages | degradation is exposed to finance as replacement capex |
| **EV fleet / charging hub** | fleet size, arrival/departure windows, daily energy, charger MW, smart-charging on/off, V2X, public charger utilisation curve with probabilistic draw | aggregated Store with time-varying availability and `e_sum_min` per window, charger Link | probabilistic utilisation → scenario draws (§10) |
| **Thermal / process flex** | heat demand series, heat pump COP(T), thermal store, e-boiler, gas backup, interruptible process MW with notice/duration limits | heat bus, Links, Store, interruptible Load + DSR resource | uses PyPSA-Eur temperature cutouts where available |

---

## 9. Dispatch realism (valuation modes)

Sizing is solved with perfect foresight (upper bound, labelled). Valuation re-dispatches the
**fixed design** (`p_nom_extendable=False`, capacities from `p_nom_opt`) in two modes. Because
PyPSA's `optimize_with_rolling_horizon` has no per-window input hook and is disabled for
multi-period networks, valuation first **flattens** the design to one representative operating
year per investment period (a single-period network with that period's capacities), then runs:

1. **Perfect foresight** — one full solve of the flattened year.
2. **Realistic controller** — `solve_strategy="realistic"` in `services/solver/realistic_dispatch.py`
   (own window loop, same DAG position as `myopic.py`; never imports `solver_service`):
   - Vocabulary: window length `horizon`, `overlap`; each window **commits `horizon − overlap`**
     intervals (the same convention the existing rolling strategy uses).
   - Forecasts: for each window the controller sees `forecast = realised + ε`, with ε an AR(1)
     process whose standard deviation grows with lead time; parameters per series kind (price, load,
     wind, solar) with disclosed defaults, calibratable from user-supplied forecast-vs-actual pairs in
     the Library.
   - **Settlement rule** (what makes the schedule physically consistent): the committed schedule of
     *steerable* assets (storage, gensets, flexible loads, exports) is kept; realised load and VRE
     replace the forecast; the resulting imbalance is absorbed by the PoC import/export within its
     connection envelope and **billed at the tariff or imbalance price**, and any residual beyond the
     envelope is curtailment or unserved energy, reported as such. Storage state of charge is carried
     from the **realised** balance, not the forecast solve. `P_peak[m]` carries its running maximum (§5.2).
   - Ancillary-service reservations reduce the power and energy available to the controller in the
     window and are settled from the MarketPack.
   - Multiple seeds give a revenue distribution; the report shows mean, P10/P90 and the **haircut**
     versus perfect foresight; the `mode` and `seed` ride on every downstream line's provenance.

Both modes feed the same billing pass and finance engine.

## 10. Uncertainty on money

Axes: price path (MarketPack alternates), tariff escalation, capex, capacity factor/yield,
degradation, COD delay, grid-arrival year, interest rate, tax-pack alternates. Two speeds:
**re-solve** axes (change dispatch; run through the campaign budget) and **finance-only** axes
(re-evaluate the cashflow engine on cached dispatch; seconds). Outputs: tornado on equity IRR
and min DSCR, P50/P90 tables, scenario matrix stored as child projects with
`scenario_type="sensitivity"` (adding the value the DB model already anticipates).

---

## 11. Packs and the Library

- **Jurisdiction packs** (`services/finance/packs/`): `eu_de` (KStG/GewSt corporate rate,
  straight-line/declining balance, Netzentgelte structure incl. §19 StromNEV atypical use and
  §17(2b) EnWG FCA discount slots), `eu_nl` (VPB, depreciation, ODE/energy tax bands, energy-hub
  group contracts), `us_federal` (21 % rate, MACRS 5/7/15-yr, bonus, ITC/PTC with OBBBA
  dates and FEOC flag, storage ITC runway), `ca_federal` (CCA classes 43.1/43.2, clean-tech
  ITC). Each has `valid_from`, `source`, `pack_hash`; state/provincial packs are empty slots.
- **Market packs**: DE, NL, US (ERCOT/PJM), CA_ON — *schemas and loaders*, sample series for
  tests only; real forward curves are user imports.
- **Library** (`routers/library.py`): tariffs, contracts, connection agreements, price series,
  degradation curves, EV utilisation curves, meter data, costing assumptions; per org; every
  item hashed and versioned; a case pins the item versions it used.
- **Import schemas** (§11.4): CSV/xlsx for price series and meter data; JSON for tariffs
  (OpenEI URDB-compatible fields for US); adapters for external providers are out of scope.

---

## 12. API, chat and UI

**Endpoints** (thin handlers in `routers/results.py`, runners in `services/`):
`POST/GET /results/investment_case`, `/abort`, `GET /results/investment_case/report`,
`GET /results/investment_case/export.xlsx`, `POST /results/valuation_dispatch` (mode, seeds),
`GET /results/billing` (billing pass on current dispatch), `GET /results/cfe_score`,
`POST /network/archetypes/{dc_load|bess|ev_fleet|thermal_flex}`, `CRUD /library/*`,
`POST /results/scenario_matrix`.

**Chat tools**: `build_archetype`, `set_connection_agreement`, `attach_tariff`,
`define_participants`, `run_investment_case`, `run_valuation_dispatch`, `explain_cashflow`
(which lines drive IRR/DSCR), `solve_ppa_price`, `run_scenario_matrix`, `get_investment_case`.
Tiers per existing convention; runs are `execution_long_running`.

**UI (consultant-first)**: a new Results tab `investment` (added to the `Results.tsx` tab union and
routing; the EH panel itself is mounted inside `AdequacyTab.tsx`, so the Investment Case panel gets its
own tab rather than another stacked panel) containing:
Participants designer (roles, templates, Sankey), Tariff builder (items/periods/tiers/ratchets,
bill preview on current dispatch), Connection agreement editor, Finance inputs (tranches, tax
pack, incentives, tax-equity), Valuation mode selector with haircut chip, Results (per
participant cashflows, returns, DSCR, tax, flip), Scenario matrix (tornado, P50/P90), Export.
Completeness chips everywhere, as in the EH panel.

---

## 13. Phases, work packages and gates

Process (owner's instruction): plan → review loop until pass → per work package TDD with an
implementation-review loop until pass → per phase end-to-end QA (a `qa_*.py` driver discovered
by `run_qa_drivers.py` plus frontend vitest) before the next phase. Each phase ends with a
findings note under `docs/superpowers/findings/`.

| Phase | Work packages | Phase e2e QA |
|---|---|---|
| **P0 Contracts & seams** | WP0.1 `models/commercial.py`, `finance.py`, `flex_archetypes.py`, `InvestmentCaseReport` skeleton + completeness; WP0.2 pack loader + hashing + `not_established` semantics; WP0.3 results seam: physical-quantity accessor used by both Economics and finance (regression test that they agree); WP0.4 `scenario_type="sensitivity"` backend + frontend enums; WP0.5 persistence: `investment_case_report` + billing cache in `RESULT_STATE_KEYS`, bundle round-trip; WP0.6 tripwire tests for `services/commercial|finance|library` (no router imports, no `solver_service` import, docstring-only `__init__`) | schema round-trip, hash stability, seam agreement, save/load round-trip |
| **P1 Commercial layer, dispatch-grade** | WP1.0 weightings-from-frequency (`set_snapshots`, profile templates) + 15-min fixture + hourly-assumption audit; WP1.1 Library series store + `TimeSeriesRef` resolution + Alembic migration + org access rule; WP1.2 **`tariff_engine` core** (energy/TOU/fixed items, hand-rated fixtures) — built *before* the LP bindings so the LP is validated against the billing engine; WP1.3 PoC price binding + export price; WP1.4 capacity fee + connection agreement kinds (firm / non-firm static / envelope / FCA / available_from); WP1.5a-0 linopy new-variable spike; WP1.5a peak-demand variables; WP1.5b ratchet (running-max carry for windowed dispatch is P6); WP1.5c convex tiers + `nonconvex_tier` flag; WP1.6 group contract; WP1.7 `cost_breakdown` rows for every new term + `objective_decomposition` gap 0; WP1.8 validation preflight (double counting, missing PoC) | `qa_commercial_lp.py`: a 3-bus edge network under DE and US tariffs at 15-min; LP cost vs `tariff_engine` rating; objective gap 0 |
| **P2 Billing pass & contracts** | WP2.1 `tariff_engine` demand/ratchet/tier rating at 15-min; WP2.2 contracts settlement (PPA variants, CfD, DR, lease, EaaS, retail); WP2.3 per-item-kind `billing_vs_lp_gap` with cause attribution + gate; WP2.4 Library CRUD + import schemas; WP2.5 `compute_billing` / `compute_cfe_score` thin results + seam cases | hand-rated bills (fixtures) match to the cent; REopt URDB fixture parity |
| **P3 Participants & value flows** | WP3.1 participants + assignment + conservation; WP3.2 templates; WP3.3 per-participant tables + Sankey (FE) | conservation on all templates; FE tests |
| **P4 Finance engine (single owner)** | WP4.1 time axis, capex phasing, escalation, degradation, replacement, terminal value; WP4.2a debt: gearing-sized tranches, annuity/level schedules, fees, IDC; WP4.2b DSCR sculpting fixed-point + DSRA; WP4.3a tax & depreciation engine with `eu_de` and `us_federal` packs; WP4.3b `eu_nl` and `ca_federal` packs (post-MVP-A); WP4.4 incentives with dated rules (OBBBA); WP4.5 metrics + solve-for-PPA + real/nominal WACC gate; WP4.6 xlsx export | **SAM Single Owner oracle** parity within tolerance on 3 reference cases; Excel round-trip |
| **P5 Flex archetypes** | WP5.1 DC load + CFE score; WP5.2 BESS degradation/augmentation/warranty; WP5.3 EV fleet/hub; WP5.4 thermal/process flex; WP5.5 archetype UI forms + chat tools | each archetype: build → solve → bill → finance on a fixture; EH pipeline still passes with archetype networks |
| **P6 Dispatch realism** | WP6.1 valuation re-dispatch API (fixed design); WP6.2 forecast-error processes + calibration; WP6.3 rolling controller with reservations; WP6.4 seeds, distribution, haircut chip | PF ≥ realistic mean on every fixture (monotonicity); reproducible seeds; BESS arbitrage haircut in a plausible band on a GB/DE price fixture |
| **P7 Tax equity & uncertainty** | WP7.1 partnership flip (SAM "Partnership Flip with Debt" oracle, deviations documented); WP7.2 sale-leaseback/inverted lease (schema-first, implement if time); WP7.3 scenario matrix + finance-only path + tornado + P50/P90; WP7.4 `InvestmentCaseReport` assembler, chat narration, link to `ReferenceDesignReport` | **SAM partnership-flip oracle** parity; matrix reproducibility; report completeness |
| **P8 Scale & hardening** | WP8.1 PyPSA-Eur clustered network acceptance (aggregate per-carrier cashflows); WP8.2 performance budget for 15-min year; WP8.3 security review (uploads, packs) | acceptance gate per decision 8 |

**MVP-A** = P0–P4 on `single_owner` with `eu_de` + `us_federal` (a data-centre archetype may be
hand-built; `eu_nl`/`ca_federal` follow in WP4.3b). **MVP-B** = + P5 + P6. **v1** = through P7. P8 is post-v1 hardening.

---

## 14. Non-goals (v1)

- A market-price forecasting engine (PLEXOS/Aurora territory); an in-house tariff database.
- Multi-party co-optimisation in the LP objective (behind a flag, later).
- Sub-annual financial statements; IFRS/GAAP accounting; FX modelling.
- State/provincial tax packs beyond empty slots; non-EU/NAM jurisdictions.
- In-tree EMT / dynamics (gridspine hands off); replacing PowerFactory.
- Replacing the client's Excel model: the tool exports to it.

---

## 15. Acceptance and QA doctrine

- **Oracles**: SAM single-owner and partnership-flip reference cases (inputs and expected outputs
  committed as fixtures with provenance); REopt.jl URDB tariff fixtures; hand-rated bills.
- **Cross-surface agreement**: Economics tab, EH TEA and finance layer read the same physical
  quantities; a seam test fails if they diverge (trustworthy-numbers).
- **Provenance**: every report field resolvable to a pack hash, a library item version, a
  dispatch mode and a seed; `null`+flag over 0.
- **Monotonicity & conservation invariants**: PF revenue ≥ realistic mean; participants' internal
  streams sum to zero; on every LP fixture the billing-vs-LP gap is fully attributed to computed causes
  (`unattributed_pct` < 1e-6, §5.5), replacing "billed ≥ dispatch-grade cost minus tolerance on convex
  tariffs".
- **CI**: backend pytest (`gui-tests`), qa drivers (`gui-qa-drivers`), frontend vitest +
  typecheck; new fixtures under `tests/fixtures/investment_case/`.

---

## 16. Risks and open points (to be settled in the plan review)

1. 15-minute settlement across a full year multiplies LP size by 4; representative periods and
   the two-pass design mitigate, but the P2 performance budget must be measured early.
2. Peak-demand ratchets with lookback windows longer than the modelled horizon depend on meter
   history quality; the Library must make the seed explicit.
3. Tax-equity parity with SAM requires reproducing SAM's exact allocation conventions; tolerance
   and documented deviations must be agreed in the P7 plan.
4. Forecast-error calibration defaults are assumptions; the report must say so until a client
   supplies forecast-vs-actual data.
5. The process-global live network limits parallel scenario matrices; finance-only paths avoid
   this, re-solve axes queue through the campaign budget.
6. Flattening a multi-period design to per-period operating years for valuation loses inter-period
   storage coupling (seasonal stores); the report labels valuation as per-year and the P6 plan tests
   that annual energy balances match the design solve within tolerance.
7. Review conditions carried into plans: F10 (gap attribution) → P2; F13 (flip fields, oracle) → P7;
   F16 (billing core before LP bindings) → P1 order above; F17 (migration, series store,
   `RESULT_STATE_KEYS`, cost-breakdown rows, weightings, tab/route, chat + facade tests) → P0/P1/P2.
