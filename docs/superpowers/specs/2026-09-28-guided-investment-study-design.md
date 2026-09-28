# Design: Guided investment studies (the Study layer, the finance layer and the bound report)

**Date:** 2026-09-28
**Status:** design, awaiting review. No feature code written.
**Assessment it answers:** `docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md`
**Relationship to other specs:** builds on the FMEA adequacy design (`2026-08-27-solution-fmea-adequacy-design.md`), the Energy Hub reference design (`2026-09-14-eh-reference-design.md`), the Model Horizon guided steps (`2026-08-12-model-horizon-guided-steps-design.md`) and the trustworthy-numbers design (`2026-08-01-trustworthy-numbers-design.md`, whose roadmap item 3 "Study → one deliverable" this spec finally specifies). It replaces none of them.

**Goal.** Let a user with little optimisation or finance background state a decision question ("Do I need a BESS at this site? Do I need hydrogen? Should the data centre recover its waste heat? What is the project worth?"), be led step by step to a conclusion the tool computed, see why the conclusion holds and how robust it is, and leave with a client-ready report whose every number is bound to the model.

**One sentence of shape.** A **Study** is a new object above the existing Project: it owns a question, an assumptions ledger, a baseline and a small set of options, a solve budget, a set of computed findings (bill, pro forma, drivers, robustness) and a report. The existing workbench becomes the **expert view** of the same PyPSA network; nothing in it is removed.

---

## 1. What already exists (reuse)

Mandatory reuse, per the house rule "no second engine" and "one report builder".

| Existing capability | Where | Role in this design |
|---|---|---|
| Least-cost expansion with vintages, foresight modes, representative weeks | BE `services/solver_service.py`, `services/solver/myopic.py`, `services/time_aggregation_service.py` | The only sizing engine. Options are solved by fixing or freeing extendability, never by a second optimiser |
| Annuity, `overnight_cost`, `lifetime`, `build_year`, `overnight_cost_pv`, `active_period_years` | BE `services/solver/periodized_costs.py`, `services/period_utils.py` | Source of every CAPEX figure in the pro forma; the pro forma expands these to calendar years, it does not re-derive them |
| Cost breakdown, asset economics, LCOH, objective decomposition, nine reconciled surfaces with golden tests | BE `services/results/*`, `services/economics.py`, `tests/golden/` | Source of OPEX, FOM, revenue at duals, LCOE/LCOS/LCOH; the pro forma becomes the tenth surface and joins the golden matrix |
| Scenario fork, solve queue, pairwise compare | BE `routers/projects.py` scenarios, `services/solve_queue.py`, `routers/compare.py` | Option solves are queued jobs on forked projects; compare summaries feed the option table |
| Campaign budget, abort at stage boundaries, restore guarantees | BE `services/adequacy/campaign.py`, `*_runner.py` | The Study runner is a campaign; every multi-solve stage inherits budget, abort and restore |
| Bisection over re-solves, ε-frontier | BE `services/adequacy/frontier_loop_runner.py`, `frontier.py` | Break-even thresholds are a bisection on one ledger input; the frontier pattern generalises to any one-dimensional sweep |
| Parametric stress multipliers | BE `services/adequacy/stress.py` | Tornado runs are Class-C style multipliers on ledger inputs |
| Sequential Monte Carlo, ELCC, DtC islanding, VoLL slack | BE `services/adequacy/mc.py`, `elcc.py`, `dtc.py`, `services/results/lost_load.py` | Resilience value: survival curve from the MC hourly samples; monetised outage cost from VoLL or a damage function |
| Completeness enum, honesty-note tuples, null-not-zero (ADR-0001), engine and fidelity badges | BE `models/energy_hub.py`, `models/adequacy.py`, FE `pages/results/adequacy.tsx`, `FmeaTab.tsx` | Every contract below carries `status: ok | not_established | skipped` and honesty notes; every figure carries `engine` and `fidelity` |
| Study report contract (`required_disclosures`, `not_established`, `evidence_gaps`) and the EH report assembler | BE `services/adequacy/study_report.py`, `eh_report.py` | The `DecisionReport` assembler is the same builder extended, not a parallel one |
| `explain_investment` (binding-constraint reasons, reading notes), `compare_scenarios`, `start_campaign` | BE `services/chat_tools.py` | Deterministic "why" facts; the copilot narrates from these, and the UI shows them without the copilot |
| Guided step chrome, summary-first landing, pure step predicates | FE `pages/modelHorizon/StepShell.tsx`, `HorizonSummary.tsx`, `modelHorizonModel.ts` | The intake wizard and the hub reuse the rail, the Advanced disclosure and the "entry state from the model, not from UI state" rule |
| Stage list with per-stage status, assumptions ledger with measured/datasheet/assumed counts | FE `pages/GridspinePanel.tsx` | The Study run screen and the ledger's provenance column |
| Shared caveat strings shown at input and read-back | FE `pages/results/adequacy.tsx` (`RESERVE_MARGIN_CAVEAT`, `ensTargetWarning`) | Every honesty note in this design is one string, rendered in both places |
| New-project wizard with a study-kind picker | FE `layout/NewProjectWizard.tsx` | The question cards replace the kind picker's first screen |
| PyPSA-Eur technology cost data | repo root `rules/retrieve.smk::retrieve_cost_data` (`costs_{year}.csv`), `data/custom_costs.csv` | Seed of the assumptions library, with source and year already in the file |
| RAM rate library with `rate_source` provenance chips | BE `services/adequacy/` (EH P7) | The pattern for every library entry: value, unit, source, year, range |
| PageKit, Dialog primitive, toasts and undo, command palette | FE `components/PageKit.tsx`, `components/Dialog.tsx`, `utils/toasts.tsx` | UI primitives; no new design system |
| Chart export to SVG/PNG, Asset Detail XLSX writer | FE `pages/results/shared.tsx`, BE `services/asset_results/export.py` | Report figures are the same chart objects; the pro forma workbook reuses the openpyxl path |

