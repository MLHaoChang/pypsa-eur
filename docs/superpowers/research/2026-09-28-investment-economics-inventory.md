<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# pypsa-gui: investment decision-making and techno-economic inventory (Agent A)

Scope: `pypsa-gui/backend`, `pypsa-gui/frontend/src`, `gridspine/`, `docs/superpowers/`. Read-only audit, 2026-09-28.
Paths below are relative to `pypsa-gui/backend/` (BE) or `pypsa-gui/frontend/src/` (FE) unless given in full.

**Summary.** The tool is a **least-cost system-planning optimiser** (PyPSA LP/MILP) with a careful, cross-checked
**cost-accounting layer**. That layer covers annuitised CAPEX + FOM, OPEX, CO2, curtailment and VoLL, per-asset
revenue at shadow prices, net profit, LCOE/LCOS/LCOH, per-period and per-carrier roll-ups, and a reconciliation to
the LP objective. It also has a mature **reliability/adequacy** stack: ENS cap, frontier, MC LOLE, ELCC, FMEA, and
Energy-Hub archetype packs.

It has **no project-finance layer**. There is no NPV/IRR/payback, no year-by-year cash-flow table, no
debt/equity/tax/depreciation, no degradation or replacement, no tariff engine (demand charges, TOU bill), no PPA,
no ancillary-service markets, no price/cost Monte Carlo, no parametric cost sensitivity, and no PDF/DOCX/PPTX
report. "Revenue" always means **revenue valued at the model's own LP nodal duals**, not at external market or
tariff prices.

---

## 1. Economics computed and shown

### 1.1 Cost primitives and where the money comes from

| Primitive | Implementation | Notes |
|---|---|---|
| Annuity / CRF | `services/solver/periodized_costs.py:29 _annuity(rate, lifetime)` | Standard `r(1+r)^L/((1+r)^L-1)`; single home, "the LP and the reporting layer must not diverge on it" (`services/economics.py` docstring). |
| CAPEX input forms | Per-asset `capital_cost` (annualised €/MW/yr) **or** `overnight_cost` + per-asset `discount_rate` + `lifetime` (`models/schemas.py:137-149, 201-215, 261-289, 338-343`) | PyPSA derives the annualised value as `overnight × annuity(r,L) × nyears`. It is filled transiently from `SolverConfig.discount_rate` / `default_lifetime` by `with_periodized_cost_defaults` (`periodized_costs.py:~126-300`). The per-asset `discount_rate` column is the only per-asset "WACC" knob, and it only feeds the annuity. |
| FOM | `fom_cost` per asset; `fom_horizon_factor` (`periodized_costs.py:58`); `statistics_fom_lookup` (`services/economics.py:207`) | Added to every "Capital Expenditure" cell since 2026-09-26 (see 1.4). |
| Variable OPEX / fuel | `marginal_cost` (static or time series, `FE pages/TimeSeriesManager.tsx:86,114`), `marginal_cost_quadratic`, UC `start_up_cost`/`shut_down_cost`, `committable` (`models/schemas.py:274-289`) | Fuel is **not a separate object**: fuel price is folded into `marginal_cost`. There is no fuel-price/efficiency split and no escalation curve (only time-series upload). |
| CO2 cost | `SolverConfig.co2_price` (scalar €/t) and `co2_price_per_period` (`services/solver_service.py:147-155`), applied as a marginal-cost surcharge `price × co2_intensity / efficiency` | CO2 cap via `GlobalConstraint type=primary_energy` (`models/schemas.py:~501`). Shadow price reported (`services/results/emissions.py:328-340`). |
| Curtailment cost | Generator `curtailment_cost` via LP wrapper `_wrap_with_curtailment_cost` (`services/solver/objective.py:170`) | It is a *dispatch subsidy/penalty*, not a revenue-loss metric. Prices are "merit-order corrected" to remove its distortion (`services/results/prices.py:40-90`, `load_frames.py:59-140`). |
| Lost load (VoLL) | `SolverConfig.voll` → per-bus slack generators; captured cost `lost_load_cost_eur` (`services/results/lost_load.py:39-77`) | Excluded from the adequacy cost axis by construction (`models/adequacy.py:288 CostBlock.excludes_shed_cost: Literal[True]`). |
| DSR | `dsr_price_eur_per_mwh`, `dsr_share_of_load`, `dsr_buses` (`solver_service.py:190-193`) | Opt-in per bus. |
| CAPEX budget | `capex_budget_per_period` → LP constraint `Σ overnight × Δp_nom ≤ budget[P]` (`solver_service.py:242`, `services/solver/objective.py:20-165`) | The only "financing-like" constraint. It is an investment ceiling per period, not a financing structure. |

### 1.2 Results endpoints (all under `/api/results`, `routers/results.py`)

