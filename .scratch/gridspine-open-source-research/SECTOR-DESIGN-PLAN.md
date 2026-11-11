# GridSpine: sector expansion design and integration plan

Date: 2026-10-20 (Europe/Berlin). Status: revised proposed implementation design, not implemented, scientifically approved or published. Independent verdict is recorded separately in INDEPENDENT-REVIEW.md.

## Recommendation and scope

Build eight reusable example families on the existing planning, electrical, commercial and harness foundations: commercial/industrial (C&I), data centers, independent power producers (IPPs), green hydrogen, ammonia, mining, microgrids, and hydrogen-to-other-products. The last is an extensibility path, not a promise to support every chemical process at once. Treat the user's CNI/EP references as C&I/IPP in this document.

The highest-value additions are demand confidence, battery lifecycle, bounded process-performance models, production-aware reliability, and operating-strategy comparison. Use external packages behind curated adapters. Do not put a second optimization or finance application beside GridSpine.

Read with the normative [five-card modular contract](FIVE-CARD-MODULARITY.md), [implementation backlog](IMPLEMENTATION-BACKLOG.md), [acceptance matrix](SECTOR-TEST-MATRIX.md), [adapter readiness gates](ADAPTER-READINESS.md), [additional sources](SECTOR-SOURCES.md) and [handoff](SECTOR-HANDOFF.md). Earlier [research](RESEARCH.md) remains a historical snapshot; its three-group layout is superseded.

## 1. Fresh source baseline and existing work

Fresh remote master for this revision was `90c7b8ffcec104397383361212da0aa21f545f1b`. Since the prior plan baseline `51ddf8b0bf11f5375b30c0bf84825ff411ae5d45`, master added the docs-only IPP renewables/BESS spec in PR453. Its gaps and native commercial/dispatch boundaries must be reused, not reimplemented as a competing IPP plan. Earlier changes since original research `c7be9512861914b40aeb31bb6b555421f61b1642` concerned designs/coordination, not additional simulation engines. No worktree checkout was changed. Later implementation must reassess fresh master and open work before editing.