## 2. Decisions (pinned)

| # | Decision |
|---|---|
| 1 | **A Study is a first-class object above Project.** It references a base Project, owns forked option Projects, a ledger, findings and a report. Projects and the workbench are unchanged. This is the "slot in the IA" the FMEA spec §8.1 said a study lacks. |
| 2 | **Question-first entry.** A study starts from a `DecisionQuestion` template (v1 set in §6). "Custom question" and "open blank model" remain available so experts are never trapped. |
| 3 | **The finance layer is a post-process over solved networks: a cash-flow expander, not a second cost engine.** It takes the reconciled cost surfaces and the pro-forma settings and expands them to calendar years. It never re-derives an annuity, a CAPEX or an OPEX. It is the tenth economic surface and joins the golden-fixture matrix. |
| 4 | **Every headline is relative to a named baseline.** The baseline is a solve of the same network with the option's assets fixed at their existing capacity (or absent). The template names the baseline in plain words ("grid supply with the existing diesel backup"). An absolute system cost is never a headline. |
| 5 | **Options are solved by extendability, never by a second optimiser.** "Best with BESS" frees the BESS; "Best without" fixes it to zero. Discrete choices (redundancy trains, storage duration) stay enumerated, as the EH design already decided. |
| 6 | **Verdict classes are three and computed:** `recommended`, `marginal`, `not_recommended`. `marginal` is declared when the sign of the headline metric flips inside the ledger's plausible ranges (from the tornado), not by a hand-set threshold. |
| 7 | **Financial conventions are explicit toggles with one labelled default:** real terms, pre-tax, without subsidy (the IRENA convention) is the default; nominal, post-tax and with-subsidy are opt-in and every figure states which basis it is on. Mixing bases across screens is a defect. |
| 8 | **Perspective is a ledger field.** `perspective: site_owner | developer | investor | multi_party`. v1 implements `site_owner` and `developer`; multi-party cash-flow assignment is v2. |
| 9 | **Tariffs are a first-class object, and demand charges are an LP constraint, not a post-process.** A `Tariff` has energy bands, a demand charge on the billing-period peak import, fixed and capacity charges and export compensation. The peak-import variable is injected the way `capex_budget_per_period` already injects a constraint. The bill calculator then prices the solved import series on the same tariff. |
| 10 | **Revenue at duals stays, labelled.** Existing "revenue at model prices" figures keep their meaning and gain the label "valued at the model's own prices". Bill savings and contract revenue are separate, named streams. The value-stream waterfall shows which is which. |
| 11 | **Assumptions live in a versioned library with provenance, and every input has a status.** Ledger rows carry value, unit, source, source year, plausible range, provenance (`library | user | imported | measured`) and status (`default | customised | needs_attention`). "Using defaults" is a visible state, never silent. |
| 12 | **Maturity badge on every study.** `screening | feasibility | design`, derived from ledger provenance (share of key inputs still at library defaults, load measured or synthetic, costs quoted or generic), with an indicative accuracy band in the AACE 18R-97 spirit. It is shown on the verdict card and in the report. |
| 13 | **Robustness is shown as thresholds and frequencies, not error bars.** Tornado (one-way, sorted by swing), break-even readouts ("hydrogen pays if electrolyser CAPEX < X"), an optimal-option map over two variables, and a quantile dotplot for probability of positive NPV when scenario sets exist. |
| 14 | **Staged fidelity.** `quick_screen` (representative weeks, single year, one weather year) then `full_study` (8760 h, multi-year, scenario sets). Every figure names the fidelity that produced it. The quick screen runs inside a default budget of a few solves. |
| 15 | **One report builder.** `assemble_decision_report` extends the existing study-report assembler. Sections carry completeness, honesty notes come before numbers, and every figure in the document is a reference to a fact ID resolved at render time. Hand-typed numbers are rejected by the renderer. |
| 16 | **The copilot narrates, it never computes.** It receives the facts bundle and must cite a fact ID for every figure; the renderer substitutes the value. AI-drafted paragraphs carry an AI label and a `reviewed` tick, and nothing exports with an unreviewed AI paragraph. The deterministic facts layer must produce a complete report without the copilot present. |
| 17 | **Plain label first, technical term as subtitle, unit always.** Every novice-facing field is defined once in a vocabulary map (§8.1). The expert view keeps PyPSA names as primary labels. |
| 18 | **Per-step apply, not a transactional wizard.** Same reasoning as the Model Horizon design: the backend has no transaction; each intake step commits to the study record. |
| 19 | **Export formats v1:** DOCX and PDF for the report, XLSX for the pro forma and the ledger. PPTX and the read-only interactive share link are v2. |
| 20 | **Nothing here changes what any existing control does, any existing API contract, or any existing number.** New endpoints are additive. Existing screens gain labels and caveats only. |