| Endpoint | Line | Service | What it gives |
|---|---|---|---|
| `/cost_breakdown` | 229 | `services/results/cost_breakdown.py:94 compute_cost_breakdown` | Totals and breakdowns per component, carrier and period: `capex`, `fom`, `opex`, `capex_expansion`, `curtailment_cost`, `storage_charge_cost`, `storage_capex_expansion`. Also `capex_lifetime` / `capex_expansion_lifetime` = **PV of upfront (overnight) investment**, discounted per asset by `(1+r)^-(build_year-ref)` (`_pv_factor_series`, `periodized_costs.py:372`). The field is `null` when it cannot be resolved (`capex_lifetime_available` flag). Output keys: `cost_breakdown.py:682-727`. |
| `/objective_decomposition` | 263 | `services/results/objective_decomposition.py` | Audit bridge from `n.objective + objective_constant` to `cost_breakdown.total`: `nonextendable_fixed_cost_eur`, `period_weighting_adjustment_eur` (objective vs years weights), `residual_gap_eur/pct`. |
| `/economics_by_carrier` | 298 | `services/results/economics_by_carrier.py` → `services/compare/economics.py:33 _compute_economics_summary` | Per carrier (`total` + `by_period`): revenue, opex, gen_cost, storage_charge_cost, curtailment_cost, lost_load_cost, capex, dispatch_gwh, `lcoe_eur_per_mwh` (`compare/economics.py:540-563`). |
| `/asset_economics` | 1628 | `services/results/asset_economics.py:33 compute_asset_economics` | **Per asset** for Generator / StorageUnit / Store / **Link**: `revenue_eur` (Σ p·price·w at corrected LP duals), `vom_cost_eur`, `fixed_cost_eur` ((capital+fom)×p_nom_opt×Σyears), `fom_cost_eur`, `net_profit_eur`, `lcoe`/`lcos`, `capacity_factor`, `avg_price` (capture price). Storage adds charge/discharge MWh, `charge_cost_eur`, `spread_eur_per_mwh`, and RTE. Links are netted (gross revenue at bus1 − input cost at bus0; `asset_economics.py:724+`). Every row has a `by_period` breakdown. Fields `asset_economics.py:419-444, 563-596, 691-719`. |
| `/lcoh` | 429 | `services/results/lcoh.py:25 compute_lcoh` | Per electrolyser plus fleet: (fixed + VOM + electricity input at corrected duals) / H2 out, €/MWh and €/kg (LHV 33.33 kWh/kg, `lcoh.py:264-266`), `fom_eur_per_year`, per-period values. |
| `/carrier_kpis` | 546 | `services/results/carrier_kpis.py` | Energy, CF, market value, revenue per carrier. |
| `/statistics` | 331 | `services/results/statistics.py` | Raw `n.statistics()` plus a "Fixed O&M" column. |
| `/emissions`, `/prices`, `/price_drivers`, `/curtailment`, `/lost_load`, `/line_duals`, `/unit_commitment` | 575, 727, 755, 781, 1515, 651, 623 | `services/results/*` | Physical and price context for the economics. |
| `/api/simulation/asset_costs` | `routers/simulation.py` | `periodized_capital_costs` (`periodized_costs.py:397`) | Per-asset `capital_cost`, `fom_cost`, `fixed_cost`, `overnight_cost`, `overnight_cost_pv`, `lifetime`. |
| Asset Detail `/api/results/asset/{cls}/{name}` (+`export.xlsx`) | `routers/asset_results.py:18-77` | `services/asset_results/registry.py`, `compute.py`, `export.py` | Registry metrics: `capex_annual` ("Annualised fixed cost", `registry.py:94`), revenue / VOM / fixed / net profit / LCOE (`registry.py:198-214`), capture price and capture rate (`:184-188`), CO2 (`:220-228`), bus-level load cost and generation revenue (`:364-368`), and self-sufficiency (`:311`). |

### 1.3 Frontend surfaces
- **Economics tab**, `FE pages/results/Economics.tsx`:
  - KPIs (`:717-761`): Total revenue, Fixed cost, Variable cost, Net profit (+margin), Energy delivered, Charge cost, System LCOE/LCOS, Avg capture price.
  - Charts: "Top profitable & loss-making assets" (`:768`) and per-asset LCOE/LCOS ranking (`:798`).
  - Grouped or individual asset table with per-period expansion (`:827-879`).
  - Hydrogen panel with Fleet LCOH, H2 produced, Fixed cost, and Electricity input cost (`:1210-1255`).
- **Capacity Expansion tab**, `FE pages/results/CapacityExpansion.tsx`:
  - `costMode` 'annual' vs 'lifetime' ("Total investment (PV)") toggle (`:174, :949`).
  - KPIs: Total new investment, CAPEX installed, Total system cost, Storage charge cost, Curtailment cost, Storage CAPEX (`:964-1002`).
  - System-cost waterfall (existing CAPEX → expansion → OPEX; annual only; `:755-1060`).
  - Expansion CAPEX by class, capacity expansion by period, investment by asset class per period (`:1065-1169`).
- **Dispatch tab** carries "CAPEX (annuitised)" KPI which reconciles exactly with Σ `fixed_cost_eur` (asset_economics docstring).
- **Model Horizon > Economics step**, `FE pages/modelHorizon/StepPeriodEconomics.tsx`: period years, objective weight, PV preview, auto-discount toggle, per-carrier load scalers, and CAPEX budget per period.

