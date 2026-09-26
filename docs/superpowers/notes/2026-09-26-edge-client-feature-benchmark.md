# Edge-client feature benchmark — what the tool has, what investors need, what to build next

**Written:** 2026-09-26 · **Status:** research note for a product decision, not a plan.
**Question asked:** if hyperscalers, data-centre developers, energy-hub participants or IPP
developers were to *use* this tool to support an investment decision, what is already there,
what is missing from an integrated tool chain, how would financial modelling fit coherently,
and in what order should the gaps be closed?

Everything in §1–§2 was verified against the code on this branch (`ec23302`). §3 is external
market research (sources at the end). §4–§7 are the analysis and the recommendation.

---

## 0. The one-paragraph answer

The tool is a mature **system-cost, reliability-first design engine**: it finds the least-cost
energy-hub design that meets an unserved-energy / LOLE target and explains which failure modes
drive the residual risk, with an unusually strong provenance discipline (assumption hashes,
completeness flags, "null not zero"). What an investor's decision needs, and what the tool has
essentially none of, is the **asset-owner's cash-flow view**: who pays what to whom under which
tariff or contract, financed how, taxed where, returning what IRR at what DSCR, and how that
changes across price and cost scenarios. The two views are not in conflict. PyPSA already
produces every physical quantity a cash-flow model consumes (hourly dispatch, imports, exports,
capacities, build years), so the coherent plan is a **commercial layer** (tariffs, contracts,
exogenous prices) feeding the existing optimiser, and a **finance layer** (annual cash flows,
debt, tax, incentives, returns) post-processing its results — plus a first-class **data-centre
load archetype** so the client's own energy behaviour is an input rather than a hand-built
network. That is the P0. Everything else in §7 is sequenced behind it.

---

## 1. What exists today — inventory by capability

Scale, for orientation: ~99k lines of backend Python with ~100k lines of tests, ~108k lines of
TypeScript, 24 routers, ~150 chat tools. This is a product, not a prototype.

### 1.1 Modelling core (pypsa-gui workbench)

| Capability | Status | Where |
|---|---|---|
| Visual network build (schematic + geographic), full PyPSA component CRUD incl. multi-port links (electrolyser, heat pump, CHP, data-centre waste heat) | Done | `pypsa-gui/frontend/src/pages`, `backend/routers/network*.py` |
| Spreadsheet editing, bulk edit, CSV/xlsx export | Done | `routers/network_bulk`… |
| Snapshots, weightings, representative weeks, per-asset time-series upload | Done | `routers/snapshots.py`, `network_profiles.py` |
| Multi-year investment periods, per-vintage bounds, overnight / myopic / perfect foresight | Done | `routers/vintage.py`, `services/solver/*` |
| LOPF + optional AC power flow, pre-flight validation, SSE solver log, solve queue | Done | `services/solver_service.py`, `solve_queue.py` |
| Results: capacity, dispatch, economics (LCOE/LCOS/LCOH, revenue, "net profit"), emissions, nodal prices, curtailment, lost load, storage cycling, load flow | Done | `services/results/*`, `asset_results/*` |
| Two-project Compare with deltas | Done (strictly A/B) | `services/compare/*` |
| Projects, scenario trees (baseline / scenario / stress), bundles, templates, undo, audit log | Done | `routers/projects.py`, `db/models.py` |
| Multi-tenant auth: orgs, users, roles, project ACL, edit locks, desktop local mode | Done v1 (process-global live network is a known limitation) | `routers/auth.py`, `admin.py`, `services/project_acl.py` |
| "Trustworthy numbers" doctrine: unresolvable figures ship as `null` + flag, cross-surface reconciliation tests | Done | ADR-0001, `specs/2026-08-01-trustworthy-numbers-design.md` |

### 1.2 Reliability / adequacy stack (solution FMEA)

Roughly 30 phase plans; all landed and QA-gated.