## 3. The guided flow

Principle: a short linear wizard to **frame**, a hub to **build**, answer-first screens to **decide**, a bound report to **communicate**. The Expert view (the existing canvas, tables and results) is one click away from every screen and edits the same network.

| # | Screen | What it shows | The novice needs | The expert unlocks |
|---|---|---|---|---|
| 0 | **Start** (replaces the first screen of the new-project wizard) | Question cards from §6 plus "Custom question" and "Open blank model"; recent studies with maturity badge and verdict chip | One sentence per card and an example of what they will get ("a yes/no, a size, a value, a report") | Blank model goes straight to the canvas |
| 1 | **Intake** (one thing per page, five to eight steps, reusing `StepShell`) | Location and grid zone → what exists today (connection capacity, existing assets) → consumption (upload, typical profile by sector, or sketched) → goal (minimise cost, maximise value, hours of resilience, CO2) → horizon and perspective → check your answers | Presets by sector ("data centre, 10 MW IT load, PUE 1.3"); consequence questions ("hours of backup", not kWh); each default shown with its source | Direct entry of snapshots, weights, carriers; jump to Model Horizon |
| 2 | **Study hub** (task list) | Sections with status chips: Site and grid, Demand, Options, Prices and tariffs, Finance, Assumptions, Run, Findings, Report; the maturity badge; "these N assumptions matter most for this question" | Sections ranked by relevance to the question; "Using defaults" as a visible status | Any section's Advanced layer; the canvas |
| 3 | **Options** | Cards per candidate technology from the template (BESS, electrolyser + H2 store, heat pump + district-heating connection, on-site generation) with include / exclude / force size, and low / central / high cost presets with source year | Friendly names, one line of what the option does, the preset's source | `capital_cost`, `overnight_cost`, `efficiency`, `p_nom_max`, `max_hours`, `standing_loss`, custom components |
| 4 | **Prices and tariffs** | Tariff picker (library or custom): energy bands, demand charge, fixed and capacity charges, export compensation; market price series where relevant; carbon price | Bill preview on the existing load before any option ("your baseline bill is X per year"); plain explanations of each charge | Time-varying `marginal_cost` series, custom constraints, price scenario sets |
| 5 | **Finance** | Perspective, discount rate or WACC, lifetime, inflation, basis toggles (real/nominal, pre/post tax, with/without subsidy), debt share and tenor, replacement schedule | One labelled convention as default; a sentence of what each toggle changes | Depreciation schedule, DSCR target, salvage rule, per-asset discount rates |
| 6 | **Assumptions review** | The ledger filtered to the key drivers; defaults versus customised; plausible ranges; provenance | "Please check these six numbers"; upload metered load to raise maturity | All rows, bulk edit, CSV round-trip, pin a library version |
| 7 | **Run** | Fidelity choice (quick screen or full study), the solve budget in plain words ("about 4 solves, typically 3 minutes"), stage list with status, abort, restore guarantee | Plain-language solver status; failure explanations from the failure taxonomy | Solver, options, clustering, logs |
| 8 | **Verdict** (answer first) | Verdict chip and one sentence; recommended sizes; three headline KPIs versus the baseline; top three drivers; maturity badge; the one caveat that matters most | "Yes: a 20 MW / 40 MWh battery is worth building. It adds EUR 4.1 M of value at 7 percent, pays back in 6.5 years, and earns mostly from peak-charge reduction." | Full KPI set, objective decomposition, duals, `explain_investment` reasons |
| 9 | **Why and how** | Value-stream waterfall; cumulative cash-flow chart with payback and replacement years marked; typical-week dispatch of the option; categorized option table (baseline / best with X / best without X / both) | Captions written for the reader; the zero-profit-by-construction note where net profit at duals is shown | Every time series and per-component result; the network map |
| 10 | **How robust** | Tornado over the flagged ledger inputs; break-even readouts; optimal-option map over two chosen inputs; dotplot when scenario sets ran | "What would have to be true" sentences; "in 17 of 20 futures the battery pays" | Custom sweeps, Monte Carlo settings, raw tables |
| 11 | **Scenarios** | Pinned baseline study; scenario studies as deltas with an "inputs that differ" list | Presets ("high-price future", "cheap batteries") | Arbitrary trees and batch runs (the existing Scenarios panel) |
| 12 | **Report** | Template-driven draft (§7), bound figures and charts, AI-drafted prose marked and reviewable, a stale flag if inputs changed, export DOCX/PDF and the XLSX pro forma and ledger | A good default template and tone presets (client, board, technical) | Section library, custom sections, methodology appendix, model hash |
| 13 | **Copilot** (docked on every screen) | Grounded explanations citing fact IDs, with an AI label | "Why no hydrogen?", "What is IRR?" | "Show me the binding constraint", "Run a sweep over X" (a proposal that needs confirmation) |