### 1.4 Correctness history ("trustworthy numbers")
- `docs/superpowers/specs/2026-08-01-trustworthy-numbers-design.md` and `plans/2026-08-01-trustworthy-numbers.md`:
  - Asset Detail under-reported annualised CAPEX by 22–100% because it read raw `capital_cost` while assets were priced via `overnight_cost`.
  - The fix was a golden solved fixture (`tests/golden/fixture.py`), an independent oracle (`tests/golden/oracle.py`) and a coverage matrix across the **nine economic surfaces**.
- `findings/2026-07-31-link-economics-missing.md`: `/asset_economics` had no `links` key, so electrolysers and heat pumps were invisible. The finding is **FIXED**, and two user decisions came out of it:
  - Link revenue is **net** of energy bought.
  - Converter unit cost is **all-in** (matches LCOH).
- `findings/2026-08-01-economic-surface-disagreements.md`: **CLOSED**. Two wrong numbers were fixed:
  - Asset Detail CAPEX.
  - A 365× CAPEX overstatement in `economics_by_carrier`/Compare, caused by a hand-rolled annuity without `nyears`.

  Two shape residuals were deferred: `compare_economics.per_asset_lcoh` is Link-only, and cost_breakdown / statistics / economics_by_carrier have no per-asset field.
- `findings/2026-08-03-compare-tab-correctness.md`:
  - S1 (Capacity tab omitted Link CAPEX) is **FIXED**. `annuitised_capex_by_carrier` (`services/economics.py:79`) now walks all six classes, including lines and transformers.
  - S2 (`binding_hours` basis) is fixed. S3 (LCOE) is cleared.
- `findings/2026-09-27-fom-missing-from-fixed-cost.md`: **fixed cost now = annuitised investment + FOM on every surface**, so `cost_breakdown.total` reconciles with the LP objective (a measured gap of `fom_cost × p_nom_opt` was closed). Regression test: `tests/test_fom_reconciliation.py`.
- `services/cost_totals.py`: `horizon_system_cost` (used by the solve queue) has the same basis as `cost_breakdown`. Its docstring explains why the **LP objective is unusable as a system cost under myopic foresight** (−42.9% / +22.2% measured).
- Doctrine (ADR-0001): an unresolvable number is `null` plus a flag, never 0 (`capital_costs_available`, `capex_lifetime_available`).

### 1.5 Per-asset vs fleet vs per-period
- **Per-asset:** asset_economics, Asset Detail, lcoh rows, and `asset_costs`.
- **Fleet / per-carrier:** economics_by_carrier, carrier_kpis, cost_breakdown `by_carrier`, and the LCOH fleet row.
- **Per-period:** every surface emits `by_period`. Horizon totals are **Σ over periods of (per-year value × `ipw.years`)**, which is **undiscounted** (`services/period_utils.py:80 snapshot_weights`).
- Only `capex_lifetime*` is a present value, and it covers the upfront investment only.

---

## 2. Financial metrics: missing or partial

A repo-wide grep for `npv|irr|payback|cash flow|depreciat|tax credit|ITC|PTC|salvage|residual value|degradation|PPA|demand charge|time-of-use|tariff|ancillary|FCR|aFRR|capacity market|debt|equity` found **no implementation** of any of them in BE services/routers/models or FE pages. The only hits are unrelated uses of "subsidy" (the curtailment wrapper) and one label, `period_basis: "npv_multi_period"` (`models/adequacy.py:297-299`).

