# GridSpine: open-source capability research and product brainstorm

Research date: 20 October 2026. Status: research/brainstorm, not an implementation commitment or integration approval.

Historical snapshot: the three-group UX below has been superseded by the owner's five modular analysis cards in [FIVE-CARD-MODULARITY.md](FIVE-CARD-MODULARITY.md). Use the revised [sector plan](SECTOR-DESIGN-PLAN.md), [adapter gates](ADAPTER-READINESS.md) and [independent review](INDEPENDENT-REVIEW.md) for implementation decisions, including newly identified exact-component H2Integrate source risks.

## Recommendation

Preserve the existing PyPSA/pandapower foundation and one guided workflow. The strongest opportunities are better demand evidence, battery lifecycle analysis, calibrated thermal/process detail, procurement-risk analytics and bounded operating-strategy validation. Most can live behind existing screens. Do not introduce another general planning, billing or finance application just because it is open source.

The first product experiments I would choose are:

1. **C&I demand confidence and bill reconciliation**: measured data first, explainable synthetic profiles when unavailable, and a supported independent tariff check.
2. **Battery lifespan and firm backup capability**: quantify the effect of ageing on usable capacity, replacements, cost and resilience, with explicit chemistry/temperature assumptions.
3. **Sector-specific operating realism**: data-center cooling/workload coupling for that segment; heat/process maps for industrial sites; PPA firming and market-exposure scenarios for IPPs.
4. **Deeper control/dynamic validation only where a customer needs it**: reusable adapters and evidence, not a universal controller editor or a live EMS rollout.

These priorities are product judgments based on the source review, not measured commercial ROI.

## What was actually checked

- Web searches and primary project documentation; GitHub metadata, README and license retrieval for **35 unique public repository candidates**.
- Repository tree/engineering signals for 12 shortlisted projects, plus selected package files, tests and interoperability source. Presence of tests/CI is not a claim those tests passed or that a model is validated.
- Fresh `origin/master` of `MLHaoChang/pypsa-eur`, **`c7be9512861914b40aeb31bb6b555421f61b1642`**, read without changing the active worktree. This is the baseline for overlap statements below; later commits may change it.
- No candidate was installed or executed. No integration benchmark, scientific validation, paid model/media call, deployment, reviewer contact, PR or repository code change was performed for this research.

The cached GitHub material and collection scripts are in this directory. `REPOSITORIES.md` is the complete candidate inventory. GitHub's automatically detected license was not treated as authoritative when it returned NOASSERTION or contradicted the actual license file.

## Existing scope: avoid building it twice

Source presence is evidence of implementation, not proof that every path is exposed in the UI, works in every template or passes current full tests.

| Domain | Observed current source | Consequence for additions |
| --- | --- | --- |
| Capacity planning and dispatch | PyPSA/Linopy/HiGHS; staged/myopic and limited-foresight solve paths | Retain the solver. New modules should supply validated inputs, constraints or independent checks. |
| Load flow, electrical and fault screening | pandapower, lightsim2grid, campus electrical, island flow/short-circuit/frequency code | Another balanced power-flow engine adds little. Verify a specific unsupported study before adding one. |
| Grid-code handling | Project code upload, reviewed limit profiles, source/assumption tags and campus checks | An external engine may test more phenomena; it does not automatically establish jurisdiction-wide compliance. |
| Reliability | COPT, storage-aware sequential Monte Carlo, ENS/LOLE metrics, FMEA, ELCC and feedback loops | Enhance source quality/scenario coverage or use an independent reference; do not replace it wholesale. The MC source explicitly limits its weather, outage dependence and network representation. |
| Tariffs/contracts/finance | Rating engine includes demand, tiers/ratchets and explicit unsupported cases; PPA/CfD settlement; project debt/tax/returns/replacements; SAM oracle fixtures | Generic billing and PPA settlement are not missing. Focus on tariff data, reconciliation, portfolio procurement and uncertainty. SAM finance-reference work already exists. |
| Controls | Supported GFM droop/inertia settings search and island/frequency screening | Explain and extend the screening, with separate evidence for more detailed closed-loop models. |
| Data-center load | AI-training utilization profile and sub-hourly IT envelope; temperature-dependent assumed PUE | Do not claim a DC profile must be built from scratch. Richer thermal/cooling physics and calibration are the incremental opportunity. |
| Profiles and performance | atlite, ENTSO-E access, renewable profile shaping, `tsam` time aggregation | These are already dependencies/features. Better exposure or validation can help without another library. |