Two behaviours change in the existing workbench, both additive: after a successful solve inside a study the Verdict screen opens (today nothing opens); and the Economics tab and Asset Detail gain the zero-profit-by-construction caveat that today lives only in a chat reading note.

## 4. Contracts (normative)

All payloads follow ADR-0001 (unresolvable figure is `null` plus a flag), carry `status: ok | not_established | skipped` per section, carry `engine`, `fidelity` and `basis` on every figure, and carry an `honesty_notes` tuple. Field lists are the minimum; names are proposals for the plan to pin.

### 4.1 `Study`

```
study_id, name, question_id, base_project, option_projects[], perspective,
ledger_version, fidelity_last_run, budget {solves_max, solves_used},
maturity {class, accuracy_band, reasons[]}, findings_ref, report_ref,
created_by, created_at, stale: bool, stale_reasons[]
```

### 4.2 `DecisionQuestion` (template)

```
question_id, title, one_line, archetype (maps to EH pack where relevant),
mandatory_inputs[], defaults[] (ledger rows), network_pack (template builder),
baseline_definition {text, fixed_assets[]},
options[] {option_id, label, one_line, free_assets[], fixed_assets[], discrete_choices[]},
headline_metrics[] (ordered), value_streams[] (ordered), key_drivers[] (ledger keys),
report_template_id
```

### 4.3 `AssumptionsLedger`

```
ledger_version, rows[] {
  key, label, technical_name, value, unit, basis (real|nominal),
  source, source_year, source_url|null, range {low, high}|null,
  provenance: library|user|imported|measured,
  status: default|customised|needs_attention,
  sensitivity_flag: bool, changed_by, changed_at }
```