| Metric | Status | Closest hook |
|---|---|---|
| **NPV (project)** | Absent | `capex_lifetime` (PV of upfront CAPEX, `cost_breakdown.py`). The LP objective under `auto_discount_periods` is a discounted system cost (NPV of cost, not of project cash flow). Adequacy `CostBlock.period_basis="npv_multi_period"` is only a label. |
| **IRR / ROI** | Absent | `asset_economics` `net_profit_eur` and revenue per asset and period: one annualised or horizon number, no cash-flow vector. |
| **Simple / discounted payback** | Absent | Same. The data needed is upfront CAPEX (`overnight_cost_pv`) plus annual net margin, but no year axis. |
| **Year-by-year cash-flow table** | Absent | `by_period` rows (one row per investment period, weighted by `years`). There is no expansion to calendar years. |
| **WACC / debt-equity / financing** | Partial (discount rate only) | Global `SolverConfig.discount_rate` (default 0.07, `solver_service.py:133`) plus a per-asset `discount_rate` column feed only the annuity. No debt share, interest, tenor, DSCR or equity return. |
| **Depreciation / taxes** | Absent | none |
| **Inflation / escalation** | Partial | `inflation_rate` (`solver_service.py:134-145`) is used **only** to convert the nominal discount rate to a real rate in the cross-period PV factor, and only when `auto_discount_periods=True` (`services/solver/assumptions.py:641-695`). The annuity keeps the nominal rate. There is no price or cost escalation; the workaround is per-period `co2_price_per_period`, `load_scalers(_by_carrier)`, or `marginal_cost` time series spanning periods. |
| **Degradation** (PV, battery, electrolyser stack) | Absent | Static `efficiency`, `standing_loss`. The storage-cycling tab counts cycles (`services/compare/storage_cycling.py`) but nothing is priced or degraded. |
| **Replacement cycles** | Absent | `lifetime` + `build_year` retire assets per period (`period_utils.active_period_years`). Vintages via `vintage_bounds` (`services/vintage_service.py`, `routers/vintage.py:65`) allow re-building, but there is no explicit replacement-cost schedule (e.g. stack at year 10). |
| **Salvage / residual value** | Absent (implicit) | Annuity accounting charges only the years within the horizon, which is implicit salvage. It is not reported. |
| **Incentives / tax credits / grants** | Absent | Could be hand-netted into `capital_cost`/`marginal_cost`. Negative `marginal_cost` is possible, and `curtailment_cost` behaves as a dispatch subsidy. |
| **Tariffs: TOU energy** | Partial (workaround) | Grid import modelled as a Generator or Link with a time-varying `marginal_cost` series (`TimeSeriesManager.tsx:86-114`). |
| **Tariffs: demand / capacity / network charges** | Absent | Only through admin-gated `extra_functionality_code`, which runs arbitrary Python constraints and is disabled by default (`PYPSA_GUI_ALLOW_USER_CODE`, `solver_service.py:1445-1495`). A capacity charge could be approximated as `capital_cost` on an import Link's `p_nom` (annualised, not monthly peak). |
| **PPA pricing** | Absent | Could be emulated by fixed `marginal_cost` on a contracted generator. No contract object or settlement, and no revenue at contract price. |
| **Revenue stacking: energy arbitrage** | Present (LP-internal) | Storage `spread_eur_per_mwh`, discharge revenue and charge cost at **model** nodal duals (`asset_economics.py:563-596`). Not at an exogenous market price unless the user builds a market bus. |
| **Revenue stacking: ancillary (FCR/aFRR/mFRR), capacity market** | Absent | Planning reserve margin (`reserve_margin`, `solver_service.py:170`) is an adequacy constraint, not a priced market. ELCC/firm MW exist (`services/adequacy/elcc.py`), but no capacity payment. No operating-reserve co-optimisation (grep for spinning/operating reserve in `services/solver*` returned nothing). |
| **Curtailment revenue loss** | Partial | Curtailed MWh (`services/results/curtailment.py`, Asset Detail `curtailed_mwh`, `registry.py:143`) and capture rate exist. No € value of curtailed energy. `curtailment_cost` is an LP penalty. |
| **Carbon pricing scenarios** | Partial | Scalar or per-period CO2 price; CO2 cap GlobalConstraint with a shadow price. Scenarios only by forking projects; no carbon-price sweep. |
| **LCOE / LCOS / LCOH** | Present | See section 1. |
| **Merchant market price input / price forecast** | Absent as a concept | Prices are **outputs** (LP duals). The only exogenous-price path is marginal-cost series on import/export assets. |

**Currency inconsistency (minor):** the generator creation form labels costs `$/MWh` and `$/MW` (`FE layout/CreationForm.tsx:61-62`), while everything else is € (`asset_economics` returns `"currency": "EUR"`, `asset_economics.py:939`). There is no currency or FX setting.

---

## 3. Investment decision framing

- **No "business case" view.** No screen answers "should I invest in X / what is my ROI". The closest pieces:
  - **Optimiser decides investment.** Extendable assets (`p_nom_extendable`, bounds, per-period vintage bounds) let the LP pick the least-cost *system* build. This is a social-planner, cost-minimising objective, not an investor-return objective.
  - **`explain_investment` chat tool** (`services/chat_tools.py:4295`) fuses the sizing bound (which constraint stopped the build), Asset Detail KPIs (revenue, net profit, LCOE), bus prices, CO2 caps and congestion. It is a narrative "why did the LP build this" tool, not a financial appraisal.
  - **Economics tab "Net profit"** plus avg capture price vs System LCOE (`Economics.tsx:733-761`). The hint text explicitly frames "capture above LCOE means market prices cover production cost".
- **Scenario compare:**
  - `routers/compare.py:309 /projects/{name}/results-summary` loads a saved project's `network.nc` transiently and computes 9 summaries: capacity, dispatch, loading, prices, emissions, economics, curtailment, lost load and storage cycling.
  - `/compare-state` (`:54`) is an overview.
  - FE `pages/CompareView.tsx` is **strictly pairwise A/B** (`TABS` `:40-51`) with signed `Delta` cells (`:3381`).
  - Scenario tree: `FE pages/ScenariosPanel.tsx` (fork via `parent_project`, `scenarioDelta` objective delta vs parent at `:132`, "Solve subtree" batch via the solve queue, `services/solve_queue.py`).
  - Chat `compare_scenarios` (`chat_tools.py:2358`) produces a headline A vs B diff.
  - There is no N-way comparison matrix, no scenario ranking, and no incremental (A−B) NPV/IRR.