Representative baseline files, all under the commit above:

- [Dependency pins](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pixi.toml): PyPSA 1.1.2, pandapower 3.1.2, tsam 2.3.9 in the relevant environment; separate environments have separate pins.
- [Tariff engine](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/commercial/tariff_engine.py), [contract settlement](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/commercial/contracts.py), [finance](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/finance/engine.py).
- [Sequential MC and its scope](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/adequacy/mc.py), [asset-health provenance](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/adequacy/asset_health.py).
- [DC profiles/envelope](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/dc_load_profile.py), [time aggregation](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/time_aggregation_service.py).
- [Grid-code workflow](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/campus_grid_code_service.py), [GFM tuning limitations](https://github.com/MLHaoChang/pypsa-eur/blob/c7be9512861914b40aeb31bb6b555421f61b1642/pypsa-gui/backend/services/campus_island_tuning.py).

## Shortlist: useful capabilities rather than more top-level modules

### 1. Better demand evidence and measured savings

**Candidates:** [oemof-demand](https://github.com/oemof/oemof-demand), previously reached through the demandlib repository; [OpenDSM](https://github.com/opendsm/opendsm), formerly OpenEEmeter.

The former supplies heat/power shapes from annual consumption; the latter offers weather-normalized meter baselines and savings analysis. Their roles differ: synthetic design inputs versus statistical evidence on existing sites. Best fit is C&I. Put them behind load import/profile settings and an optional baseline report. Keep synthetic, measured and fitted data distinct; reject insufficient data. A statistical baseline is not a physical resilience model. Current declared licenses are MIT and Apache-2.0 respectively. Confirm API/name migration before adopting a release.

**First experiment:** one existing C&I site with meter/weather data and a known bill. Compare native imported data with a fitted baseline; separately test annual-MWh normalization and peak/seasonality error on synthetic profiles. Do not force both annual energy and peak to match with a single scale factor.

### 2. Battery lifespan, replacement and aged backup capacity

**Candidate:** [BLAST-Lite](https://github.com/NatLabRockies/BLAST-Lite).

Its empirical cell-ageing models could connect dispatch, temperature and SOC history to a lifecycle scenario. This benefits all three groups. Integrate as a bounded post-processing worker, then feed an explicitly reviewed degradation/replacement assumption into existing finance and resilience studies. A cell model is not an automatic pack warranty or usable-power guarantee. Chemistry and operating-range validity matter. The reviewed tree lacks a conventional Python test suite/CI workflow, so integration needs its own numerical checks. The license file is BSD-3-Clause; packaging contains inconsistent license metadata that should be clarified.

**First experiment:** compare two dispatch policies on the same chronological battery trajectory and weather basis. Show energy fade assumptions, replacement-cost sensitivity and resulting critical-load autonomy separately. Do not infer loss of firm MW from energy fade alone; power/resistance/thermal limits require separate evidence.

### 3. Data-center cooling, workload and water sensitivity

**Candidates:** [SustainCluster](https://github.com/HewlettPackard/sustain-cluster), including its [SustainDC model](https://github.com/HewlettPackard/sustain-cluster/blob/main/envs/sustaindc/README.md), and the earlier [dc-rl](https://github.com/HewlettPackard/dc-rl).

The models provide a reference for IT/thermal/cooling coupling and workload scheduling. Extract a calibrated reduced model or bounded simulation adapter, rather than installing a reinforcement-learning training platform into GridSpine. The incremental value is richer site behavior beyond its existing assumed PUE/envelope. Both repositories have MIT licenses. Available tests do not establish accuracy for a customer's cooling plant. Preserve critical IT versus deferrable training and avoid counting cooling twice inside PUE. These timescales do not replace sub-second electrical envelopes.

**First experiment:** one campus under a heatwave: compare the current PUE approximation with a calibrated cooling model; report peak facility demand, curtailment exposure, energy cost and water assumptions. Never assume every training job can shift or every cooling load can be shed.

### 4. Industrial heat and process realism

**Candidate:** [TESPy](https://github.com/oemof/tespy), MIT. More advanced alternative: [IDAES](https://github.com/IDAES/idaes-pse), with its reviewed BSD-style license and additional notices.

Thermal component models could generate efficiency/operating envelopes for heat pumps, chillers, boilers or CHP, then pass validated curves to native planning. This is particularly useful for C&I and energy hubs. Keep a small curated component catalogue; avoid a generic flowsheet editor in guided mode. IDAES is a later option for a process-specific need because nonlinear process coupling and solver dependencies increase integration effort. Neither name guarantees a suitable mining or ammonia model.

**First experiment:** compare a site's fixed-COP assumption with a temperature/part-load performance map. Check heat/electricity balances and interpolation bounds before using it in a sizing optimization. Preserve fixed process-unit sizes where native adapters do not support optimizing them.

### 5. Procurement, PPA shape and merchant-risk analytics

**Candidates:** [pypsa-ppa](https://github.com/open-energy-transition/pypsa-ppa) as a method/reference example; [gridstatus](https://github.com/gridstatus/gridstatus) for relevant US market data. Existing ENTSO-E/atlite pipelines should be reused elsewhere.

The product opportunity is portfolio shaping, residual purchases, negative-price exposure, capture price and firm-delivery scenarios using existing settlement/finance. It is not another generic PPA calculator. The MIT-licensed PPA repository explicitly describes itself as proof of concept; study selected formulations and tests rather than embed its application or finance engine. Gridstatus is BSD-3-Clause, but data access/redistribution terms are separate. Historical prices are not forecasts. Hourly matching needs a declared accounting boundary; it does not establish a green-certification claim.

**First experiment:** compare a few contract/portfolio alternatives using the same load, weather and price scenarios and native settlement. Report uncovered exposure and uncertainty; do not label a shortfall penalty as physically served load or count the same battery/reserve capacity twice.

### 6. Tariff and bill assurance

**Candidate:** [PySAM](https://github.com/NatLabRockies/pysam), especially [Utilityrate5](https://nlr-pysam.readthedocs.io/en/stable/modules/Utilityrate5.html), plus curated tariff-source adapters.

This is an independent comparison/import opportunity, not a billing-engine replacement. Start with a declared overlap subset, matching local time, settlement intervals, demand peaks, netting, taxes and currency. Native tariff logic already contains substantial coverage; native finance already has SAM reference fixtures. PySAM/SSC licenses are BSD-3-Clause with notices. US URDB support does not provide worldwide tariffs. Reject unsupported terms instead of reporting a partial bill as a complete total.

**First experiment:** a supported C&I bill with known energy/demand charges; reconcile native rating, an external calculation and the actual bill. Use full chronology for peak-demand testing; representative periods cannot silently replace monthly maxima.

### 7. Control-strategy comparison

**Candidates:** [python-control](https://github.com/python-control/python-control), BSD-3-Clause, for feedback analysis; [do-mpc](https://github.com/do-mpc/do-mpc), LGPL-3.0, for model-predictive-control experiments.

Start with small, explicit plant/controller models and compare a baseline with a constrained operating policy. The result should be a study recommendation, not plant actuation. python-control is a lighter first addition; do-mpc requires state estimation, constraints and a calibrated model. The latter also brings a separate license/dependency review. A good step response or simulation cost does not establish grid-code compliance. Optional compiled numerical backends need platform checks.

**First experiment:** compare supported operating policies on held-out disturbances/weather/forecast errors, with matched constraints and initial SOC. Measure constraint violations as well as cost. Reuse native GFM screening where adequate; do not present perfect-foresight dispatch as an implementable online controller.

### 8. Deeper electrical/dynamic validation, conditional on need

**Candidates:** [ANDES](https://github.com/CURENT/andes), [OpenIPSL](https://github.com/OpenIPSL/OpenIPSL), [FMPy](https://github.com/CATIA-Systems/FMPy), and [OpenDSSDirect.py](https://github.com/dss-extensions/OpenDSSDirect.py).

These solve different problems. ANDES supports power-system time-domain/small-signal studies; OpenIPSL supplies Modelica components; FMPy runs supplied FMI models; OpenDSS is relevant to selected detailed distribution studies. They should be optional adapters selected by study/data needs, not four new default engines. Static network conversion does not create valid dynamic/control parameters. The ANDES [pandapower interface](https://docs.andes.app/en/latest/_modules/andes/interop/pandapower.html) is not a universal dynamic round-trip. RMS/phasor simulation cannot be advertised as EMT verification.

**First experiment:** one supported saved operating point, one documented disturbance and a reference result. Require initialization convergence, exact asset/source identity, model/parameter provenance, timestep sensitivity and explicit assessed phenomena/clauses. Retain current native electrical checks where they already answer the question.

## Reliability and planning speed: reuse before replacing

[PRAS](https://github.com/NatLabRockies/PRAS) is a useful bulk-system adequacy/capacity-credit reference for large IPP portfolios. Its inspected Julia packages have MIT licenses, despite absent root license detection. It would add a Julia runtime and new system-data mapping; it is not a replacement for site UPS, protection or dynamic continuity studies. Favor an offline independent benchmark first. Shared assumptions can still produce shared errors.

[REopt.jl](https://github.com/NatLabRockies/REopt.jl) is Apache-2.0 and overlaps site capacity/dispatch/resilience planning. Use a small common case as an independent reference if worthwhile; embedding its full optimization alongside PyPSA would multiply ownership and model semantics.

[tsam](https://github.com/FZJ-IEK3-VSA/tsam) is already pinned and wrapped by the native GUI. Improve the existing fast-preview versus detailed-validation path rather than call it a new module. Preserve extreme events and storage chronology; a clustered economic solve does not certify adequacy or monthly bill maxima. Benchmark approximation error before exposing a simple speed/accuracy choice.

For multi-weather-year or correlated-outage risk, first extend scenario inputs and the native adequacy methods. The source identifies those limits. A new package cannot conjure reliable outage statistics or a calibrated weather ensemble.

## User-group priorities

| User | Highest-value incremental capabilities | Useful later | Avoid making default |
| --- | --- | --- | --- |
| Commercial & industrial | Meter/profile confidence, tariff/bill reconciliation, battery life; heat/cooling where material | Savings measurement, process-specific efficiency maps, constrained operating policies | Research RL, generic flowsheets, a second planning/finance app |
| Data center | Calibrated workload/cooling model, aged UPS/BESS capability, matched outage/ramp/dynamic evidence | Water/heat reuse, explicit workload flexibility, selected dynamic validation | Assuming IT is fully flexible, using economic adequacy as an uptime certificate |
| Large IPP / hybrid developer | PPA/merchant exposure, resource/weather scenarios, battery lifecycle, evidence-backed connection/dynamic checks | Portfolio adequacy reference and explicit market-service scenarios | Treating historical arbitrage as a revenue forecast or modelled capacity credit as accreditation |

The priority differs by project: a process-heavy factory may benefit more from a thermal map than a PPA comparison; a pure merchant battery needs market-service constraints and degradation before office-load shapes.

## Keep the user experience simple

Retain the three agreed design groups:

1. **Energy & system planning:** demand confidence, optional cooling/process detail, battery lifecycle and commercial assumptions within existing relevant steps.
2. **Grid & electrical design:** reuse native checks, disclose limitations, and offer deeper studies only when the model/data supports them.
3. **Controls & dynamic validation:** compare supported strategies and expose optional validated simulator adapters.

Use user-facing choices such as “Include battery ageing” or “Use measured demand,” not package names. Advanced settings reveal provenance, calibration and model versions. A result card should explain how the optional analysis changes the decision; if it cannot do so, defer the feature.

A proposed assistant action can help find missing inputs or run an admitted study. It must use typed native adapters, the existing permissions/approvals/locks and exact evidence/effect receipts. Do not add a generic unrestricted code-execution tool to invoke arbitrary libraries. Measured, assumed, fitted, unavailable and stale remain distinct states.

## Integration constraints found during source checks

- **Incompatible numerical stacks:** reviewed BLAST-Lite [packaging](https://github.com/NatLabRockies/BLAST-Lite/blob/main/pyproject.toml) requires NumPy <2; reviewed OpenDSM [packaging](https://github.com/opendsm/opendsm/blob/master/pyproject.toml) requires NumPy >=2. These declarations cannot coexist in one environment. Prefer optional versioned workers and small input/output contracts. Do not change native pins merely to make a research package install.
- **Licenses are not uniform:** ANDES is GPL-3.0-or-later; do-mpc LGPL-3.0; RAMP EUPL-1.2; OpenIPSL BSD-3-Clause; FMPy core BSD-2-Clause with separate material notices. Distribution architecture and dependencies need release-specific review. A process boundary is not a blanket licensing exemption.
- **Custom binding terms:** the inspected OpenDSSDirect.py [license](https://github.com/dss-extensions/OpenDSSDirect.py/blob/master/LICENSE) has specific conditions beyond a generic BSD label. Review the selected binding, engine and binary dependencies separately.
- **Do not confuse model execution with a data file:** an FMU can contain executable native code. Any future FMU path needs approved models, isolation and bounded execution; the runner does not supply a validated plant model by itself.
- **Native version gap:** current upstream releases differ from the native pins. Recommendations describe capabilities to evaluate, not permission to upgrade PyPSA, pandapower or tsam indiscriminately. Re-run relevant scientific oracles after any tested adapter/version change.
- **Data is separately governed:** weather, market, meter, manufacturer and trace datasets have their own coverage and usage conditions. A permissive code license does not guarantee free production data or lawful redistribution.

## Things to defer

- Full alternative optimization stacks where PyPSA already fits.
- General RL control/training as a product feature; calibrated deterministic baselines and constrained simulations come first.
- [HELICS](https://github.com/GMLC-TDC/HELICS) federation until more than one concurrent simulator actually needs synchronization. It is orchestration infrastructure, not a missing engineering model.
- [PyBaMM](https://github.com/pybamm-team/PyBaMM) as a default site-planning dependency. Detailed electrochemistry is a later requirement-driven study.
- [IDAES](https://github.com/IDAES/idaes-pse) as a universal industrial flowsheet editor.
- Live plant/market dispatch, SCADA and [OpenLEADR](https://github.com/OpenLEADR/openleadr-python) integration during this planning scope. Protocol support does not create an approved operational controller.
- Universal compliance certification. [SunSpec SVP](https://github.com/sunspec/svp) and [IEEE 1547.1 scripts](https://github.com/sandialabs/svp_1547.1) are relevant lab/test references, but require compatible equipment/models and applicable procedures; they are not a drop-in global compliance engine.

## Practical next experiments, if selected later

Use one bounded case per customer segment, not an immediate platform rewrite:

- **C&I:** real interval meter + weather + tariff + known bill; establish baseline/error and a defensible PV/BESS/heat decision.
- **Data center:** the existing weak-grid campus; add a calibrated cooling/ageing sensitivity, keeping critical IT, flexible training, UPS and electrical timescales separate.
- **IPP:** one wind/PV/BESS portfolio and a few procurement/delivery scenarios; reconcile native settlement, weather/price uncertainty and aged storage capability before adding market-service revenue.

Accept an addition only when it improves an identified decision, has enough data, passes a documented reference comparison, has a manageable dependency/license path, and fits an existing step. Effort estimates and benefit rankings here are qualitative. No integration or domain validation is established by this brainstorm.