The ledger is the report's assumptions chapter and the input to the tornado. Rows the user edited are visually distinct from defaults.

### 4.4 `Tariff`

```
tariff_id, name, source, source_year, currency, billing_period (month|year),
energy_bands[] {label, price_per_mwh, applies (time rule)},
demand_charge {price_per_mw_per_period, basis: billing_period_peak|annual_peak|ratchet {months, share}}|null,
capacity_charge {price_per_mw_per_year, basis: contracted|measured}|null,
fixed_charge_per_period, network_charges[] {label, price, basis},
export {price_per_mwh|series_ref, cap_mw|null}, connection_limit_mw|null,
honesty_notes[]
```

Engine contract: the demand charge adds a variable `peak_import[p]` per billing period with `import_t ≤ peak_import[p]` and the charge in the objective, injected through the existing constraint-injection path. The `BillCalculator` prices any import and export series on the same tariff and returns `annual_bill`, `by_component[]` and `by_period[]`. Baseline and option bills come from the same calculator, so "savings" is a subtraction of two numbers with one basis.

### 4.5 `InvestmentCase` (the pro forma)

```
case_id, study_id, option_id, perspective, basis {real|nominal, tax: pre|post, subsidy: excl|incl},
horizon_years, discount_rate, wacc|null, inflation|null,
years[] {year, capex, replacements, opex_fixed, opex_variable, fuel, co2_cost,
         bill_baseline|null, bill_option|null, savings, contract_revenue, market_revenue_at_duals,
         resilience_value|null, tax|null, depreciation|null, debt_service|null,
         net_cash_flow, discounted_cash_flow, cumulative_discounted},
kpis {npv, irr|null, payback_simple|null, payback_discounted|null, lcoe|null, lcos|null, lcoh|null,
      dscr_min|null, capex_total},
value_streams[] {label, annual_value, share, engine, basis},
sources {cost_breakdown_ref, asset_economics_ref, bill_refs[], mc_ref|null},
completeness {...}, honesty_notes[]
```

Rules: CAPEX lands in the build year from `overnight_cost` (or from `capital_cost` un-annuitised with the same annuity, labelled as derived); replacements come from the ledger's replacement schedule (stack at year N, battery augmentation); salvage is the un-depreciated share at horizon end and is reported, never hidden; `irr` is `null` with a flag when cash flows have no sign change; every stream names its engine (`lp_duals`, `bill_calculator`, `contract`, `mc_resilience`).

### 4.6 `OptionSet` and `Findings`

```
options[] {option_id, label, project_ref, solve_status, sizes[] {asset, p_nom_opt, e_nom_opt|null},
           system_cost, bill|null, case_ref, delta_vs_baseline {npv, payback, capex, co2}},
baseline {project_ref, solve_status, bill|null, case_ref},
verdict {class: recommended|marginal|not_recommended, sentence, headline_kpis[3], drivers[3], main_caveat},
robustness {tornado[] {key, label, low_value, high_value, npv_low, npv_high, swing},
            breakevens[] {key, label, threshold, direction, text},
            option_map|null {x_key, y_key, grid[][], winner[][]},
            dotplot|null {n_futures, n_positive, values[]}},
explain[] (from explain_investment: binding_constraint, reading_notes),
completeness, honesty_notes
```

### 4.7 `DecisionReport`

Assembled only by `assemble_decision_report(study)`, an extension of the existing study-report assembler. Sections (§7) each carry `status`, `facts[]` (fact_id → value, unit, basis, engine, fidelity), `figures[]` (chart refs) and `prose[]` (template paragraphs and AI paragraphs with `ai: true, reviewed: bool`). The renderer resolves every `{{fact_id}}`; an unresolved reference or a bare number in prose fails the render with the offending paragraph named.

## 5. Engines: how each finding is computed