- **Sensitivity / parametric:**
  - No tornado, spider, or cost/price parametric sweep.
  - The chatbot research ranks "Guided what-if (clone → mutate → solve → compare)" as #7 (`pypsa-gui/CHATBOT_FEATURE_RESEARCH.md`). `plan_what_if`, `generate_run_report`, `diagnose_results`, `solve_overview` and `sanity_check_results` were registered but never implemented and have been **removed** (`chat_tools.py:4494-4515`).
  - Existing sweeps are all reliability-oriented:
    - **ε-constraint cost-vs-availability frontier** (`services/adequacy/frontier.py`, ≤12 points, `MAX_FRONTIER_POINTS` at `:45`; `/results/frontier` `routers/results.py:983-1017`; FE `FrontierPanel.tsx`). This is the closest thing to an investment trade-off curve: system cost vs ENS target.
    - Contingency sweep (`services/adequacy/sweep.py`, ≤20; fixed capacity; ΔEUE × VoLL).
    - Class-C stress (`stress.py`: load / availability multipliers).
    - Coupling and margin loops (`coupling.py` MAX_LOOP_SOLVES=8).
    - Campaign budgets chaining studies (`campaign.py`).
- **Monte Carlo:** `services/adequacy/mc.py` (≤2000 draws) and `mc_zonal.py` sample **unit outages only** (two-state chains) for LOLE/EUE and ELCC. There is **no stochastic price, cost, demand-growth or weather-year MC** for economics.
- **Discrete option optimisation:**
  - EH redundancy enum (`services/adequacy/redundancy.py`: `base`, `n1_generation`, `n1_conversion`, `parallel_storage`; "cost vs achieved ENS") plus an outer-loop selection of train counts.
  - **Economics are `cost_basis: "synthetic_placeholder"`**: hard-coded capital_cost 60 / 40 / 80×trains and marginal_cost 180 (`redundancy.py:282, 330-474`).
  - Levers: `levers.py` import cap {0, 25, 50 MW} and storage duration {4, 24, 72 h}.
  - Otherwise investment is continuous LP sizing. MILP exists only for unit commitment; there is no binary build decision or lumpy-unit sizing.

---

## 4. Study and report outputs

| Output | Where | Format |
|---|---|---|
| `ReferenceDesignReport` (Energy Hub) | `models/energy_hub.py:256`, `services/adequacy/eh_report.py:193 assemble_reference_design_report`, `export_reference_design` (`:251`, fixed `EXPORT_KEYS` `:27`), `GET /results/eh_reference_design` (`routers/results.py:1435`), FE `EhReferenceDesignPanel.tsx` | JSON; CSV of frontier and FMEA-top rows (`EhReferenceDesignPanel.tsx:225, 285`). Sections: target, certification, cost, frontier, sizing, redundancy, levers, dtc, fmea_top, **tea**, gates, multi_energy. |
| TEA block | `TeaBlock` (`models/energy_hub.py:240`), `compute_tea` (`eh_report.py:137`) | LCOE = cost_at_target / served energy (excluding shed), plus optional fleet LCOH €/kg. That is the entire TEA: no NPV/IRR ("no second cost engine", spec decision 9). |
| AdequacyReport and study write-up | `services/adequacy/report.py`, `study_report.py` (structured sections with `required_disclosures`, `not_established`, `evidence_gaps`; "writes no prose — the caller narrates"); chat `build_study_report` (`chat_tools.py:3921`) | JSON consumed by the chat LLM for narrative. |
| FMEA worksheet | `routers/adequacy_worksheet.py:79-162` (worksheet, stress scenarios, asset health) | JSON sidecar; FE `FmeaTab.tsx`. |
| Asset Detail workbook | `routers/asset_results.py:24 export.xlsx`, `services/asset_results/export.py:206` (openpyxl) | XLSX per asset. |
| Chart and table exports | `FE pages/results/shared.tsx:802 downloadCSV`, `:822 downloadSVG`, plus PNG (78 CSV / 9 SVG / 9 PNG call sites) | CSV / SVG / PNG. |
| Network I/O | `routers/io.py:55-177` | NetCDF, CSV bundle, Excel, MATPOWER. |
| Project bundle | `routers/projects.py:3383 /{name}/bundle` | `.pypsaproj.zip`. |
| Chat agent exports | `export_to_excel` (`chat_tools.py:3737`), `export_to_csv`, `export_preview_png`, `export_chat_summary` (`:3851`, md/txt transcript only), `export_asset_results` | XLSX / CSV / PNG / MD. |
| gridspine handoff | `gridspine/handoff/` (PSS/E .raw v33, .dyr, contingencies.csv, ledger), `gridspine/readback/` | Engineering-study artefacts (planning → dynamics). **No cost or finance content** (`docs/superpowers/specs/2026-08-27-gridspine-design.md`). |

- **PDF / DOCX / PPTX generation: absent.** `python-docx`/`pypdf` are used only to *read* chat uploads (`requirements.txt:15-20`, `services/upload_service.py`).
- The "run-report markdown generation" idea is backlog #17 in `CHATBOT_FEATURE_RESEARCH.md` and was not implemented (`generate_run_report` removed, `chat_tools.py:4497`).
- The narrative client deliverable today is whatever the chat LLM writes from `build_study_report` / `explain_investment` / `compare_scenarios` output.
- Chat tool surface: about 112 tools (`chat_tools_schema.TOOLS`, `tools/_v6_tool_registry_dump.txt`). The finance-relevant read tools are `get_results(kind)`, `get_asset_costs`, `get_asset_results`, `explain_investment`, `compare_scenarios`, `get_project_results_summary`, `run_frontier_study`, `run_eh_study` and `build_study_report`. None computes NPV/IRR.