| Capability | Status | Where |
|---|---|---|
| ENS-capped least-cost expansion (ε-constraint), VOLL weighted form, per-Load VOLL slacks, DSR as a resource distinct from shedding | Done | `services/adequacy/slack.py`, `solver/assumptions.py` |
| COPT (multi-state VRE, thermal outages), sequential Monte Carlo LOLE/EUE (≤2000 draws), ELCC by bisection, reserve margin | Done | `adequacy/copt.py`, `mc.py`, `elcc.py` |
| Class-A/B/C stress registry (parametric load/availability, link residuals, profile packs); real climate years deferred | Done (C = synthetic) | `adequacy/stress.py`, `sweep.py` |
| Cost-vs-availability frontier (≤12 points), margin & coupling loops, campaign solve budget | Done | `frontier.py`, `margin_loop_runner.py`, `campaign.py` |
| IEC 60812-style FMECA worksheet ranked by €/yr criticality, asset-health provenance (failure-rate source) | Done | `worksheet.py`, `asset_health.py` |

### 1.3 Energy Hub (EH) reference design — the closest thing to a product for the target clients

| Capability | Status | Where |
|---|---|---|
| Three archetype packs: `strong_grid`, `weak_flexible`, `off_grid` (network overlay + solver patch, PoC import-link selection via `eh_role` / `eh_poc`) | Done | `adequacy/archetypes.py`, `models/energy_hub.py` |
| Orchestrated study `run_eh_study` (HTTP + chat), abort, 30-solve default budget | Done | `eh_study.py`, `eh_study_runner.py` |
| Redundancy scenarios (N-1 generation / conversion, parallel storage), discrete train-count loop | Done | `redundancy.py` |
| Levers: import cap 0/25/50 MW, storage duration 4/24/72 h | Done | `levers.py` |
| Disconnect-to-Critical (DtC) stress and planning on islanded topology | Done (planning off by default) | `dtc.py` |
| `ReferenceDesignReport`: target vs achieved ENS, cost at target, sizing, TEA (LCOE only), SCR gate + EMT-recommended flag, multi-energy ENS by carrier, completeness map, assumption hashes | Done | `eh_report.py`, `scr_gate.py`, `multi_energy.py` |
| Frontier, MC certification, FMEA top-N **inside the EH driver** | **Always `skipped`** — "not implemented in P1.5 sync driver"; `mc_lole_h` never filled, so weak/off-grid designs report LOLE `not_established` | `eh_study.py` L110–129 |
| LCOH in TEA block | Field exists, never computed | `eh_report.py` |
| Data-centre concept, tariffs, PPAs, site commercial inputs | **Absent** | — |

### 1.4 gridspine (planning → dynamics handoff)

PyPSA nodal UC → pandapower AC LF → lightsim2grid N-1/N-2 → IEC 60909 short circuit → snapshot
ranking (min inertia, max IBR share, max load, max import, N-1 severity) → PSS/E `.raw`/`.dyr`
bundle for PowerFactory → read-back gate. Increments 1–6 landed; 13 endpoints, study view, 12 chat
tools. The **connection-study variant** (host grid + connection request → static screening →
grid-strength → RfG/VDE compliance) is specified but **not built**. The independent PowerFactory
comparison has never been run. Relevant to hyperscalers because grid-code compliance and
grid-strength (SCR) at the point of connection are now routinely demanded of >100 MW loads.

### 1.5 Copilot (chat)

~150 tools spanning every backend operation, five confirmation tiers, provider seam (Anthropic
default; OpenAI-compatible, Ollama etc.), uploads (Excel demand → network, image → network),
`explain_investment`, `build_study_report`, EH and gridspine tools. Well suited to the "guided
what-if" that a non-modeller client user needs.

### 1.6 Financial modelling — what actually exists

Verified by grep across the whole repository:

| Concept | Present? | Notes |
|---|---|---|
| Annuity / CRF, overnight → annualised CAPEX, discount rate + inflation (Fisher), lifetime, build year | Yes | `services/solver/periodized_costs.py`, `assumptions.py` L641–698 |
| Per-asset revenue at (merit-order-corrected) nodal duals, VOM, fixed cost, "net profit", LCOE/LCOS/LCOH, capture price/rate | Yes | `services/results/asset_economics.py`, `lcoh.py` |
| System cost by class/carrier/period, objective reconciliation, per-period CAPEX budget constraint | Yes | `cost_breakdown.py`, `objective_decomposition.py`, `solver/objective.py` |
| CO₂ price (static / per period), VOLL, DSR price, curtailment cost | Yes | `SolverConfigSchema` |
| **NPV / IRR / payback per asset or project** | **No** (the adequacy `npv_multi_period` is PV-weighted *system* cost) | |
| **Debt, DSCR, sculpting, equity vs project returns, WACC as a structured input** | **No** (WACC appears in one comment) | |
| **Corporate tax, depreciation schedules, tax credits (ITC/PTC), grants, CfD, capacity payments** | **No** | |
| **Tariffs: TOU energy, capacity/demand charges, network fees, grid-connection fees** | **No** | grid import is priced by the LP dual of the client's own small network |
| **PPAs / offtake contracts, revenue stacking (FCR/aFRR, DR programmes)** | **No** (`e_sum_min/max` is an energy bound only) | |
| **Exogenous market-price series (price-taker mode)** | **No** first-class support | possible by hand via a marginal-cost generator |
| **Cash-flow timeline, construction phasing, degradation, O&M escalation, terminal value** | **No** | |
| **Financial sensitivity / Monte Carlo (prices, capex, CF), P50/P90** | **No** — all MC is outage-only | |
| Fuel price vs heat rate as separate inputs | No — folded into `marginal_cost` | |