Important correction: [ammonia PR #418](https://github.com/MLHaoChang/pypsa-eur/pull/418) is merged, commit `0171f1d61cba6c76f3d22ce7ffe72572eaa9f23e`. It is no longer a future template to build from scratch.

| Existing implementation or design | Reuse and boundary |
| --- | --- |
| PyPSA/Linopy/HiGHS capacity, dispatch and investment-period paths | Own the planning decision and objective. External models supply validated coefficients/envelopes or evaluate a frozen candidate. |
| pandapower, native campus/island/fault studies | Remain first-line electrical checks. The checked-answer branch-loading profile is a separate, narrower claim scope; do not confuse it with all native studies. |
| Native adequacy, tariffs, PPA/CfD settlement and finance | Extend coverage and evidence; avoid replacing the engines. Existing SAM finance fixtures are not a new integration. |
| Native DC profile, assumed temperature/PUE and IT envelope | Extend cooling/calibration and workload constraints. Do not rebuild the profile or equate it with a validated cooling plant. |
| H2 hub, staged hubs, ammonia tracer, process schema/committable Links | Extend their actual restrictions. Ammonia's Haber-Bosch unit is currently fixed-size and committable, not extendable; its ASU is explicitly outside the template boundary. |
| `.scratch/grid-to-process/issues/04,06–14` | Existing backlog for multiport results, process economics/forms, production criticality, chronological year, concentrator, counterfactuals, motor restart and assistant fields. Their documents say `needs-triage`; reconcile actual code/PR status before creating duplicate work. |
| `.scratch/project-workflow/library-and-live-lookup.md` | One parameter library; find → review → use → save, version pins, local/org/common scopes, bounded official-source lookup. These are design commitments, not proof all slices are implemented. |
| `.scratch/checked-results/audit.md` | Result-profile coverage backlog, fail-closed dispositions and chart evidence. New sector results must join it; do not assume finance/process/multi-period answers are already checked. |
| `.scratch/ipp-renewables-bess/spec.md` | New docs-only IPP plan: capacity and commercial on, grid/control initially off; PPA share/site-sold settlement, price-series/template and revenue-view gaps; seller PPA v1 settles after dispatch. Reconcile group naming with the owner's latest five-card instruction. |

The native source map is in RESEARCH.md. Existing design references are available on [the reviewed master](https://github.com/MLHaoChang/pypsa-eur/tree/51ddf8b0bf11f5375b30c0bf84825ff411ae5d45/.scratch).

## 2. One experience for all examples

After login, show the example/workflow overview with Resume. One card per example family; no separate single-period and staged-growth cards. Selecting a card, including the requested right-click shortcut, enters its builder without mutating a project. Provide equivalent visible/keyboard/touch activation; right-click is never the only route.

The builder preselects relevant capabilities, then asks for planning start/end and optional investment dates. Preserve backend template identifiers during migration. A supported single-period path and supported multi-period path must both be tested; an unsupported combination is explained before creating/running the project. Never drop investment dates silently.

Use the owner's **five modular analysis cards**, replacing the earlier three-group proposal:

1. **Energy / capacity expansion:** native capacity/dispatch and optional demand, process, thermal and lifecycle detail.
2. **Load flow & grid:** electrical sizing, supported flow, contingency and precisely scoped grid-code assessments.
3. **Reliability:** adequacy, critical service, lost production and stress studies, using relevant grid/control evidence when available.
4. **Commercial:** tariffs, contracts, procurement/merchant exposure and finance.
5. **Control strategies:** operating-policy replay, eligible native GFM screening, process restart and separately admitted dynamics.

These select capabilities over the same canonical project inputs. They do not duplicate master's field-owner sections or make every preceding card mandatory. Commercial deselection preserves native objective costs; Energy deselection permits admitted fixed/imported-model studies. Full dependency, selection/removal, migration and evidence rules are in FIVE-CARD-MODULARITY.md.

Offer “Select all applicable,” with a clear list of unavailable capabilities and their missing prerequisites. It cannot bypass eligibility or silently add unsupported simulations. Advanced for this step and Expert edit the same project fields, with no shadow model. Every example remains editable in Guided and Expert. One bottom-right companion invokes the same typed actions and approval flow.

| Family | Energy | Load flow & grid | Reliability | Commercial | Controls |
| --- | --- | --- | --- | --- | --- |
| C&I | Capacity selected; process/thermal optional | Optional | Optional | Tariff/finance selected | Off; optional policy replay |
| Data center | Capacity selected | Supported sizing/readiness selected | Critical-service/adequacy intent selected | Selected | Optional, architecture/model dependent |
| IPP/hybrid | Selected | **Initially off**, optional | Initially off, optional | Selected | **Off by default** |
| Green H2 | Capacity/process selected | Optional | Optional delivery/supply risk | Selected | Optional policy replay |
| Ammonia | Capacity/process selected | Optional | Production/stress intent selected | Selected | Optional process restart |
| Mining | Capacity/process selected | Weak-grid sizing intent selected | Production/stress intent selected | Selected | Optional restart/pickup |
| Microgrid | Capacity selected | Supported island/network checks selected | Critical-service/adequacy selected | Applicable costs/finance selected | Optional policy replay and **explicit eligible GFM opt-in** |
| H2-to-products | One admitted recipe selected | Optional | Optional | Selected | Optional product-specific policy |

These are capability selections, not claims all backend paths are ready. Preview must identify unavailable data/engines instead of offering a fake successful run.

## 3. Shared technical integration

### 3.1 One source of truth and bounded workers

Extend existing services and jobs rather than create a new parallel platform. Proposed names below are design contracts, not existing APIs.

`SectorPreset` selects native components, parameter-library pins, relevant study capabilities and declared boundaries. Its selections reference versioned server-authoritative feature definitions from FIVE-CARD-MODULARITY.md; labels/checkboxes grant no execution authority. `ModelEnvelope` records physical ports, units, input bounds, performance curves, validity, uncertainty and source/model hashes. `AdapterRun` binds an immutable project revision/run, scenario, engine/version and worker-image digest to inputs, outputs, logs and resource limits. `ClaimDisposition` maps each requested metric to a checked profile, unchecked evidence or an unavailable reason under the existing assurance contract.

All exchanges carry canonical asset IDs; timezone and interval duration; snapshot and investment-period weights; currency and real/nominal year; mass and energy basis (including H2/NH3 LHV or HHV); operating ranges; calibration status; and a boundary stating included auxiliaries. Distinguish missing, assumed, illustrative, measured and fitted inputs.

Integrate in three modes:

- **Coefficient provider:** an external model produces a reviewed efficiency/operating envelope. Native planning optimizes within its bounds. Interpolation is tested; extrapolation is refused unless separately supported.
- **Frozen-candidate evaluator:** evaluate exact native capacities/dispatch and return mismatches or sensitivities. A failed replay never certifies the economic solution. Applying revised assumptions requires a reviewed diff and a new native run.
- **Independent oracle:** compare a matched supported subset without importing another objective, finance basis or dispatch policy.

For nonlinear planning feedback, first use explicit bounded iterations: fit → solve → replay → propose correction → rerun. Record stopping tolerances, max iterations and nonconvergence; do not claim a globally optimal coupled solution. Committable-process sizing remains fixed or a documented finite candidate sweep until a separately reviewed formulation supports more.

Optional workers solve dependency conflicts and keep default installation light. Reviewed BLAST-Lite requires NumPy <2; OpenDSM requires >=2. H2Integrate declares Python >=3.11,<3.14 and a broad OpenMDAO/PySAM/Pyomo stack, so initially isolate it too. Do not upgrade native pins to satisfy it. Disable worker networking by default; stage approved data before execution. A process boundary does not remove license obligations.

### 3.2 Chronology and coupling

Use full chronology where inventories, monthly peaks, long outages, startup/cooldown or shipment schedules require it. Existing grid-to-process ticket09 specifies a nominal 8,760-hour year per period and a measured runtime/memory gate. Preserve that decision and declare leap/calendar mapping; do not silently coerce an actual leap-year meter series into it. Representative periods remain eligible for previews only where their error/boundary is declared.

Carry a traceable operating point from planning or an admitted imported/fixed model to electrical evaluation; carry admitted electrical trip/curtailment events to a production/critical-service replay. Keep instantaneous electrical response, seconds/minutes restart, hourly dispatch, annual weather and investment periods distinct. Monetary penalties cannot stand in for physically supplied electricity or product. Reliability must retain its method/weather/outage/seed/sample/stopping/uncertainty basis; battery ageing must use physical calendar time and declared duty repetition/replacement resets, never infer them from optimization weights. See FIVE-CARD-MODULARITY.md §7.

### 3.3 Evidence and assistant reliability

Register every adapter metric in the existing result/assurance inventory. Retain raw time series sufficient to reconstruct the metric. Adapter execution success proves execution, not truth or domain approval. Checked assistant claims require deterministic reconstruction against the exact source, units, asset and time scope. Until an approved profile exists, withhold or explicitly mark unsupported using the current contract; never widen the checked tool allowlist as a shortcut.

Model edits, runs and library writes use current permissions, locks, confirmations, cancellation and durable effect receipts. A denial, stale revision, failed/partial job, wrong owner, mismatched model or corrupted artifact must not produce a success claim. Native result views retain freshness; copilot-authored claims carry check state. Do not say numbers are displayed without a matching displayed-data acknowledgment.

Use existing library storage and pins for curves, archetypes, control presets and process recipes, extending kinds only through its schema/versioning rules. Metadata verification changes do not change content hashes. Fetched documents are data; any future live lookup inherits the server-side confinement design, not arbitrary library-internal web requests.

## 4. Plans by use case

### A. Commercial and industrial energy systems

**Decision:** what to build or change, given actual demand, tariff, production/thermal needs and outage cost?

**Incremental features:** meter quality/interval/DST report; measured-versus-synthetic load provenance; weather-normalized baseline and held-out error; supported bill reconciliation; thermal equipment maps; battery replacement and degraded-capacity scenarios. Savings measurement is a later optional report, not an automatic causal claim from a planning forecast.

**External tools:** oemof-demand for synthetic archetypes; OpenDSM for statistical meter/weather baselines; TESPy for selected thermal maps; BLAST-Lite for ageing; PySAM for supported independent tariff checks. All native optimization, rating and finance stay.

**Implementation:** (1) import a known site's interval meter/tariff/bill and reconcile the native bill; (2) add synthetic/fitted profile adapters with separate labels; (3) add one heat-pump/chiller or CHP envelope; (4) compare PV/BESS/process options with matched chronology, constraints and costs. Missing demand-charge terms remain explicit incomplete coverage.

**Evidence gate:** annual energy and monthly peaks reconcile independently; DST/ratchet/tiers fixtures; baseline holdout; thermal energy balance; no replacement double-count. First release need not install every listed tool: native import and one supported bill are the first useful outcome.

### B. Data-center energy hub

**Decision:** how to serve critical IT through weak-grid capacity, growth and disturbances, at an understandable lifecycle cost?

**Incremental features:** calibrated IT/cooling separation; explicit PUE boundary; cooling/weather/water sensitivities; workload deadlines and genuinely deferrable fractions; aged UPS/BESS capacity; staged connection energization; joint heatwave, outage and ramp scenarios. Retain native AI-training/envelope capabilities.

**External tools:** selected HPE SustainDC/SustainCluster thermal/workload models, TESPy only when a specific cooling plant requires it, BLAST-Lite. A selected dynamics adapter is later, conditional on validated UPS/inverter/controller models.

**Implementation:** (1) compare existing PUE approximation to a calibrated cooling evaluator on the 50 MW campus/40 MW connection; (2) add deadline-constrained workload scenarios and staged growth; (3) evaluate aged batteries and fuel-limited gensets under heatwave/outage; (4) connect separately admitted ride-through/island evidence. Avoid importing HPE's RL training platform.

**Evidence gate:** no cooling counted twice; critical IT never reclassified as flexible; heat-removal/temperature constraints; electrical power and stored energy separately tested; thermal studies cannot prove subsecond continuity or an uptime tier.

### C. IPP and hybrid renewable developer

**Decision:** which plant, storage and contract portfolio performs acceptably across resource, price, delivery and connection scenarios?

**Incremental features:** versioned weather/price ensembles; capture-price/negative-price exposure; shaped delivery and residual-purchase risk; degradation-adjusted firming; comparable curtailment/connection cases; optional capacity-value benchmarking. Keep scenario observations distinct from forecasts and accredited capacity.

**External tools:** BLAST-Lite; regional market-data adapters (reuse ENTSO-E; gridstatus only for applicable US markets); pypsa-ppa as formulation/test reference, not production dependency; PRAS as an independent bulk-adequacy benchmark. HOPP is a conditional independent hybrid-performance reference, not a replacement optimizer. Reuse native IPP PR453's pending gaps/fixtures. Its seller PPAs settle after dispatch, so contract comparison is not contract-optimal capacity/dispatch; controls remain unchecked by default.

**Implementation:** (1) freeze a native wind/PV/BESS base and reconcile existing settlement/finance; (2) add a few consistent weather/price/contract cases; (3) add lifecycle and delivery-risk analysis; (4) admit detailed connection/dynamics only for a defined unsupported phenomenon.

**Evidence gate:** compatible price basis/currency/timezone; no revenue or reserve double-count; physically feasible delivery; portfolio aggregation conservation; full-tail scenarios; controlled comparison to external references.

### D. Green-hydrogen production and storage

**Decision:** electrolyser/storage/renewables sizing and operation to meet H2 quantity/quality/schedule at a credible cost and declared emissions boundary.

**Incremental features:** part-load H2 production maps; explicit compression, water-treatment and auxiliary electricity; stack degradation/replacement assumptions; startup/min-load/ramp restrictions where supported; storage inventory/pressure bounds; delivery shortfall and grid-supply exposure. Account for by-products only with a defined feasible use and value.

**External tools:** H2Integrate's selected electrolyser/storage/component models, subject to exact-component gates in ADAPTER-READINESS.md; optional TESPy/CoolProp-based engineering maps where required. Source review flagged PEM water-unit and controller conservation risks; do not admit affected outputs/modes until reproduced and resolved or refused. WaterTAP is later only if water treatment/desalination changes the decision. Do not assume a detailed pressure vessel or hydrogen-safety simulation exists because a technoeconomic storage model does.

**Implementation:** (1) evaluate one native H2 hub with fixed capacities and chronological operation; (2) obtain a pinned external H2 yield/auxiliary reference; (3) fit a bounded native envelope and replay candidate dispatch; (4) add delivery/reliability and staged costs. Keep native finance; do not adopt H2Integrate's alternate finance objective.

**Evidence gate:** kg versus tonnes, MW versus MWh and LHV/HHV conversions; water/material balances; auxiliary accounting; depleted inventory/zero input/min load; vendor-map bounds; differences from external reference explained.

“Green” is an ambition, not a result label inferred from wind/PV ownership. Report electricity sources, emissions factors and matching boundary. Any regional eligibility/certification claim requires its own current rule set and evidence; no automatic certificate.

### E. Green ammonia

**Decision:** how to supply 50/100/150 t/day over selected investment dates, with credible H2/N2 supply, process operating limits and delivery risk?

**Incremental features:** explicit air-separation/N2 and auxiliary boundary; realistic process-performance envelopes; warm/cold-start and production recovery sensitivities; H2/NH3 inventory delivery; grey buy-in versus actual shortfall versus own output; traceable cost/emissions counterfactuals. The existing tracer already includes fixed committable Haber-Bosch, storage and buy-in/shortfall logic.

**External tools:** a specifically validated H2Integrate synloop component for chronological reference/envelopes; its inspected simple-ammonia component is limited to annual reference/costing because production does not depend on hourly H2 input values. See ADAPTER-READINESS.md. TESPy is conditional for material thermal integration; IDAES only if a specific detailed flowsheet is justified later.

**Implementation:** (1) reconcile grid-to-process tickets04/06/07 and their live code; (2) add N2/ASU explicitly without duplicating existing compressor electricity; (3) cross-check a frozen candidate against H2Integrate; (4) admit validated startup/heat assumptions and production-aware outage replay. Never stack both engines' startup losses or change native commitment semantics implicitly.

**Evidence gate:** H/N and energy balances on declared boundaries; t/day and MWh_NH3 conversions; no double-count of grey-purchase emissions/cost; stockpile/contract reconciliation; fixed committable sizing respected. H2Integrate's synloop constraints are operating approximations, not a first-principles reactor transient or safety case; its documented zero-production startup behavior must be checked against actual standby/start energy.

### F. Mining and concentrator energy system

**Decision:** energy supply and process buffering to maintain production through weak-grid limits, outages and phased electrification.

**Incremental features:** native concentrator example with crusher, ore stockpile, mill lines, flotation, product stockpile and shipment schedule; safe/critical/flexible process tiers; throughput/energy maps and restart; non-firm connection envelope; later charging/haulage demand. Measure lost product and energy cost per tonne, not just ENS.

**External tools:** initially none is required for the native concentrator template. Reuse demand/ageing/thermal adapters where relevant. H2Integrate's iron-mine/DRI models are a possible iron-specific extension; they are not generic copper/gold/concentrator models. OpenMines is a later truck-dispatch simulation reference, not an electrical energy or whole-mine engine.

**Implementation:** (1) complete/reconcile existing tickets08/09 before ticket10; (2) build the agreed chronological concentrator template and ticket11 counterfactual; (3) add validated throughput maps and ticket12 restart/pickup screening; (4) only then test a measured haulage-to-charging demand adapter. Keep pit planning, geology and fleet optimization outside the initial energy-planning release.

**Evidence gate:** ore/product recovery and inventory conservation; 21-day shipments cannot use an independent repeated week; mill and stockpile constraints; interrupted production caught up only within feasible capacity; coupled restart losses cannot be added independently when that double-counts downstream loss.

H2Integrate's iron-mine documentation identifies regional legacy cost inputs and an AI-assisted electrical breakdown. These may inform a clearly illustrative sensitivity, but cannot become an approved customer or global mining default without independent input validation and currency-year/site recalibration.

### G. Island and grid-connected microgrids

**Decision:** capacity, reserves, fuel and operating strategy that sustain declared critical service during island operation.

**Incremental features:** multi-weather/outage/fuel-delivery scenarios; aged BESS capability; nonanticipative policy replay with forecast errors; explicit reserve/SOC and critical-load pickup; transition/restart evidence where supported. Existing island microgrid and staged examples remain the starting point.

**External tools:** BLAST-Lite; python-control for admitted feedback models; do-mpc later for a calibrated MPC policy. An opt-in grid-forming feature reuses native screening first and its actual converter/headroom/network/disturbance boundaries; python-control does not certify GFM. ANDES or OpenIPSL/FMPy only for a selected validated dynamic model; OpenDSS only for a demonstrated distribution/unbalance gap. PRAS is not the UPS/island-transition verifier. GFM selection does not imply black-start, protection or reconnection validation.

**Implementation:** (1) replay the existing no-subsea-tie island case with constrained fuel/initial SOC; (2) compare rule-based operating strategies on identical disturbances; (3) add ageing, forecast error and common-cause/weather scenarios; (4) add one detailed dynamic adapter if native screening is inadequate. No live EMS/SCADA actuation.

**Evidence gate:** no perfect-foresight claim for implementable controllers; reserve is not sold/used twice; fuel/SOC and pickup-order conservation; all-critical-load survival cannot be inferred from annual ENS alone; reconnection requires an admitted synchronism model.

### H. Hydrogen-to-other-products expansion

**Decision:** should H2 be sold, used for ammonia, or converted into one supported alternative product?

**Incremental features:** versioned product recipes with typed electricity/heat/water/H2/CO2/N2/material ports; inventory/offtake and minimum production constraints; common supply-chain cost/emissions boundaries. Admit one additional product after ammonia rather than expose an arbitrary flowsheet editor.

**External tools:** H2Integrate for a selected methanol or DRI/steel model; IDAES for a later justified process model. WaterTAP remains conditional. A library having a product model does not establish compatibility with all native scheduling/finance assumptions.

**Implementation:** (1) choose one calibrated product recipe; (2) evaluate it on fixed native supply; (3) implement/verify its bounded planning envelope and all-port results; (4) compare alternatives on both common upstream boundaries and product-specific units. Methanol requires a defined CO2 supply; DRI requires ore quality and reductant/process parameters.

**Evidence gate:** species and energy conservation, no free feedstocks or circular accounting, by-product revenue feasibility, product-quality/model applicability, correctly scoped carbon accounting. Do not equate unlike EUR/t denominators or claim identical service from unlike products.

## 5. Priority and sequencing

**P0: shared foundations.** Reconcile existing library, checked-results, grid-to-process and IPP work; define five-card registry, standalone input providers, dependency/lifecycle/migration semantics, adapter contracts and versioned optional workers; complete applicable chronology/production-accounting gaps. These are often more important than a new dependency. A bounded H2Integrate/BLAST feasibility investigation can establish compatibility and exact-component limits before product commitments.

**P1: first useful outcomes.** C&I demand/bill proof; a battery lifecycle slice reusable across sectors; one H2 performance adapter and extension of the merged ammonia tracer. Add native concentrator only after its chronology/criticality prerequisites. Deliver thin end-to-end studies rather than eight unfinished integrations.

**P2: broaden realism.** Calibrated DC cooling, TESPy thermal maps, IPP risk scenarios and microgrid operating-policy comparison. Add production-aware reliability and mining restart studies against their reviewed semantics. Sequence actual slices by dependency in IMPLEMENTATION-BACKLOG.md, not by installing packages in rank order.

**P3: demand-driven depth.** One dynamics engine, detailed process flowsheets, water treatment, haulage and additional H2 products. Each must improve a named decision and have an input/reference dataset before becoming a default feature.

Release gates: exact-component build contract; dependency/license feasibility; per-adapter/version/platform real-worker reference and conservation evidence; native round-trip and enabled-family E2E; admission into native jobs/permissions/evidence; five-card composition/removal/migration tests; Guided/Expert parity and honest limitations; checked claims admitted or explicitly withheld. External domain review and real-use calibration remain pending until supplied, not implied by passing software tests. ADAPTER-READINESS.md distinguishes plan, build, availability and domain gates.