---

## 5. Use-case fit by client archetype

Shared modelling vocabulary:
- **Components:** Bus (AC/DC/H2/heat/gas), Line, Transformer, Generator (conventional, renewable), StorageUnit, Store, Link (electrolyser, fuel cell, power-to-heat, CHP, generic), Load (electric/H2/heat), GlobalConstraint.
- **Palette:** `FE layout/Sidebar.tsx:133-172`.
- **Templates:** only 3-bus, IEEE-14, Belgium, IEEE-39 (`routers/projects.py:1207`; built by `project_templates/_build.py`; `.nc` not tracked in git).
- **Not an archetype library:** `presets.json` holds **LLM provider presets** (Anthropic/OpenAI/…).
- **No technology cost database:** new assets default to capital_cost 0 (`CreationForm.tsx`).

| Archetype | Can model today | Missing to answer "invest? how much? ROI/value?" |
|---|---|---|
| **Data centre (+ waste-heat recovery)** | <ul><li>Must-run DC load as a Link with fixed `p_min_pu=p_max_pu` profile; waste heat → low-T bus; heat pump Link with bus2 extraction (COP); heat dump. Script only: `pypsa-gui/scripts/scaffold_dc_heatpump.py` (REST client, not in UI).</li><li>Reliability depth: EH packs strong_grid / weak_flexible / off_grid (`models/energy_hub.py:296-360`), DtC critical-load islanding (`services/adequacy/dtc.py`), FMEA, N+1 redundancy enum, MC LOLE certification, ELCC.</li><li>Heat revenue valued at heat-bus dual via Link netting.</li></ul> | <ul><li>No DC template or pack in the UI; no PUE/IT-load modelling; no UPS/genset autonomy cost model.</li><li>Redundancy economics are placeholders.</li><li>No tariff / demand-charge bill.</li><li>No heat-sales contract price.</li><li>No backup-power avoided-outage € valuation beyond VoLL.</li><li>No NPV/IRR/payback.</li></ul> |
| **Large C&I site** | <ul><li>Grid import as generator/link with TOU `marginal_cost` series.</li><li>On-site PV / BESS / CHP sized by the LP.</li><li>CO2 price.</li><li>Asset net profit at nodal duals.</li></ul> | <ul><li>Tariff engine: demand charges (monthly peak kW), capacity and network charges, standing charges, export tariffs / net metering.</li><li>Baseline vs project **bill savings**.</li><li>No utility-rate library.</li><li>No incentives or tax.</li><li>No cash-flow, payback or IRR.</li><li>Revenue is at internal duals, not the site's bill.</li></ul> |
| **Hydrogen (electrolyser + storage)** | <ul><li>Electrolyser / fuel-cell Links, H2 bus / Store / Load, `p_min_pu`, overnight-cost annuity.</li><li>Per-asset and fleet **LCOH €/MWh and €/kg** (`/lcoh`, Economics H2 panel, EH TEA).</li><li>Electricity input priced at corrected duals.</li></ul> | <ul><li>No stack degradation or replacement schedule; no water or O2 streams (only via marginal cost).</li><li>No H2 offtake contract price / H2 sales revenue at an exogenous price.</li><li>No subsidy (e.g. CfD, 45V) modelling.</li><li>No part-load efficiency curve.</li><li>No NPV/IRR.</li></ul> |
| **BESS + renewables (co-located)** | <ul><li>PV/wind + StorageUnit/Store; shared POI via bus plus import-limit Link (EH `import_overlay`, `eh_poc` role).</li><li>Arbitrage spread, LCOS, cycles, capture price / capture rate, curtailment MWh, ELCC firm MW.</li></ul> | <ul><li>Ancillary-service markets (FCR/aFRR/mFRR), capacity-market payments, revenue stacking with SoC reservation.</li><li>Degradation and augmentation.</li><li>Exogenous price forecasts.</li><li>€ curtailment loss.</li><li>PPA / tolling.</li><li>Grid-connection charges.</li><li>Merchant NPV/IRR and P50/P90 price scenarios.</li></ul> |
| **Microgrid / off-grid** | <ul><li>`off_grid` archetype pack (islanding via Class-B p_pu→0, MC LOLE certification, storage-duration lever 4/24/72 h).</li><li>Unit commitment (min up/down, start-up cost, ramping) for diesel.</li><li>VoLL and ENS cap; ε-frontier cost vs reliability.</li><li>Levers.</li></ul> | <ul><li>Fuel logistics / price escalation.</li><li>Generator minimum-runtime maintenance and replacement.</li><li>Battery degradation.</li><li>Multi-year project cash flows.</li><li>Least-cost vs diesel-only baseline comparison as a first-class "savings" view (possible only via two projects + Compare).</li><li>Generator sizing is continuous (no discrete unit sizes except the placeholder redundancy enum).</li></ul> |

---

## 6. Time resolution and horizon relevant to investment