One consistency defect worth a ticket: `frontend/src/utils/propertyDocs.ts` (L60, L90) tells the
user `capital_cost = overnight × annuity + fom_cost`, while the backend treats FOM as informational.

---

## 2. Who the clients are and what decision they are making

The brief names hyperscalers, data-centre developers, energy hubs and IPP developers. Their
questions overlap heavily but are not identical.

| Persona | Decision | The number they defend to a committee |
|---|---|---|
| **Hyperscaler / DC developer** (site energy team) | Which power architecture gets this campus energised soonest at acceptable cost and reliability: grid-only, grid + BESS, bridge gas/gensets until grid arrives, co-located generation, full BTM? | Speed-to-power (months), all-in €/MWh, availability (nines), hourly CFE %, exposure to curtailment obligations |
| **Colocation / edge DC operator** | Same, at 5–50 MW, often inside a congested distribution grid; is joining an *energy hub* (shared capacity contract) worth it? | NPV of hub participation vs waiting, demand-charge savings, resilience value |
| **Energy-hub participant / developer** (industrial park, port, NL/DE/BE) | How much aggregate grid capacity does the hub need, who gets it when, what storage/flex makes the group contract feasible? | Aggregate cap vs individual sum, congestion-service revenue, member cost allocation |
| **IPP / developer** (solar, wind, BESS, hybrid, gas peaker) | Build / hold / sell; PPA price to hit target equity IRR; merchant vs contracted exposure; financing terms | Project IRR, equity IRR, DSCR min, P50/P90 revenue, LCOE vs capture price |
| **Consultant** (today's actual user) | Deliver a defensible reference design and a bankable report to any of the above | Provenance, reproducibility, gates, assumptions ledger |

Common thread: **every one of them ends with a cash-flow model** in front of a credit committee
or an investment board. Today the tool stops one step before that.

---

## 3. What the market offers (benchmark)

| Category | Representative tools | What they do well | Where this tool already differs |
|---|---|---|---|
| **BTM / microgrid techno-economics** | HOMER Grid, Xendee, NREL REopt, Energy Toolbase (ETB Developer) | Tariff databases (ETB: 70k+ rates; HOMER via Genability), demand-charge and TOU dispatch, incentives, simple project finance (NPV/IRR/payback), 25 technologies incl. fuel cells, SMRs (Xendee), data-centre marketing tracks (HOMER, Xendee) | None model reliability probabilistically (COPT/MC/ELCC), N-1 redundancy as a lever, SCR/dynamics gates, or multi-energy ENS. This tool's reliability depth is the moat. |
| **Project-finance performance models** | NREL SAM (single-owner PPA, partnership flip, sale-leaseback, MACRS, debt sculpting, solve-PPA-for-IRR, P50/P90), Excel templates | The financial layer, period. SAM is open-source and its financial model is a stable reference implementation. | Absent here. SAM's *single-owner* structure is the right first target. |
| **Market / portfolio simulation** | PLEXOS, Aurora (Energy Exemplar), Ascend PowerSIMM, Aurora Energy Research forecasts | Price forecasting, nodal/ancillary revenue, portfolio risk over 10–25 years | Not the goal; this tool should *consume* price scenarios, not produce market forecasts. |
| **BESS revenue analytics / PPA pricing** | Modo Energy (bankable BESS forecasts, DE/GB/US), Pexapark (PPA price benchmarks, EU) | Revenue stacking across DA/ID/FCR/aFRR, P50/P90 forecasts diligence-ready | Integration target (import their price/revenue curves), not a build target. |
| **Interconnection / site screening** | Nira Energy (injection studies per substation), Pearl Street (Interconnect, SUGAR), Paces (AI site + grid-capacity funnel for DC developers), envelio (hosting capacity) | "Where can I connect, when, at what upgrade cost" | gridspine's unbuilt connection-study variant is the nearest analogue; heavy data dependency. |
| **Data-centre development finance** | CRE-style DC development models (IT-load ramp × PUE → utility cost, lease yield) | Phased IT load, PUE, redundancy tiers as *financial* inputs | Physical side is stronger here; the DC load archetype is missing. |

**Market context that should shape the roadmap (2026):**

- Speed-to-power dominates: FLAP-D grid queues run 7–10 years against 18–24-month build windows;
  ERCOT had ~198 GW of large-load requests in Q1 2026; PJM queue ~2,600 GW. On-site gas/bridge
  power and co-location are now permanent design options, not fallbacks.
- Flexibility is becoming contractual: Texas SB6 (mandatory curtailment for large loads, PUCT
  Docket 59220 requires *full* co-located load to curtail), Ireland CRU Dec-2025 (on-site
  dispatchable generation/storage matching import capacity + 80 % additional renewables), Germany
  EnWG §17(2b) flexible connection agreements (Modo estimates −20 % BESS revenue under FCAs),
  Netherlands energy hubs (group capacity contracts as the congestion workaround; TenneT queue
  38 GW). Google announced 1 GW of DR contracts (Mar 2026); the Nvidia/Google/Emerald AI "AI
  Energy Management Alliance" launched 16 Sep 2026. Duke Nicholas Institute: ~98 GW of new load
  absorbable at 0.5 % annual curtailment.
- US tax landscape moved under the client's feet: OBBBA (Jul 2025) ends wind/solar ITC/PTC
  unless construction begins by 4 Jul 2026 or in service by end-2027; storage keeps credits to
  2034; FEOC content rules with safe-harbour tables due end-2026. Any US financial layer must
  treat incentives as versioned, dated rule packs, not constants.
- EU: Data Centre Energy Efficiency Package (PUE minimum standards, 2026) and EED reporting make
  PUE and hourly CFE reportable metrics, not marketing.

---

## 4. Gap analysis — the integrated tool chain an investor-facing user needs

Stages of the decision workflow, and where the tool stands:

| # | Stage | Need | Today | Gap size |
|---|---|---|---|---|
| 1 | **Describe the client's load** | DC load builder: IT MW ramp by phase, PUE as f(outdoor temperature), UPS/distribution losses, redundancy tier (N+1 / 2N) → installed vs usable, critical vs curtailable share, workload-shift duration, standby gensets with permit-limited runtime | A scaffold script (`scripts/scaffold_dc_heatpump.py`), `eh_critical` bus flag, DSR at 10 % of load, a tooltip | **Large** — client must hand-build |
| 2 | **Describe the grid interface commercially** | Firm vs non-firm (flexible) connection capacity, time-varying import caps, connection fee €/MW/yr, energy tariff (TOU, market-indexed), capacity/demand charges, network fees, curtailment obligations and compensation | PoC import link with a fixed MW cap; price = LP dual | **Large** — this is why "cost" today is system cost, not the client's bill |
| 3 | **Describe revenue and contracts** | PPA (pay-as-produced, baseload, CfD strike), merchant price series, ancillary-service revenue, DR programme payments, capacity market, REC/GoO value | Nodal-dual revenue only | **Large** |
| 4 | **Design & size** (physical) | Least-cost design meeting reliability; redundancy, storage duration, import cap levers; multi-year staging | **Strong** (EH stack) | Small — wire the skipped EH stages |
| 5 | **Certify reliability** | LOLE/EUE with MC, N-1, SCR/dynamics gate, FMECA | **Strong**, but MC/frontier/FMEA are `skipped` inside the EH driver | Small–medium |
| 6 | **Finance it** | Annual cash flows, capex phasing, degradation, escalation, debt & DSCR, tax & depreciation, incentives, NPV/IRR/payback, solve-for-PPA-price | Annuity only | **Large** |
| 7 | **Stress it** | Sensitivities on prices, capex, CF, delay; P50/P90; scenario matrix | Outage MC only; A/B compare | **Medium** — machinery (campaign, queue, scenario tree) exists, the axes don't |
| 8 | **Report it** | Bankable pack: design, reliability certificate, cash-flow, assumptions ledger, Excel handoff | Reference-design report + provenance chips + xlsx per asset | Medium — extend, don't rebuild |
| 9 | **Collaborate / govern** | Multi-user, roles, audit, versions | Done v1 | Small |
| 10 | **Site / connection screening** | Where to connect, when, at what cost | Not built (gridspine connection study spec only) | Large, data-bound; partner first |

---

## 5. Deep dive — how financial modelling fits coherently

### 5.1 Two views of the same design

PyPSA minimises **total system cost** (annuitised capex + opex) as if one social planner owned
everything and prices were the LP's shadow prices. An investor needs the **owner's cash-flow
view**: what one legal entity pays and receives each year under real contracts, financed with
real debt, taxed by a real jurisdiction. The first view answers "what should be built"; the
second answers "should *I* build it and on what terms". The tool must offer both, and they must
reconcile: the physical quantities are identical, only the *valuation* changes.

The per-asset "net profit" the Economics tab shows today is a merchant proxy under perfect
competition inside the client's own small network. For a behind-the-meter data centre that
number is close to meaningless, because the marginal price at the grid bus is set by the
client's own import link rather than by the market or the tariff.

### 5.2 Layered design (each layer maps onto something PyPSA already has)

**Layer A — Commercial layer (inputs to the optimiser).** The insight that keeps this coherent
is that almost every commercial construct is expressible with existing PyPSA primitives on the
point-of-connection link and the grid bus, so the optimiser dispatches against *the client's
bill* instead of a fictional dual:

| Commercial construct | PyPSA expression |
|---|---|
| Market-indexed or TOU energy tariff | time-varying `marginal_cost` on the import link (or on a "grid" generator behind the PoC bus) |
| Export revenue / price-taker sales | a "grid sink" load or negative-cost link with the export price series |
| Connection capacity fee €/MW/yr, demand charge | `capital_cost` on the import link's `p_nom` (extendable → the optimiser sizes the connection); monthly peak demand charge needs a small custom constraint (peak variable per billing period) |
| Non-firm / flexible connection agreement | time-varying `p_max_pu` on the import link, or a scenario axis of curtailment hours |
| Energy-hub group capacity contract | a `GlobalConstraint` over the sum of several import links |
| Grid arriving in year N (speed-to-power) | per-vintage bounds on the import link across investment periods — already supported |
| PPA pay-as-produced at fixed price | a revenue adjustment in Layer B (does not change dispatch) or a `marginal_cost` offset if the PPA changes incentives |
| CfD strike | Layer B: (strike − reference price) × volume |
| Curtailment obligation (SB6-style) | mandatory load reduction in named hours: time-varying `p_set` scaler on the DC load or a stress-class entry |
| DR / flexibility programme payment | already a DSR resource; add a €/MW-yr availability payment in Layer B |

This is a modelling convention plus a schema, not a new solver. It also fixes the deeper problem:
"cost at target" in the EH report becomes the client's cost at target.

**Layer B — Finance layer (post-processor on solved results).** Consumes: per-asset capacities
and build years (from `p_nom_opt` and vintages), hourly dispatch, imports/exports, the
commercial layer's prices, plus finance inputs. Produces an annual cash-flow timeline per asset
and per project entity:

1. Capex schedule (overnight cost, construction period, phasing, contingency), replacement
   capex at end of component life (batteries), decommissioning/terminal value.
2. Operating cash flows: energy revenue (merchant, PPA, CfD), tariff costs, capacity/DR
   payments, FOM (with escalation), VOM, fuel (heat rate × fuel price once separated),
   degradation of yield.
3. Financing: debt sizing (gearing or DSCR-sculpted), tenor, rate, fees, DSRA; equity.
4. Tax: corporate rate, depreciation schedule (straight-line / declining balance / MACRS),
   loss carry-forward, incentives (ITC/PTC with dated OBBBA rules, grants, accelerated
   depreciation, CfD treatment).
5. Outputs: project IRR, equity IRR, NPV at WACC, payback, min/avg DSCR, LLCR, finance-consistent
   LCOE, **PPA price required to hit a target IRR** (the single most-asked question), and
   per-year tables exportable to Excel because bankers will re-run it there.

The existing WACC-style `discount_rate` becomes an *output-consistent* input: the LP's annuity
rate should equal the finance layer's WACC, and the report should flag when they differ.

**Layer C — Uncertainty on money.** Reuse the campaign / solve-queue / scenario-tree machinery
(a `sensitivity` scenario type is already anticipated in `db/models.py` but not added) to sweep
price paths, capex, capacity factor, delay-to-grid. Distinguish two speeds: sweeps that change
dispatch (need a re-solve) and sweeps that only change valuation (finance-only re-evaluation,
seconds). Output tornado charts and P50/P90 on IRR/DSCR.

### 5.3 "Tax forms" and jurisdiction packs

The term in the brief is best read as *tax structures and incentive forms*. Recommendation:
make the finance layer jurisdiction-agnostic and ship **dated rule packs**, exactly like the EH
archetype packs (hash, provenance, `not_established` when a rule is unknown):

- **EU generic / DE / NL (first).** No federal credits; corporate tax rate, straight-line or
  declining-balance depreciation, grid-fee regime (Netzentgelte, capacity-based tariffs,
  §17(2b) FCA discounts), CfD/EEG treatment, national grants. The fork is PyPSA-Eur and the
  owner's practice is European, so this is where the first paying users are.
- **US federal (second).** ITC/PTC with OBBBA begin-construction / placed-in-service dates,
  storage-only credit runway to 2034, FEOC content test, MACRS 5-yr + bonus depreciation,
  transferability. Tax-equity *partnership flip* is complex and US-specific; ship
  **single-owner** first (SAM's default) and treat flip as P2.
- Never silently convert or infer: a pack that lacks a rule sets the output to `null` and flags
  it, consistent with ADR-0001.

### 5.4 Build vs integrate

- **Build** Layers A and B in-tree: they are thin relative to the solver stack, they must
  share the provenance discipline, and they are what makes the reference design *bankable*.
- **Reference** NREL SAM's single-owner cash-flow model for equations and as a test oracle
  (open source, widely trusted by lenders). Do not embed SAM.
- **Integrate, don't build:** price forecasts (Aurora Energy Research, Modo, Pexapark),
  tariff databases (ETB / Genability-style), interconnection queue data (Nira, Paces). Provide
  clean import schemas and let the consultant bring the data.

---

## 6. Data-centre and energy-hub energy behaviour — what "model their energy behaviour" needs

A **DC load archetype** (a builder that emits PyPSA components, like the EH archetype packs):

- IT load ramp by phase (MW per hall, commissioning dates → investment periods), utilisation
  profile (training vs inference: AI training is flat-high with abrupt steps; inference is
  diurnal), and a stochastic short-term variability option for the dynamics gate.
- PUE as a function of outdoor temperature (cooling is the seasonal component) — PyPSA-Eur's
  cutouts already have temperature; a `Link` with time-varying efficiency does this.
- Redundancy tier as a *lever* (N, N+1, 2N) changing installed vs usable capacity and feeding
  the existing redundancy scenarios.
- Critical vs curtailable load split (`eh_critical` already exists) and a workload-shift
  resource (energy-neutral over a window; a Store with zero standing losses models it) — the
  Emerald AI / Google DR pattern.
- Standby gensets with fuel, permit-limited run hours (a `e_sum_max` bound — the primitive
  exists), and emissions; bridge-power gas as a dated vintage that can retire when grid arrives.
- Waste-heat export to a district heat network (already a multi-port link; make it part of the
  archetype and price it in Layer A).
- **24/7 CFE hourly-matching score** as a results metric: share of consumption matched by
  contracted or on-site carbon-free generation in the same hour. Cheap to compute from existing
  dispatch; increasingly demanded by hyperscaler procurement and EU reporting.

An **energy-hub group contract** needs the aggregate-import `GlobalConstraint`, a member
allocation rule (cost/benefit split — Layer B), and a congestion-service revenue line.

---

## 7. Prioritised roadmap

Effort is order-of-magnitude for one strong developer with the existing test discipline.
Value is judged against "a hyperscaler, hub or IPP would use this for a real decision".

| Prio | Item | Why now | Depends on | Effort |
|---|---|---|---|---|
| **P0-1** | **Commercial layer v1** — schema + builders for: exogenous price series on the PoC (price-taker mode), TOU/indexed import tariff, connection capacity fee on `p_nom`, export price, non-firm cap as time-varying `p_max_pu`, group-cap global constraint. Surfaces in EH report as "client cost at target" beside system cost. | Without it every € the tool reports is system cost, not the client's bill; blocks everything financial. | none | 4–6 weeks |
| **P0-2** | **Finance layer v1 (single-owner)** — annual cash-flow engine per asset/entity, capex phasing, degradation, escalation, simple debt (gearing or DSCR-sculpted), corporate tax + depreciation, EU-generic and DE/NL rule packs, NPV/IRR/payback/DSCR, solve-for-PPA-price, xlsx export, provenance flags. | The deliverable investors actually sign off. Reference SAM single-owner for equations and as oracle. | P0-1 for prices | 6–8 weeks |
| **P0-3** | **Data-centre load archetype** — builder emitting IT ramp × PUE(T) load, redundancy tier lever, critical/flex split, gensets with run-hour cap, waste-heat link; 24/7 CFE metric. Add as a fourth EH pack dimension. | Turns "energy hub reference design" into "data-centre reference design" with a few inputs; the client's own energy behaviour becomes a first-class input. | none (uses existing EH levers) | 3–4 weeks |
| **P1-1** | **Wire the skipped EH stages** (frontier, MC certify, FMEA top-N) into the driver so `mc_lole_h` is established and the report stops saying `not_established` for weak/off-grid. | A design without a certified LOLE is not bankable; the engines exist, the plumbing is missing. | none | 2–3 weeks |
| **P1-2** | **Financial sensitivity & scenario matrix** — add the `sensitivity` scenario type, finance-only re-evaluation path, re-solve sweeps via campaign budget, tornado + P50/P90 on IRR/DSCR, multi-scenario compare (beyond A/B). | Lenders ask "what if" before "how much"; machinery exists, axes don't. | P0-2 | 4–5 weeks |
| **P1-3** | **Speed-to-power staging** — templated multi-period study: bridge power (gensets/BESS) with grid import cap rising by vintage; NPV of waiting vs building BTM. | The #1 2026 hyperscaler question; needs only per-vintage bounds + finance layer. | P0-1, P0-2 | 2–3 weeks |
| **P1-4** | **US federal rule pack** (ITC/PTC with OBBBA dates, MACRS, FEOC flag, storage runway). | Needed for any US client; dated packs keep it honest. | P0-2 | 2–3 weeks |
| **P2-1** | **Revenue stacking for BESS/flex** — heuristic FCR/aFRR/DR availability payments with capacity reservation reducing arbitrage energy; import Modo-style revenue curves. | IPP/BESS bankability; keep heuristic, don't build a market model. | P0-1 | 3–4 weeks |
| **P2-2** | **Bankable report pack** — extend `ReferenceDesignReport` with finance section, reliability certificate, full assumptions ledger incl. financial inputs and rule-pack hashes; one-click PDF/xlsx. | Consultant deliverable; extends what exists. | P0-2, P1-1 | 2–3 weeks |
| **P2-3** | **Fuel/heat-rate separation and fuel-price series**, emissions permits for on-site gas. | Bridge-gas economics are fuel-price-driven; today fuel is folded into `marginal_cost`. | none | 1–2 weeks |
| **P2-4** | **Tax-equity partnership flip** (US) and member cost-allocation for energy hubs. | Only after single-owner is trusted. | P1-4 | 3–4 weeks |
| **P3-1** | **Connection-study variant of gridspine** (host grid + request → screening → SCR/WSCR → RfG/VDE compliance) and PowerFactory read-back run for real. | Hyperscaler grid-code obligations; heavy on external data and PowerFactory access. | data access | 8+ weeks |
| **P3-2** | **External data adapters** — tariff DB import, market-price scenario import (Aurora/Modo/Pexapark formats), interconnection-queue/capacity-map import (Nira/Paces/envelio). | Integration, not modelling; partner-dependent. | P0-1 | ongoing |
| **P3-3** | **Horizontal scaling** (SaaS plan steps 2–3: move the live network out of process memory). | Sensitivity sweeps and multiple client teams will hit the process-global network limit. | none | large |

**Explicit non-goals** (to keep the plan coherent): no in-house long-term market-price
forecasting engine (PLEXOS/Aurora territory), no in-tree EMT (gridspine hands off), no tariff
database curated in-house, no replacement of the client's Excel model — the finance layer exports
to it.

### What "done" looks like for the P0

A consultant opens a `data_centre` archetype, enters 3 phases × 40 MW IT load, PUE 1.2, N+1,
30 % flexible, a 60 MW non-firm connection arriving in 2029 with a €/MW/yr fee and a
market-indexed tariff, a 15-year PPA offer at €X/MWh, 60 % gearing at 5.5 %, DE tax pack; runs
the EH study; and receives, in one report with provenance flags: least-cost design at 10‱ ENS
with certified LOLE, client cost at target vs system cost, project and equity IRR, min DSCR, the
PPA price that clears a 9 % equity IRR, hourly CFE score, and a tornado over power price, capex
and grid-arrival year — all exportable to Excel.

---

## 8. Sources (external)

- HOMER data-centre solutions — https://homerenergy.com/solutions/data-centers
- REopt Lite BTM DER framework (NREL) — https://arxiv.org/pdf/2008.05873
- Xendee data centres / SMR microgrid platform — https://xendee.com/data-centers ; https://www.datacenterdynamics.com/en/news/xendee-and-university-of-chicago-develop-platform-to-help-data-centers-integrate-microgrids-and-smrs/
- Energy Toolbase review (tariff DB, dispatch) — https://qbitsenergy.com/blog/energy-toolbase-review/ ; https://www.energytoolbase.com/solutions/etb-developer/
- HOMER Grid features — https://apps.list.solar/tools/homer-grid/
- NREL SAM financial models — https://sam.nlr.gov/financial-models.html ; https://samrepo.nrelcloud.org/help/fin_overview.html
- PLEXOS / Aurora (Energy Exemplar) — https://www.energyexemplar.com/plexos ; https://www.energyexemplar.com/aurora
- Ascend Analytics PowerSIMM — https://www.ascendanalytics.com/solutions/powersimm-suite ; large-load queues — https://www.ascendanalytics.com/blog/large-load-interconnection-queues-data-center-grid-access
- Modo Energy forecasts / German FCA impact — https://modoenergy.com/product/forecasts ; https://modoenergy.com/research/en/germany-january-2026-flexible-connection-agreement-battergy-energy-storage-grid-access
- Pexapark — https://pexapark.com/
- Nira Energy — https://www.latitudemedia.com/news/how-nira-energy-is-using-software-to-unclog-the-interconnection-queue/ ; Pearl Street — https://pearlstreettechnologies.com/ ; Paces — https://www.paces.com/data-center-developers
- Texas SB6 — https://www.bakerbotts.com/thought-leadership/publications/2025/july/texas-senate-bill-6-understanding-the-impacts-to-large-loads-and-co-located-generation
- Hyperscaler PPA structures 2026 — https://www.computeforecast.com/articles-post/hyperscaler-power-purchase-agreement-structure-2026-ai-infrastructure/
- Data-centre flexibility (Duke Nicholas Institute, DCFlex, AEMA) — https://www.utilitydive.com/news/data-centers-flexibility-utilities-speed-to-power/822588/ ; https://www.axios.com/2026/09/16/tech-giants-launch-flexible-power-coalition-data-centers
- OBBBA clean-energy credits — https://www.kirkland.com/publications/kirkland-alert/2025/08/one-big-beautiful-bill-act-brings-big-changes-to-green-energy-tax-credits ; https://www.bakerbotts.com/thought-leadership/publications/2025/july/one-big-beautiful-bill-act-substantially-alters-clean-energy-tax-landscape
- Europe grid queues / Ireland CRU / NL energy hubs / DE EnWG FCAs — https://avanzaenergy.substack.com/p/the-176-billion-detour-how-europes ; https://www.abnamro.com/en/news/grid-congestion-gets-companies-negotiating-with-grid-operators ; https://www.gleisslutz.com/en/know-how/flexible-connection-agreements-typical-models-and-negotiation-points ; https://www.morganlewis.com/pubs/2026/04/german-government-adopts-national-data-center-strategy
- EU data-centre regulation 2026 (PUE, reporting) — https://www.moduledge.com/blog/eu-data-center-regulations-2026
- BESS revenue stacking Europe — https://www.phelas.com/en/insights/knowledge/battery-storage-revenue-stacking ; https://modoenergy.com/research/en/how-does-battery-energy-storage-make-money
- Data-centre load modelling / PUE / redundancy — https://www.pscconsulting.com/news-insights/data-center-load-modeling-and-assessment ; https://www.adventuresincre.com/data-center-development-model/
- BTM architectures for AI data centres (2026) — https://doi.org/10.3390/electricity7020043