| Finding | Computation | Reuse |
|---|---|---|
| Baseline | Solve the study network with the option assets fixed (`p_nom_extendable=False`, `p_nom` at existing or 0) | solve queue on a forked project |
| Option solves | One solve per option with its assets extendable; discrete choices enumerated; all inside the campaign budget | `campaign.py`, EH `redundancy.py` outer loop |
| Bill | `BillCalculator` on the import and export series of each solved project | new, over `n.links_t` / `n.generators_t` |
| Pro forma | Cash-flow expander over `cost_breakdown`, `asset_economics`, bills, contracts and the ledger's finance rows; NPV, IRR by bisection, paybacks by crossing | new, over existing surfaces; joins golden tests |
| Value streams | Bill delta by tariff component (demand, energy, fixed, export); market revenue at duals from `asset_economics`; contract revenue from contract rows; resilience value from §5 resilience | existing plus the bill calculator |
| Drivers and verdict | Tornado from one-way re-solves (quick fidelity) or re-dispatch with fixed capacities (cheaper, labelled as such) over `sensitivity_flag` ledger rows; verdict from the sign of NPV and whether it flips within ranges | `stress.py` multipliers, campaign budget |
| Break-even | Bisection on one ledger input until NPV crosses zero | `frontier_loop_runner.py` pattern |
| Option map | Grid over two inputs, winner per cell by NPV; coarse grid, quick fidelity, labelled | campaign budget |
| Probability of positive NPV | Re-dispatch the chosen sizes over a scenario set (weather years, price series); count positive NPVs; dotplot | scenario sets are v2 data; `mc.py` hourly sampling pattern |
| Resilience value | Survival probability versus outage duration from sequential MC hourly samples; monetised outage cost as `ENS × VoLL` or a sector damage function from the library; the savings-sized versus resilience-sized cost gap as in REopt | `mc.py`, `dtc.py`, `lost_load.py` |
| Explain | `explain_investment` per built asset: binding constraint and reading notes, shown in the UI | `chat_tools.py` (lifted into a service so the UI does not need the copilot) |

## 6. Question templates (v1)

| Question | Baseline (plain words) | Mandatory inputs | Options | Headline KPIs | Value streams | Key drivers (tornado defaults) |
|---|---|---|---|---|---|---|
| **Do I need a BESS at my site?** | Grid supply on the chosen tariff with existing assets | Site and zone, tariff, load (upload or sector profile), connection limit | Best with BESS (power and energy free); best with BESS + PV; without | NPV vs baseline, payback, recommended MW / MWh | Peak-charge reduction, energy time-shift, export, ancillary (v2), resilience | Battery CAPEX per kWh, demand-charge price, energy price spread, discount rate, degradation |
| **Should I invest in hydrogen?** | Grid or on-site power with no electrolyser; H2 bought at a delivered price if there is an H2 demand | H2 demand or offtake, electricity source and price, electrolyser cost preset | Best with electrolyser + storage; with co-located RES; without | LCOH, NPV vs buying H2, break-even H2 price | H2 sales or avoided purchase, avoided CO2, grid services (v2) | Electrolyser CAPEX, electricity price, H2 offtake price, stack life, full-load hours |
| **Should the data centre recover its waste heat?** | Heat dumped; district network on its existing source (gas boiler or existing heat pump, chosen at intake) | IT load and PUE, heat-network temperature and demand, heat sale price or tariff | Heat pump + connection; direct low-temperature supply; without | NPV, payback, share of heat reused (EnEfG reporting) | Heat sales, avoided cooling electricity, avoided boiler fuel, CO2 | Heat price, heat-pump CAPEX, COP, connection cost, electricity price |
| **How should the data centre be powered before and after grid connection?** | Grid-only from the connection date with diesel backup | IT load, PUE, connection date and capacity, tariff, critical-load share, target hours of autonomy | On-site generation + BESS for bridging; BESS only; flexible connection (v2) | NPV, months of bridging covered, survival probability at target duration, 24/7 CFE share (v2) | Bridging revenue (avoided delay), peak reduction, backup value, grid services | Connection date, gas or fuel price, BESS CAPEX, VoLL, tariff |
| **What is my co-located BESS + renewables project worth?** | Renewables alone at the point of connection | Site, connection limit, RES profile or capacity, price series (library or upload) | With BESS (duration enumerated 1/2/4 h); without | NPV, IRR, LCOS, curtailment avoided | Arbitrage at price series, curtailment avoided, capacity payment from ELCC (v2), ancillary (v2) | Price spread, BESS CAPEX, degradation, connection limit |
| **Off-grid or weak-grid site: diesel-only or hybrid?** | Diesel-only with the existing fleet | Load, fuel price, site resource, hours of autonomy, reliability target | Hybrid PV + BESS + diesel; with H2 (v2); import-limited (EH `weak_flexible`) | NPV vs diesel, LCOE, ENS and LOLE at target | Fuel saved, maintenance, CO2, resilience | Fuel price, PV yield, BESS CAPEX, VoLL |

