# Investment-decision support and usability: gap analysis against commercial edge and grid-planning software

**Date:** 2026-09-28
**Status:** assessment (read-only research; no product code changed)
**Companion:** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md` (the design this assessment feeds)
**Raw research:** `docs/superpowers/research/2026-09-28-*.md` (six reports, one per research thread, with URLs and evidence tags)

## 0. Method and how far to trust this

Six parallel research threads produced this document: three read the repository (investment and economics inventory, UX audit, roadmap and plans) and three surveyed the market (behind-the-meter and C&I tools, utility-scale and grid-planning suites, guided-workflow UX patterns).

- **Repository claims** were checked in source, not in plan checkboxes. Where a plan said "done" the code was read. File paths cite `pypsa-gui/backend` (BE) and `pypsa-gui/frontend/src` (FE).
- **Market claims** come from web search extracts of vendor pages, help centres and papers. The session's network policy blocked direct fetches of nearly every vendor site (homerenergy.com, reopt.nrel.gov, energyexemplar.com, auroraer.com, siemens.com, digsilent.de, etap.com, nngroup.com and others). Only a few GitHub repositories were read in full (DER-VET, PRAS, REopt.jl, HyDesign, H2Integrate). Each market fact in the research files is tagged `[V]` (matches the cited page's extract), `[V-code]` (read in source), `[BK]` (background knowledge) or `[I]` (inference). Before quoting a competitor claim externally, open the cited URL.
- **Prices** were found only for HOMER Pro, Energy Toolbase, RETScreen and a search extract for PSS E. XENDEE, HOMER Grid, Gridcog, energyPRO and Schneider's microgrid tool do not publish prices.
- **Corrections to the brief we started with:** EnCompass belongs to Yes Energy (acquired from Anchor Power Solutions in December 2023), not Hitachi; Hitachi's "EnCompass" is a service-agreement brand. "PLEXOS Copilot" ships as PLEXOS Intelligence plus PLEXOS Pulse. "PowerFactory Monitor" is monitoring hardware; the relevant planning modules are Reliability, Generation Adequacy and Techno-Economic Calculation.

## 1. Summary

1. **The tool is a least-cost system planner and reliability certifier, not an investment-case builder.** It minimises total system cost as a social planner and certifies the plan on unserved energy and Monte Carlo loss-of-load expectation. Every commercial edge tool instead answers an investor's question: baseline versus proposed, cash flows, net present value, internal rate of return, payback, financing, and a report.
2. **The engine is not the gap. The layer above it is.** PyPSA already produces the physical answer (sizes, dispatch, duals, unserved energy). What is missing is a tariff and bill engine, a project-finance pro forma, a baseline-versus-option runner, sensitivity on economics, an assumptions library, a report generator and a guided workflow. All of these are post-processing and orchestration over the solved network, which matches the house rule "no second cost engine".
3. **No financial metric a client asks for exists anywhere in the codebase.** A repository-wide search for NPV, IRR, payback, cash flow, tariff, PPA, depreciation, degradation and incentives finds no implementation. The only related token is a period-basis label. Revenue in the Economics tab is valued at the model's own shadow prices, and an optimally sized asset shows about zero net profit by construction, which a novice reads as "no return".
4. **The tool leads where the commercial edge tools are weakest.** One engine chain runs capacity expansion, unit commitment, AC load flow, N-1 screening, adequacy with COPT and sequential Monte Carlo, ELCC, an IEC 60812 FMEA worksheet and a PSS/E hand-off. Only encoord SAInt comes close on breadth; market tools (PLEXOS, Aurora, Hitachi PROMOD) have no AC N-1, grid tools (PSS E, PowerFactory, ETAP) have no capacity expansion, and no vendor found offers an FMEA worksheet. The honesty doctrine (unresolvable figures ship as null, completeness enums, provenance badges) is stronger than anything the vendors advertise.
5. **Data-centre combined power-and-heat investment cases are white space.** No commercial tool found packages on-site power, bridging until the grid connection arrives, BESS, waste-heat sale to district heating and 24/7 carbon-free-energy matching in one case. The closest are XENDEE and HOMER Grid on power and Polysun, nPro and energyPRO on heat.
6. **The closest single analogue is Gridcog**, which compares scenarios by cash flow and IRR, assigns cash flows to project participants, models network charges, and supports perfect-foresight and uncertain dispatch. The closest open analogue is EPRI DER-VET, whose pro forma (tax, MACRS depreciation, replacements, end-of-life, MIRR, discounted payback) and 15 value streams were read in source.
7. **The current user path cannot reach an investment conclusion.** The workbench exposes roughly 80 panels, tabs and modals with equal weight, all four templates are transmission test grids (and return a developer 404 in a dev run), tooltips state that the audience is "a power-systems engineer using the GUI", and after a successful solve nothing opens and no headline appears.
8. **A reusable guidance pattern already exists, buried at the end of Results → Adequacy.** Objective first, archetype pick, ordered stage pipeline with per-stage status, a completeness ledger (`ok | not_established | skipped`), a verdict chip with one verdict sentence, a fidelity badge on every number, caveats co-located with the number and ending in a next action, a pre-run cost disclosure with abort, and a report contract with required disclosures and evidence gaps. It is reliability-only, chat-only for the report, and labelled in engine vocabulary.
9. **The market has split in two**: expert engines adding AI copilots (PLEXOS Intelligence, Siemens agentic PSS E, ETAP Copilot, Aurora EOS AI) and "answer products" that fix the inputs and sell a report or a score (Aurora Chronos, Paces, Nira, envelio's connection check). A consultant tool can do both: guided study templates with a fixed report, and the existing workbench as the expert view underneath.
10. **Recommended shape:** a Study layer above the existing Project, entered from a decision question ("Do I need a BESS?"), built from a hub of sections with statuses, run at staged fidelity, answered with a verdict relative to a named baseline, explained with value streams and drivers, stress-tested with tornado and break-even readouts, and exported as a bound report with an Excel pro forma. The design spec pins the contracts and the slices.

## 2. What the tool is today

### 2.1 Product thesis as the documents express it

The owner is a power-systems consultant. The tool chains open engines (PyPSA for expansion and unit commitment, pandapower for load flow, lightsim2grid for N-1) upstream of commercial dynamics tools. Job one, from the FMEA and Energy Hub specs: state a reliability target, get the least-cost plan that meets it, and see which failure modes drive the residual risk, packaged per archetype (`strong_grid`, `weak_flexible`, `off_grid`) as a `ReferenceDesignReport`. Job two, from gridspine: hand the plan to PowerFactory as a `.raw` and `.dyr` bundle with a contingency list and an assumptions ledger. The selling point is trustworthy numbers: a figure that cannot be computed is shown as missing, never as zero, and every number says which engine produced it.

### 2.2 Capability inventory

| Area | Present | Evidence | Caveat |
|---|---|---|---|
| Least-cost capacity expansion, multi-period, vintages, perfect / myopic / limited foresight, representative weeks | Yes | BE `services/solver_service.py`, `services/solver/myopic.py`, `services/vintage_service.py` | Period weightings default to 1 year; cross-period discounting is opt-in (`auto_discount_periods=False`) |
| Unit commitment, AC power flow, N-1 screening, short circuit, SCR proxy | Yes | `gridspine/static/`, BE `services/ac_pf_service.py` | gridspine validated on IEEE 39-bus only; PowerFactory oracle gate never closed |
| Adequacy: ENS cap, COPT, sequential MC LOLE/EUE, ELCC, reserve margin, frontier, coupling and margin loops | Yes | BE `services/adequacy/*` | Screening grade; "not comparable to a statutory standard" by design |
| FMEA worksheet (IEC 60812), Class B/C stress, RAM rate library with provenance | Yes | BE `routers/adequacy_worksheet.py`, `services/adequacy/stress.py` | Real climate years deferred |
| Energy Hub archetype packs, redundancy options, levers, DtC islanding, multi-energy ENS, `ReferenceDesignReport` | Yes | BE `models/energy_hub.py`, `services/adequacy/eh_report.py` | Redundancy option costs are hard-coded placeholders (`cost_basis: synthetic_placeholder`) |
| Cost accounting reconciled to the LP objective: annuitised CAPEX + FOM, OPEX, CO2, curtailment, VoLL; per-asset revenue, net profit, LCOE, LCOS, LCOH; per-carrier and per-period roll-ups; PV of upfront CAPEX | Yes | BE `services/results/cost_breakdown.py`, `asset_economics.py`, `lcoh.py`, `services/economics.py`; nine surfaces under golden tests | Revenue is at LP duals; horizon totals undiscounted except upfront CAPEX PV; FOM was missing from every surface until 2026-09-27 |
| Scenario tree, batch solve queue, pairwise A/B compare (10 tabs) | Yes | FE `pages/ScenariosPanel.tsx`, `pages/CompareView.tsx` | Two projects only; no input diff; no N-way |
| Copilot with 113 tools incl. `explain_investment`, `compare_scenarios`, `build_study_report`, `start_campaign` | Yes | BE `services/chat_tools.py` | Needs an API key; `plan_what_if`, `generate_run_report`, `diagnose_results` were never implemented |
| Exports: CSV, SVG, PNG, XLSX (Asset Detail), NetCDF, bundle, MATPOWER | Yes | BE `routers/io.py`, `routers/asset_results.py` | No PDF, DOCX or PPTX; python-docx and pypdf are used only to read chat uploads |
| Guided steps (Model Horizon), new-study kind picker, EH panel chips | Partial | FE `pages/modelHorizon/StepShell.tsx`, `layout/NewProjectWizard.tsx` | No end-to-end guided study; no novice or expert mode |
| Project finance: NPV, IRR, payback, cash-flow table, WACC, debt, tax, depreciation, degradation, replacements, salvage, incentives | **No** | grep over BE, FE, gridspine, docs | Only a `period_basis: "npv_multi_period"` label in `models/adequacy.py` |
| Tariffs: demand charges, capacity and network charges, time-of-use bands, export compensation, PPA, rate library | **No** | grep | Workaround: a Generator or Link with a time-varying `marginal_cost`; peak charges only via admin-gated user code |
| Market services: FCR/aFRR, capacity market, revenue stacking; price or weather Monte Carlo; tornado or parametric cost sensitivity | **No** | grep; `services/adequacy/mc.py` samples outages only | Reserve margin and ELCC exist but are unpriced |
| Technology cost catalogue, industry templates | **No** | FE `layout/CreationForm.tsx` defaults cost to 0; templates are 3-bus, IEEE-14, IEEE-39, Belgium | `pypsa-gui/scripts/scaffold_dc_heatpump.py` is a REST script, not a template; PyPSA-Eur's `retrieve_cost_data` rule exists at repo root and is a ready cost source |
| Client document, executive summary, share link | **No** | roadmap item 3 "Study → one deliverable" is one line, never specified | `build_study_report` writes structured JSON and "no prose"; reliability only |

### 2.3 Where the tool already leads

- One engine chain from expansion to N-1 to adequacy to dynamics hand-off, with open and auditable assumptions.
- A real electrical network under the investment problem. Among edge tools only XENDEE, DER-CAM+ and ETAP approach this.
- Sector coupling (electricity, hydrogen, heat) in one optimisation, including LCOH per electrolyser.
- Adequacy certification and an FMEA worksheet. No edge investment tool offers either, and among grid tools only PowerFactory, SINCAL and ETAP price interruptions.
- The honesty doctrine: ADR-0001 null-not-zero, completeness enums, engine and fidelity badges, honesty-note tuples in payloads, restore guarantees after multi-solve studies.

## 3. The commercial landscape

### 3.1 Behind-the-meter, C&I and microgrid investment tools

| Product | What it sells | Investment outputs | Workflow and reporting | Price signal |
|---|---|---|---|---|
| HOMER Pro / Grid / Front (UL) | Least-cost microgrid design; C&I bill and peak reduction; utility-scale storage IRR | NPC, LCOE, IRR, payback, ROI relative to a base case; ITC and MACRS in Grid; degradation, augmentation and replacement in Front | Design → Calculate → Results; Setup Assistant wizard; search space plus sensitivity grid; "winning system" vs base; categorized best-per-architecture; optimal-system-type map; report builder to PDF/DOCX/HTML/RTF; branded proposal in Grid; tariff library covering most US/CA/MX/AU postcodes | USD 1,575 to 4,650 per year (Pro) |
| XENDEE (Eaton minority stake) | DER and microgrid design with power flow and N-1; sales proposals | NPV, ROI, payback, cash flow; energy-as-a-service; IRA incentives | DISCOVER screen → DESIGN → PROPOSE two-tier; Load Builder; catalogues; data-centre bridging-power positioning | Annual contracts, price hidden |
| NREL REopt (free, open) | Savings- or resilience-sized DER | NPV of life-cycle savings, Excel pro forma, outage survival probability curve, off-grid mode, hydrogen, health and climate emission costs | Goal first (Financial vs Resilience); only site, tariff and load mandatory; BAU-vs-optimal table with NPV at the bottom; PDF plus Excel; portfolio screening | Free |
| LBNL DER-CAM / DER-CAM+ | Annual-cost MILP with heat and ancillary streams | Annual cost, stacked value streams | Draw the single-line diagram and heat network in a browser | Free after registration |
| Energy Toolbase | Solar-plus-storage proposals for US developers | Bill savings by value stream; cash, PPA, loan, lease | Tariff database (70k to 120k verified rates); dispatch mirrors its own field controller; proposal template gallery; web proposals | About USD 299 to 333 per user per month |
| NREL SAM (free) | Performance and finance simulation | Widest financial models (host/developer, partnership flip, merchant); P50/P90; "Send to Excel with Equations" | Parametric and stochastic sweeps; PySAM | Free |
| RETScreen Expert | Feasibility for clean-energy projects | NPV, IRR, simple and equity payback, debt, tax; Monte Carlo and impact (tornado) graph; cumulative cash-flow chart | Benchmark → Feasibility (5 fixed steps) → Performance → Portfolio; free read-only Viewer | CAD 869 per year |
| Gridcog (AU/UK) | Multi-site, multi-asset simulation and optimisation for large energy users incl. data centres | Cash flows and IRR per scenario; cash flows assigned to each project participant; network charges; wholesale exposure | Perfect-foresight or uncertain dispatch; downloadable investment-case report; bundled market data | Tiered, hidden |
| energyPRO, nPro, Polysun | Multi-energy and district-heating planning incl. power-to-X and data-centre heat reuse | Operational and financial reports | Templates for data-centre waste-heat reuse (Polysun, German EnEfG) | Hidden |
| Schneider EcoStruxure Microgrid Assessment; Microgrid Flex | Feasibility, sizing, economics; pre-engineered microgrid blocks | KPIs for economics, resilience and sustainability; multi-objective configuration selection | Setup → components → tariffs and incentives → simulate → results | Hidden |
| Market-side valuation: Aurora Chronos, Modo, Pexapark, Ascend BatterySIMM | "Bankable" storage and PPA valuations | Revenue stacks, gross-margin forecasts, high/central/low curves, downside cases | Site specs in, consultancy-standard report out "within two hours" (Chronos) | Subscription |

Hydrogen-specific tools (HyDesign, NREL H2A/H2FAST, H2Integrate, Honeywell Concept Design Optimizer) add a discounted-cash-flow model that returns the levelised hydrogen price at a target IRR, break-even H2 price, and PPA notebooks. Data-centre practice adds Bloom's value calculator (overbuild and time-to-power effects), Vertiv's reference-design selector, and Google's 24/7 hourly carbon-free-energy matching.

### 3.2 Utility-scale and grid-planning suites

| Product | Investment decision outputs | Study packaging and reporting | Note |
|---|---|---|---|
| Energy Exemplar PLEXOS (+ Aurora, Cloud, Intelligence, Pulse) | LT capacity expansion; 2026: LOLP-target expansion, ELCC/EFC accreditation inside the expansion step, partial builds; stochastic over load, inflow, fuel; Monte Carlo over renewables; settlement and counterparty-exposure dashboards | Simulation-ready datasets; scenarios and models as first-class objects; cloud batch; PLEXOS Intelligence agents (support, digital analyst writing board summaries and scenario comparisons, automation writing Python); PLEXOS Pulse no-code chat; certification programme | No AC N-1 |
| Aurora Energy Research (Origin, Chronos, Amun, Lumus, EOS AI) | Price, dispatch, investment forecasts to 2070; bankable battery and wind valuations; PPA pricing | Software plus proprietary forecasts plus advisory; fixed consultancy-standard report; source-linked AI answers | Market view only, no physics |
| Hitachi Energy EPM (PROMOD, Capacity Expansion, Velocity Suite, Asset Modeling) | 20 to 30-year resource plans; nodal market simulation; VaR, CFaR, EaR in the ETRM line; data-centre siting and interconnection exposure | Cloud PROMOD running thousands of cases in parallel; quarterly "bankable" reference cases | No public site-level investment tool found: the missing middle this tool could fill |
| Siemens PSS E / SINCAL / ODMS / Gridscale X | SINCAL Economic Efficiency Calculation: CAPEX/OPEX over planning periods, NPV of variants; Network Development; probabilistic reliability | May 2026: agentic PSS E with 2,000+ Python APIs and a cloud UX for data-centre and large-load connection studies; ODMS model version control | Search extract quotes about EUR 11.7k per month for v36; verify |
| DIgSILENT PowerFactory | Techno-Economic Calculation: NPV of expansion strategies, cost of losses and interruptions, optimal year of investment; Generation Adequacy; Reliability | Variations and expansion stages as scenario system; reporting via templates, DPL or Python | No market expansion |
| ETAP (Schneider) | Reliability with EENS and ECOST from an interruption-cost library; microgrid controller twin sizes BESS | ETAP 2026 private AI Copilot and auto-complete; physics-based digital twin; data-centre and AI-factory twin with NVIDIA | Weak on capacity expansion and LOLE |
| encoord SAInt | Expansion of generation, storage and transmission with gas coupling and AC physics | One data structure for electricity, gas, heat | Closest breadth analogue; cost-minimisation only |
| Artelys Crystal Super Grid | Web-based CBA of infrastructure and generation investment; Monte Carlo adequacy across climate years; ERAA methodology | Cloud and sovereign HPC; 2026: infeasibility logs, asset library, stochastic runs | No AC N-1 |
| Antares (RTE, open) + Xpansion + Web | Sequential Monte Carlo adequacy; Benders investment | Antares Web: accounts, permissions, event-store variant manager with explicit diffs | Best open example of study version control |
| EPRI DER-VET (open, read in source) | About 15 value streams (energy shift, regulation, reserves, load following, DR, resource adequacy, T&D deferral, volt-var, backup, demand charge); reliability sizing for a target outage duration; pro forma with replacements, end-of-life, construction-year CAPEX, federal/state/property tax, MACRS; NPV, payback, discounted payback, MIRR, LCOH | Electron/Vue GUI over a CSV engine | Reference open implementation of value stacking plus pro forma |
| Interconnection SaaS (Nira, Paces, GridUnity, envelio, Kevala, Camus FlexConnect, Feasibly) | Injection studies at every substation with upgrade-cost estimates; data-centre site score 1 to 5; MW heatmaps; flexible (non-firm) connection sizing | Map-based self-service; envelio's public Online Connection Check cut a 3-hour screening to 15 minutes; Paces Self-Service vs Managed tiers | Suggests a fourth archetype: flexible connection |

### 3.3 What the market pattern says

Engines for experts (PLEXOS, PSS E, PowerFactory, ETAP) are adding AI copilots on top; answer products (Chronos, Paces, Nira, envelio's check, REopt) standardise inputs, hide the model and sell a report or a score. Every commercial leader bundles data with the engine: tariff libraries (Energy Toolbase, HOMER Grid, REopt via OpenEI), cost and equipment catalogues (XENDEE, RETScreen, ETAP), market datasets and reference cases (PLEXOS, Hitachi, Aurora). "Bankable" and "lender-ready" are the words the buyers respond to.

## 4. Gap analysis: investment decision-making

Ranked by how often the capability recurs across vendors, how central it is to the sales pitch, and how far the tool is from having it. Effort is a first estimate against the existing code: S is days, M is weeks, L is a programme.

| # | Gap | Status here | Closest hook in the code | Who has it | Effort |
|---|---|---|---|---|---|
| G1 | **Project-finance pro forma**: year-by-year cash flows, NPV, IRR, payback, discounted payback, LCOE/LCOS/LCOH on a discounted basis, WACC, debt and DSCR, tax and depreciation, escalation, replacements, salvage, incentives; Excel export | Absent | `services/solver/periodized_costs.py` (annuity, `overnight_cost`, `lifetime`, `build_year`, `overnight_cost_pv`), `cost_breakdown.by_period`, `asset_economics.by_period`, `period_utils.active_period_years` | REopt, SAM, RETScreen, HOMER, Gridcog, DER-VET, Chronos, SINCAL, PowerFactory TechEco | M |
| G2 | **Tariff and bill engine**: demand charges with ratchets, time-of-use energy, fixed and network charges (EU capacity-based grid fees), export compensation, standby charges, connection limits; a rate library the company can load | Absent | Time-varying `marginal_cost` on an import Generator or Link; `capex_budget_per_period` shows how an LP constraint is injected; `extra_functionality_code` is admin-gated | Energy Toolbase, HOMER Grid, REopt, XENDEE, Gridcog | M (engine) + L (library) |
| G3 | **Baseline-versus-proposed framing**: a named counterfactual, bill before and after, savings waterfall by value stream, categorized best-per-option comparison | Partial: pairwise Compare, scenario tree, EH redundancy enum | `routers/compare.py results-summary`, `services/solve_queue.py` batch, EH `redundancy.py` outer loop, `explain_investment` binding-constraint reasons | REopt, HOMER, Energy Toolbase, XENDEE | M |
| G4 | **Sensitivity and uncertainty on economics**: tornado over ledger ranges, break-even thresholds, optimal-option map over two variables, P50/P90 and probability of positive NPV from price and weather scenario sets | Partial: outage Monte Carlo, ε-constraint frontier (12 points), contingency sweep, Class-C stress | `services/adequacy/frontier_loop_runner.py` (bisection over re-solves), `campaign.py` (solve budget), `stress.py` (parametric multipliers) | HOMER, RETScreen, SAM, PLEXOS, Aurora, Ascend, Artelys | M |
| G5 | **Revenue stacking and market services**: FCR/aFRR/mFRR reservation, capacity-market payment, arbitrage at exogenous prices, PPA and tolling settlement, curtailment loss in currency | Absent (arbitrage at LP duals only) | `elcc.py` (firm MW to price against a capacity payment), `reserve_margin` constraint, storage `spread_eur_per_mwh` | DER-VET, HOMER Front, Chronos, Gridcog, Enel X, PLEXOS 2026 | M to L |
| G6 | **Resilience priced in money**: VoLL and customer-damage-function library by sector, outage survival probability curve, savings-sized vs resilience-sized cost gap | Partial: ENS, shed hours, LOLE, DtC islanding, single VoLL | `services/adequacy/mc.py` (sequential MC already samples every hour), `dtc.py`, `lost_load.py` | REopt, ETAP ECOST, PowerFactory, SINCAL | S to M |
| G7 | **Lifecycle realism**: battery and PV degradation, augmentation, stack replacement, load growth, price escalation paths | Absent (vintages and lifetimes only) | `vintage_service.py`, `load_scalers`, `co2_price_per_period` | HOMER Multi-Year and Front, SAM, Chronos, iHOGA | M |
| G8 | **Assumptions library with provenance**: versioned technology costs with source and year, VoLL tables, failure-rate libraries, price scenario sets, plausible ranges | Absent for costs (defaults are zero); present for outage rates | PyPSA-Eur `rules/retrieve.smk::retrieve_cost_data` (technology-data `costs_{year}.csv` at repo root), `data/custom_costs.csv`, EH RAM rate library with `rate_source` chips, gridspine ledger (measured/datasheet/assumed) | Every commercial leader | M |
| G9 | **Industry study templates**: data centre (IT load, PUE, UPS, gensets, bridging, waste heat), large C&I site, hydrogen plant, co-located BESS and renewables, off-grid microgrid | Absent (four transmission test grids; templates 404 in dev runs) | `project_templates/_build.py`, `scripts/scaffold_dc_heatpump.py`, EH archetype packs as the pattern | HOMER Grid data-centre page, XENDEE, Schneider Flex, Polysun heat-reuse templates | M |
| G10 | **Report generator**: executive summary, recommendation, assumptions, results, sensitivities, caveats, appendix; DOCX/PDF and an Excel pro forma; numbers bound to the model; stale flag | Absent (roadmap item 3 never specified) | `study_report.py` (`required_disclosures`, `not_established`, `evidence_gaps`), `eh_report.py` assembler, chart SVG/PNG export, Asset Detail XLSX writer (openpyxl) | HOMER, Energy Toolbase, Aurora, REopt, Chronos, RETScreen | M |
| G11 | **Guided study workflow for non-experts**: question-first entry, minimal mandatory inputs with documented defaults, hub with section statuses, plain labels with the technical term as subtitle, verdict card, maturity badge | Partial (Model Horizon steps, kind picker, EH chips) | `pages/modelHorizon/StepShell.tsx`, `HorizonSummary.tsx`, `modelHorizonModel.ts`, `GridspinePanel` stage list, `adequacy.tsx` chips and shared caveat strings | REopt, HOMER Setup Assistant, RETScreen, Aurora Sales Mode, XENDEE PROPOSE | L (spread over slices) |
| G12 | **Grounded copilot narration**: what-if macro, run report, results diagnosis, all citing model-computed facts | Partial (113 tools; the composite tools were never built) | `chat_tools.py` `explain_investment`, `compare_scenarios`, `build_study_report`, `start_campaign`; `_DOMAIN_GUIDE` | PLEXOS Intelligence, Aurora EOS AI, ETAP Copilot, Siemens agentic PSS E | M |
| G13 | **Scenario management with input diffs and lineage**, pinned baseline, locked issued versions | Partial (parent link, objective delta) | `ScenariosPanel.tsx`, `snapshots.py`, change log | Antares Web, PowerFactory variations, PSS ODMS | S to M |
| G14 | **Grid-connection and large-load screening** as a packaged study; flexible (non-firm) connection as an archetype | Planned (gridspine variant 2 greyed "later") | `gridspine/` pipeline, SCR proxy, EH import overlays | Siemens PSS E 2026, Nira, Paces, envelio, Camus | L |
| G15 | **Hourly emissions and 24/7 carbon-free-energy matching**, health cost of emissions | Partial (CO2 per carrier and period, CO2 price) | `services/results/emissions.py` | REopt, hyperscaler practice | S |
| G16 | **Investment timing and staging**: build now versus defer, optimal year, mutually exclusive projects | Partial (multi-period with vintages) | `vintage_service.py`, myopic driver | PowerFactory TechEco, PSR OptGen | M |
| G17 | **Controller-consistent dispatch** (forecast-driven or rolling horizon) so savings are not overstated by perfect foresight | Partial (`rolling` strategy exists; not framed as an honesty control) | `solve_strategy=rolling`, `run_uc_rolling` in gridspine | Energy Toolbase, ETAP, XENDEE OPERATE, Gridcog | S (framing) |

Two gaps are prerequisites for the rest and are cheap: the **zero-profit-by-construction caveat** exists only as a chat reading note (`chat_tools.py`, around line 4250) and must appear in the Economics tab and Asset Detail; and the **capital-cost unit badge** in the Properties panel says `€/MW` for a field whose tooltip says `€/MW/yr`, so a novice types an overnight figure into an annuity field.

## 5. Gap analysis by client archetype

Each row states the decision question a client asks, what the tool can model today, and what is missing to answer "invest, how much, what is the return".

| Archetype | Client question | Can model today | Missing |
|---|---|---|---|
| **Data centre** (incl. waste-heat recovery) | Do we build on-site power for bridging and backup, how much BESS, do we sell waste heat, what is the value and the risk? | Must-run load as a fixed Link, waste heat to a low-temperature bus, heat-pump Link with COP, heat dump (REST script only); archetype packs, DtC critical-load islanding, N+1 redundancy options, MC LOLE certification, ELCC, SCR gate | No data-centre template or pack in the UI; no IT-load, PUE, UPS or genset autonomy model; no grid-connection timeline (bridging months); no heat-sale contract price or EnEfG reuse-share reporting; no 24/7 CFE percentage; redundancy costs are placeholders; no tariff bill; no NPV/IRR; backup value only via a single VoLL |
| **Large C&I site** | Should we add PV and BESS behind the meter, what do we save on the bill, when does it pay back? | Grid import with a time-of-use `marginal_cost` series; PV, BESS, CHP sized by the LP; CO2 price; per-asset net profit at duals | Tariff engine (demand charges, network and capacity fees, standing charges, export compensation); baseline-vs-project bill savings; rate library; incentives and tax; cash flow, payback, IRR |
| **Hydrogen** (electrolyser + storage) | What electrolyser and storage size, what LCOH, at what offtake price does it pay? | Electrolyser and fuel-cell Links, H2 bus, Store and Load; per-asset and fleet LCOH in EUR/MWh and EUR/kg; multi-energy unmet demand | Stack degradation and replacement schedule; H2 offtake contract price and sales revenue; subsidy or contract-for-difference; part-load efficiency; break-even H2 price at a target IRR (the H2A/H2FAST output) |
| **BESS + renewables** (co-located) | What battery size and duration, what revenue stack, what is the merchant NPV under price uncertainty? | PV or wind plus StorageUnit or Store; shared point of connection via a bus and import-limit Link; arbitrage spread, LCOS, cycle counts, capture price, curtailment MWh, ELCC firm MW | Ancillary-service markets, capacity payments, revenue stacking with state-of-charge reservation; degradation and augmentation; exogenous price forecasts and P50/P90; curtailment loss in currency; PPA or tolling; grid-connection charges; merchant NPV/IRR |
| **Microgrid / off-grid** | Diesel-only versus hybrid, how many hours of autonomy, what does reliability cost? | `off_grid` pack, unit commitment for diesel, VoLL and ENS cap, cost-versus-reliability frontier, storage-duration options (4/24/72 h), MC certification | Fuel price escalation and logistics; generator replacement; battery degradation; multi-year cash flow; diesel-only baseline as a first-class savings view; discrete generator sizes |

## 6. Usability findings

### 6.1 The path a novice faces today

- **Entry.** The home page offers blank, template, import and duplicate. The workbench offers about 80 panels, tabs and modals with equal weight: 14 slide panels, 13 result tabs (Adequacy alone stacks 8 panels), 10 compare tabs, 11 bottom grids of raw PyPSA columns, 5 solver-settings tabs, 6 horizon steps. Nothing is ordered or gated by the user's question. The only in-repo "typical workflow" is a README section.
- **Templates.** All four are transmission test grids. Their network files are gitignored and built only by the macOS packaging script, so in a `start.sh` run every template card returns a developer 404 telling the user to run a Python build script.
- **Vocabulary.** The tooltip file states its audience: "a power-systems engineer using the GUI, so we use PyPSA's terminology rather than re-explaining basics". Labels include `p_nom`, `p_max_pu (static)`, `η store`, "Run LOPF", "‱ of demand", "μ upper", "SCLOPF", "vintage". There is no glossary, tour, checklist or novice mode.
- **Costs.** Four cost fields sit side by side with implicit precedence (capital, overnight, FOM, per-asset discount rate); the global discount rate and lifetime live in Solver Settings → Dispatch. A battery cannot be made extendable or priced at creation, and its energy cost is not separable from power cost.
- **Horizon.** A new project has one "now" snapshot and nothing warns that storage cannot shift energy in a one-snapshot model.
- **After a solve.** Results does not open, no success toast appears, the default tab is Dispatch, and there is no headline. Economics shows net profit at shadow prices, so an optimally sized asset reads as worthless. The only narrative conclusion is the chat copilot, which needs an API key and keeps conclusions in scrollback.
- **Scenarios and sensitivity.** Scenarios are full project copies edited by hand; there is no parameter sweep outside the reliability studies.
- **Copy defects found while reading:** a literal `max_solves` placeholder renders instead of a number in the margin-loop panel; Solver Settings points to a toggle "in General" that does not exist and sends lost-load results to the "LoadFlow" tab; a validation message cites a stale "Snapshots → Multi-period" path; the generator creation form uses `$` where the app uses EUR; Dispatch KPIs render "—" for near-zero values, contrary to ADR-0001. Four page modules (`ResultsViewer`, `LoadProfileManager`, `GenerationStack`, `LoadEditor`) are imported nowhere.

### 6.2 The guidance pattern worth generalising

The adequacy, FMEA, Energy Hub and gridspine surfaces already implement, in reliability vocabulary, most of what a guided study needs:

1. Objective first (`start_campaign.objective`, "state the objective in the user's terms").
2. Archetype pick with a one-line blurb.
3. Ordered stage pipeline with per-stage status (pending, running, done, failed, aborted, skipped).
4. Completeness ledger per report section: `ok | not_established | skipped`; "not run" is never shown as zero.
5. Verdict chip plus one backend-authored verdict sentence.
6. Fidelity and provenance badge on every number; expert rows "never impersonate an engine".
7. Caveats co-located with the number, one shared string for input and read-back, each ending in a next action (`RESERVE_MARGIN_CAVEAT`, `ensTargetWarning`).
8. Pre-run cost disclosure ("a run costs one probing solve plus up to N expansions"), abort at stage boundaries, partial-result honesty.
9. Restore guarantees ("restored" versus "NOT restored: the network you are holding is the last iterate").
10. Report assembly from `required_disclosures`, `not_established` and `evidence_gaps`, placed before the numbers.

Its weaknesses as a visual model: it is dense (eight stacked panels in 10 to 11 px text), labelled in engine terms ("fmea_top: skipped", "m*", "COPT", "DtC", "SCR"), hidden at the bottom of a results sub-tab, reachable only after a solve, reliability-only, and the report layer has no UI.

### 6.3 What the best guided tools do, and the patterns to adopt

From the UX research (24 patterns catalogued in the research file):

- **Ask the goal first, then only what the user alone knows.** REopt makes site, tariff and load mandatory and documents every default. Enphase asks "how many hours of backup" rather than kWh.
- **Frame every result against doing nothing.** REopt's business-as-usual versus optimal table with NPV at the bottom; HOMER's winning system versus base case; HOMER's categorized view showing the best system per architecture.
- **Show robustness as thresholds and frequencies, not error bars.** HOMER's optimal-system-type map and break-even distance; RETScreen's impact graph and Monte Carlo; quantile dotplots ("in 17 of 20 futures the battery pays off") beat mean and standard deviation for lay audiences (CHI 2018).
- **Two layers of disclosure plus an explicit expert exit.** Aurora's guided Sales Mode beside a full Design Mode; PVGIS's lumped 14 percent loss beside PVsyst's loss diagram. NN/g: wizards suit occasional tasks, and the advanced layer must stay visibly reachable.
- **Assumptions log with provenance** (HM Treasury Aqua Book, HOMER's Input Summary Report, EnergySage's published assumptions) and a **maturity badge** tied to estimate classes (AACE 18R-97: screening about −30 to +50 percent).
- **Bound reports, no hand-typed numbers.** Aurora's placeholders, SAM's Excel with live equations, Quarto's parameterised reports to DOCX, PPTX and PDF. Copilots that earn trust ground every figure in the model (ThoughtSpot's read-back tokens, Tableau Pulse's deterministic driver detection with LLM phrasing only). Structured and visual LLM output increases over-reliance, so the LLM must never compute or draw.

## 7. What to build, in one paragraph

A **Study layer** above the existing Project. A study starts from a decision question, collects the few inputs only the user knows, fills the rest from a versioned assumptions library, builds the PyPSA network from a template pack, runs a baseline and a small option set at staged fidelity, computes a bill and a pro forma from the solved networks, answers with a verdict relative to the baseline, explains the value streams and drivers, stress-tests with tornado and break-even readouts, and exports a bound report plus an Excel pro forma. The existing workbench is the expert view of the same network. The design spec pins the contracts, the question templates, the reuse table and three MVP slices: slice 1 is the finance layer plus a BESS question end to end with a DOCX report; slice 2 adds the tariff engine, the assumptions ledger, sensitivity and the data-centre and hydrogen templates; slice 3 adds revenue stacking, monetised resilience, price and weather uncertainty and the shareable read-only study.

## 8. Prerequisites and risks

| Item | Why it matters for client-facing investment studies | Source |
|---|---|---|
| Uploaded time series live in one process-wide store shared across tenants and projects | Cross-tenant read and write; affects the desktop build as a multi-project data-integrity bug; needs its own plan (about 230 references in 13 modules) | `OPEN-ITEMS.md` item 1, `findings/2026-09-12-user-ts-is-a-process-global-shared-across-tenants.md` |
| Cost numbers exported before 2026-09-27 are suspect | FOM was missing from fixed cost on every surface; a sub-annual model over-charged it 365×; multi-period CAPEX parsing defect | `findings/2026-09-27-fom-missing-from-fixed-cost.md` |
| Myopic runs | Summing per-period objectives is wrong by −42.9 or +22.2 percent depending on config; capacity freezes silently at the first period (now a warning); fails on pandas 3 | `pypsa-gui/docs/pitfalls-myopic-and-cost-reporting.md` |
| Cross-period discounting is opt-in and off by default | A multi-period plan is undiscounted unless the user enables it; the finance layer must not inherit this silently | `services/solver/assumptions.py` |
| Adequacy figures are screening grade | The report must say so at the point of display, not in a footnote | FMEA spec §10 |
| Templates 404 outside the packaged app | Any guided study that starts from a template is broken in dev and CI runs | `routers/projects.py` template loader, `build-macos.sh` |
| Copilot needs an API key and super-admin profile setup | Guided narration cannot depend on the copilot being present; the deterministic facts layer must stand alone | `CHATBOT.md` |
| `OPEN-ITEMS.md` is stale since 2026-09-12 | Items 2 to 5 look fixed by PR #18 in code; 6, 8, 9, 10 not re-verified | this assessment's roadmap thread |
| Market research is extract-based | Vendor pages were not read in full; verify before quoting externally; consider widening the environment's network access for a follow-up pass | §0 |

## 9. Open questions for the owner

1. **Whose money?** The pro forma needs a perspective: site owner, developer, investor, or a multi-party split (Gridcog's model). The default determines what "value" means on the verdict card.
2. **What is "doing nothing" per question?** For waste heat, is the baseline a gas boiler or an existing heat pump on the district network? For a data centre, is it grid-only with diesel backup? The baseline drives every headline number.
3. **Which markets and tariffs first?** The tariff engine is generic, but the library is regional. German capacity-based grid fees and EU balancing markets, or US demand charges and the OpenEI database?
4. **Real versus nominal, pre- versus post-tax, with or without subsidy.** IRENA and SAM conventions differ; the tool must label one default and expose the toggles.
5. **How far does the Hitachi Energy equipment catalogue become the assumptions library?** The research suggests the company's own price book is the natural default library, which is a data-governance decision, not an engineering one.
6. **Should the copilot draft prose in client reports at all?** The design proposes yes, with an AI label, fact-ID citations and a reviewed tick before export. That is a product-policy call.