- **Multi-period capacity expansion:**
  - `SolverConfig.multi_investment_periods`, `investment_periods` (`solver_service.py:~200`).
  - Snapshots MultiIndex (`routers/network_time_axis.py:533 set_multi_period_snapshots`, `:807 investment_periods`).
  - `investment_period_weightings` `years` and `objective` default to **1.0 / 1.0** (`network_time_axis.py:554, 941-944`). The user must set years or the horizon is under-represented.
- **Foresight** (`solve_strategy`, `solver_service.py:338-378`):
  - `full` (perfect foresight over all periods).
  - `rolling` (operational rolling horizon; rejected with multi-period).
  - `myopic` (sequential per-period LP with capacity freeze).
  - Limited foresight via `lf_aggregate_future` (tsam representative blocks for future periods, `services/time_aggregation_service.py`; driver `services/solver/myopic.py`).
  - Myopic e2e finding: `docs/superpowers/findings/2026-08-05-myopic-foresight-e2e.md`. Reporting uses `cost_totals.horizon_system_cost` because the LP objective under-counts under myopic.
- **Representative periods:** sample representative weeks (`network_time_axis.py:639 /snapshots/sample_weeks`), snapshot weightings CSV (`:236-393`), spatial clustering (`routers/clustering.py:279`). Energy uses the `generators` weight column and cost uses `objective` (`period_utils.py:80`).
- **Vintages and lifetimes:**
  - Per-period capacity bounds expand to vintage rows with `build_year` (`services/vintage_service.py`).
  - Per-vintage results (`routers/vintage.py:65`).
  - Assets masked per period by `build_year`/`lifetime` (`period_utils.active_period_years`), consistently in capex roll-ups (`services/economics.py:79` `active_years_of`).
- **Where the discount rate is applied:**
  1. **Annuity** for `overnight_cost` assets: `overnight × annuity(discount_rate, lifetime) × nyears` (PyPSA `periodized_cost`). The global `cfg.discount_rate` (default **7%**) fills blank per-asset `discount_rate` transiently (`periodized_costs.py:~240-270`). Assets priced via `capital_cost` directly are unaffected.
  2. **Cross-period PV weighting** (opt-in `auto_discount_periods`, default **False**): `ipw.objective[P] = (1+r_real)^-(P-P0) × years[P]`, with `r_real = (1+r)/(1+inflation)-1`. PV is taken at period start × years, which approximates the within-period geometric series (`services/solver/assumptions.py:641-695`). It is applied to both CAPEX and OPEX in the LP, and it is **off by default**, so multi-period LPs are undiscounted unless the user enables it or edits the objective weights.
  3. **Reporting PV of upfront CAPEX**: `capex_lifetime` uses per-asset `(1+r)^-(build_year - first_period)` (`periodized_costs.py:330-395`).
  4. **Other reporting is undiscounted.** Every other reported horizon total (revenue, OPEX, fixed cost, net profit, LCOE) is an **undiscounted** sum of per-year values × `years`. LCOE is therefore Σcost/ΣMWh (undiscounted energy), not a discounted-energy LCOE.
  5. `objective_decomposition` exposes `period_weighting_adjustment_eur` to bridge objective (discounted) vs years (undiscounted) bases.
- **Missing:**
  - No calendar-year expansion (periods are coarse blocks).
  - No construction period or IDC, and no commissioning ramp.
  - No distinct financial discount rate vs social discount rate in reporting; discounted LCOE and NPV are not computed.
  - `default_lifetime` 25 yr is a fallback only for overnight-priced assets.

---

## Gap table