Each template maps to a network pack the way EH archetype packs map today (config plus overlays, not a new solver), and the data-centre and off-grid templates reuse the EH packs and DtC islanding directly.

## 7. The report

Structure (Minto: answer first):

1. Executive summary and recommendation: verdict, three KPIs, one chart, the main caveat, the maturity badge.
2. The question, the baseline and the options considered.
3. Recommended system and how it operates (typical week).
4. Economics: cumulative cash flow, pro forma summary, NPV, IRR, payback, LCOE/LCOS/LCOH, all on a stated basis.
5. What drives the result: value-stream waterfall, tornado.
6. Robustness: break-evens, option map, probability of positive NPV where available.
7. Reliability and resilience where the question includes it: ENS, LOLE, survival curve, FMEA top rows, with the screening-grade disclosure at the point of display.
8. Assumptions: the ledger, with provenance and the rows the user changed.
9. Limitations and next steps: maturity class, what would tighten it (metered load, quoted CAPEX, real climate years).
10. Appendix: model description, input summary, full tables, solver log excerpt, model and ledger hashes.

Rules: `required_disclosures` and `evidence_gaps` render before the numbers; every figure is a fact reference; charts are the same objects as in the Findings screens; the report shows a stale banner when any ledger row or network changed after the draft; AI paragraphs carry a label and a reviewed tick; the XLSX pro forma exports with formulas where the expander is linear so a finance reviewer can trace each cell (the SAM pattern), and with values otherwise, labelled.

## 8. Usability rules

### 8.1 Vocabulary map (maintained once, used everywhere in the guided flow)

| Plain label (unit) | Technical name | Note |
|---|---|---|
| Battery power (MW) | `p_nom` on StorageUnit / discharge Link | |
| Battery energy (MWh) / hours of storage (h) | `e_nom` on Store / `max_hours` | The guided flow prices energy and power separately; the pack builds Store + Link |
| Availability profile (share of capacity) | `p_max_pu` | |
| Minimum stable output (share) | `p_min_pu` | |
| Upfront cost (EUR/kW or EUR/kWh) | `overnight_cost` | The guided flow never asks for an annuity |
| Yearly fixed cost (EUR/kW/yr) | `fom_cost` | |
| Running cost (EUR/MWh) | `marginal_cost` | |
| Lifetime (years) | `lifetime` | |
| Discount rate / cost of capital (%) | `discount_rate` | Labelled real or nominal |
| Round-trip efficiency (%) | `efficiency_store × efficiency_dispatch` | Shown as one number, expandable |
| Unserved energy (share of demand) | `ens_cap_permyriad` | Entered as percent with the existing "‱" guard behind it |
| Value of unserved energy (EUR/MWh) | `voll` | |
| Grid connection limit (MW) | `p_nom` on the import Link with `eh_role = grid_import` | |
| Run the optimisation | "Run LOPF" | The button label in the guided flow |

### 8.2 Rules

- Units on every input; currency consistent (EUR unless the study says otherwise); the `€/MW` badge on the annuity field is corrected to `€/MW/yr` in the expert view too.
- Defaults are never silent: source, year and a "default" status chip on the field and in the ledger.
- Every caveat is one string, shown at the input and at the read-back, ending in a next action.
- Preflight for the guided flow adds four plain-language checks: storage in a one-snapshot model, no extendable asset, no price signal at the option's bus, an upfront cost typed into an annuity field (detected by magnitude against the library range).
- Solver failures are translated by the existing failure taxonomy; the guided flow shows cause and fix, and links to the offending object.
- After a solve in a study, the Verdict screen opens and a success toast names the verdict.
- No more than two disclosure layers in the guided flow; the Expert view is always one click away and edits the same network.
- Near-zero values render as the value, not as a dash (ADR-0001).

## 9. MVP slices