| Feature | Status | Evidence (file:line) | Notes |
|---|---|---|---|
| Annuitised CAPEX (overnight → CRF) | present | BE `services/solver/periodized_costs.py:29,397` | Single CRF home; transient discount-rate fill. |
| FOM in fixed cost, reconciled to LP objective | present | BE `services/economics.py:207`; `docs/superpowers/findings/2026-09-27-fom-missing-from-fixed-cost.md` | Fixed 2026-09-26. |
| OPEX / VOM / fuel (as marginal cost) | present | BE `services/results/cost_breakdown.py:94` | No separate fuel-price object. |
| UC start-up / shut-down costs | present | BE `models/schemas.py:274-289`, `services/results/unit_commitment.py` | MILP. |
| CO2 price (scalar / per period) and cap shadow price | present | BE `services/solver_service.py:147-155`; `services/results/emissions.py:328` | |
| Per-asset revenue, net profit | present | BE `services/results/asset_economics.py:33,431-444` | Valued at LP duals (merit-order corrected). |
| LCOE / LCOS | present | BE `asset_economics.py`; `services/compare/economics.py:540` | Undiscounted Σcost/ΣMWh. |
| LCOH (€/MWh, €/kg) | present | BE `services/results/lcoh.py:25,264` | LHV 33.33 kWh/kg. |
| Link / converter economics | present | BE `asset_economics.py:724`; `docs/superpowers/findings/2026-07-31-link-economics-missing.md` | Net revenue; all-in unit cost. |
| Objective decomposition / audit | present | BE `routers/results.py:263`; `services/results/objective_decomposition.py` | |
| Per-period, per-carrier, per-asset roll-ups | present | BE `cost_breakdown.py:682-727`; `routers/results.py:298` | |
| PV of upfront CAPEX ("lifetime") | present | BE `cost_breakdown.py`; FE `pages/results/CapacityExpansion.tsx:174,949` | Upfront only. |
| System-cost waterfall | present | FE `CapacityExpansion.tsx:755-1060` | Annual basis. |
| Cross-surface golden tests | present | BE `tests/golden/`; `docs/superpowers/specs/2026-08-01-trustworthy-numbers-design.md` | Nine surfaces. |
| NPV (project cash flow) | absent | none (grep) | Only a label at `models/adequacy.py:297-299`. |
| IRR / ROI | absent | none | |
| Simple / discounted payback | absent | none | |
| Year-by-year cash-flow table | absent | none | `by_period` only. |
| WACC / debt-equity / financing | partial | BE `solver_service.py:133`; per-asset `discount_rate` `models/schemas.py:141` | Annuity rate only. |
| Depreciation / tax | absent | none | |
| Inflation | partial | BE `solver_service.py:134-145`; `services/solver/assumptions.py:657-670` | Only a real/nominal conversion in the PV weight, gated by `auto_discount_periods`. |
| Cost / price escalation | partial | FE `pages/TimeSeriesManager.tsx:86`; BE `co2_price_per_period`, `load_scalers` | Manual via series or per-period dicts. |
| Degradation | absent | none | |
| Replacement cycles | absent | BE `services/vintage_service.py` (vintages only) | |
| Salvage / residual value | absent | implicit in annuity | Not reported. |
| Incentives / tax credits | absent | none | "Subsidy" hits are the curtailment wrapper. |
| TOU energy tariff | partial | FE `TimeSeriesManager.tsx:86,114` | Time-varying marginal cost on import asset. |
| Demand / capacity / network charges | absent | BE `solver_service.py:1445-1495` (`extra_functionality_code`, disabled by default) | |
| PPA pricing | absent | none | |
| Energy arbitrage revenue | present (LP duals) | BE `asset_economics.py:563-596` | spread_eur_per_mwh. |
| Ancillary services (FCR/aFRR), capacity market | absent | none | PRM and ELCC exist but are unpriced (`services/adequacy/elcc.py`). |
| Curtailment revenue loss (€) | absent | BE `services/results/curtailment.py` (MWh only) | |
| Carbon-price scenarios | partial | BE `solver_service.py:147-155` | Per-period values; no sweep. |
| Business-case / "should I invest" view | absent | closest: BE `services/chat_tools.py:4295 explain_investment`; FE `pages/results/Economics.tsx:733-761` | |
| Scenario compare | present (pairwise) | BE `routers/compare.py:309`; FE `pages/CompareView.tsx:40-51,3381` | A/B only; 10 tabs. |
| Scenario tree / batch solve | present | FE `pages/ScenariosPanel.tsx:132,803`; BE `services/solve_queue.py` | |
| Parametric cost / price sensitivity, tornado | absent | removed stub `plan_what_if` BE `chat_tools.py:4494-4515` | |
| Cost-vs-reliability frontier | present | BE `services/adequacy/frontier.py:45`; `routers/results.py:983-1017` | ≤12 points. |
| Monte Carlo on prices / costs | absent | BE `services/adequacy/mc.py:68` (outage MC only) | |
| Discrete option enumeration | partial | BE `services/adequacy/redundancy.py:282,474`; `levers.py` | Placeholder costs. |
| Energy-Hub archetype packs | present | BE `models/energy_hub.py:296-360`; `services/adequacy/archetypes.py` | strong_grid / weak_flexible / off_grid. |
| TEA block in EH report | partial | BE `services/adequacy/eh_report.py:137`; `models/energy_hub.py:240` | LCOE + LCOH only. |
| ReferenceDesignReport / study report | present (JSON) | BE `eh_report.py:193,251`; `services/adequacy/study_report.py` | |
| PDF / DOCX / PPTX report | absent | none | docx/pdf are read-only uploads. |
| Run-report markdown | absent | `pypsa-gui/CHATBOT_FEATURE_RESEARCH.md` #17; BE `chat_tools.py:4497` | |
| XLSX / CSV / SVG / PNG / NetCDF / bundle export | present | BE `routers/asset_results.py:24`, `routers/io.py:55-177`, `routers/projects.py:3383`; FE `pages/results/shared.tsx:802-822` | |
| Technology cost database / defaults | absent | FE `layout/CreationForm.tsx` (defaults 0) | No technology-data integration in the GUI. |
| Industry templates (DC, C&I, H2, BESS, microgrid) | absent (DC script only) | BE `routers/projects.py:1207`; `pypsa-gui/scripts/scaffold_dc_heatpump.py` | Grid-textbook templates only. |
| Multi-period / myopic / limited foresight | present | BE `solver_service.py:338-378`; `services/solver/myopic.py`; `services/time_aggregation_service.py` | |
| Discounting in LP | partial (opt-in) | BE `services/solver/assumptions.py:641-695` | `auto_discount_periods=False` by default. |
| Currency handling | partial / inconsistent | FE `layout/CreationForm.tsx:61-62` ($); BE `asset_economics.py:939` (EUR) | No FX or currency setting. |