| Slice | Includes | Done when |
|---|---|---|
| **MVP-1: one question end to end** | `Study` object and hub; the BESS question template with a site pack; intake wizard; `AssumptionsLedger` seeded from technology-data with provenance; baseline and option solves under a campaign budget; `Tariff` with energy bands and a demand charge as an LP constraint; `BillCalculator`; `InvestmentCase` expander with NPV, IRR, paybacks on the real, pre-tax, no-subsidy basis; Verdict, Why-and-how (waterfall, cumulative cash flow, categorized table), tornado over five drivers at quick fidelity; `DecisionReport` to DOCX and PDF plus XLSX pro forma; the zero-profit caveat and unit-badge fixes in the expert view | A novice with a load file and a tariff reaches a verdict, a waterfall, a tornado and a DOCX report without opening the canvas; the pro forma reconciles to `cost_breakdown` on the golden fixture; the demand-charge constraint is verified by a test where the peak binds; a QA driver runs the journey over HTTP |
| **MVP-2: the archetypes** | Data-centre waste-heat and data-centre bridging templates (reusing EH packs and DtC), hydrogen template with LCOH and break-even H2 price, off-grid template; break-even bisection; optimal-option map; maturity badge; ledger CSV round-trip and library versions; scenario deltas with input diffs; post-tax and nominal bases; replacements and degradation rows; resilience value (survival curve and monetised outage cost) | Each template's QA driver passes; the report's reliability section carries the screening-grade disclosure; the maturity badge changes when metered load replaces a synthetic profile |
| **MVP-3: uncertainty, markets and sharing** | Price and weather scenario sets and the dotplot; ancillary-service reservation and capacity payment from ELCC; PPA and tolling contract rows; multi-party perspective; PPTX export; read-only interactive study link with consultant-chosen sliders; copilot narration with fact-ID validation | Probability-of-positive-NPV renders from a scenario set; every AI paragraph in an exported report has a reviewed tick; a shared link exposes only the chosen sliders |

Each slice follows the house protocol: adversarial plan review before code, TDD with the red error stated, an independent assessor gate (GO, GO WITH BINDING CONDITIONS, NO-GO) before the next slice, path-limited commits, one slice per pull request, and a findings note with before-and-after measurements.

## 10. Non-goals (v1)

- A second cost or sizing engine, or any optimiser other than the existing PyPSA solve path.
- Replacing the workbench, the Scenarios panel, the Compare view or the Adequacy and FMEA surfaces.
- A regional tariff database at Energy Toolbase scale; v1 ships a library format, a handful of seeded tariffs and import.
- Statutory-grade adequacy, real climate years, planned-outage Monte Carlo (still deferred by the FMEA and EH specs).
- Grid-connection and large-load screening as a study type (gridspine variant 2 stays planned).
- Controller-consistent dispatch as a product feature; v1 only labels perfect foresight and offers the existing rolling strategy.
- An LLM that computes, draws, or writes unlabelled prose into a client document.
- Billing, code signing, auto-update and the SaaS worker queue (unchanged from their own specs).

## 11. Open questions

1. Whose money is the default perspective (decision 8), and does the multi-party split need to be in v1 for the data-centre case?
2. The baseline for waste heat: gas boiler or existing heat pump on the district network. It changes every KPI on that template.
3. Which tariff library first: German capacity-based grid fees and EU balancing products, or US demand charges with the OpenEI database?
4. Does the company's equipment catalogue become the default assumptions library, and who owns its versions?
5. Should the copilot draft prose in client reports at all (decision 16 assumes yes with labels and review)?
6. Do the technology-data cost files at repo root satisfy the provenance format, or does the library need a curation step before they can be shown to clients?

## 12. Risks

| Risk | Mitigation |
|---|---|
| The tenancy defect in the user-timeseries store leaks one client's load into another's study | Fix or contain before any multi-user deployment of studies; the desktop build is also affected as a multi-project bug |
| The pro forma disagrees with the existing cost surfaces | It is the tenth surface and joins the golden-fixture and oracle tests from the trustworthy-numbers work; the FOM lesson (missing on every surface for weeks) is the reason |
| Demand-charge constraint changes solve behaviour for existing projects | It is injected only when a study attaches a `Tariff`; projects without a tariff are byte-for-byte unchanged, and a test pins that |
| Templates 404 outside the packaged app | The study packs are built by code at study creation, not shipped as gitignored NetCDF files |
| The copilot is absent (no key) | The facts layer, the Verdict screen and the report render without it; AI prose is an optional layer |
| Multi-solve studies are slow on a laptop | Quick-screen fidelity with representative weeks and a visible solve budget; full study is an explicit choice; the campaign abort and restore guarantees apply |
| Financial conventions get mixed | One labelled default basis, toggles that re-label every figure, and a render-time check that all facts in a section share a basis |
