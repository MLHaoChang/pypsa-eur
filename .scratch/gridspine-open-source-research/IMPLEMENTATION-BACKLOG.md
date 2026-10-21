# Prioritized integration backlog

Status: proposed slices, all unstarted in this research. One row may require multiple reviewable PRs. Relative effort S/M/L is qualitative and depends on the current native implementation; it is not a staffing or delivery-date estimate.

## Reconcile before building

Revised baseline `90c7b8ffcec104397383361212da0aa21f545f1b`. Consult current AGENTS.md, steward skill and harness README when implementation begins. Read FIVE-CARD-MODULARITY.md and ADAPTER-READINESS.md first. Reconcile live PRs and these existing designs:

- `.scratch/grid-to-process/`: ticket04 results,06 counterfactual,07 editor,08 criticality,09 chronology,10 concentrator,11 mining economics,12 restart,13 grid-to-tonnes,14 assistant.
- `.scratch/project-workflow/`: library, commercial-finance, integration/sensitivities and workspace reconciliation. Use its existing library store; do not create a sector-specific replacement.
- `.scratch/checked-results/audit.md`: S1–S10 dispositions and calculation profiles. Extend coverage, not the raw checked-tool allowlist.
- `.scratch/ipp-renewables-bess/spec.md` and tickets01–07 (PR453, docs only): reuse its actual gaps and post-solve seller-PPA semantics; controls/grid initially off. Reconcile older Hand-off grouping with the latest five-card instruction, not a blind enum rename.
- Existing workspace/companion PRs and implementation status must be checked; do not repeat source preparation or merge anything for this research task.

## Delivery slices

| ID / priority | Deliverable and scope | Dependencies | Done when | Effort |
| --- | --- | --- | --- | --- |
| F0 / P0 | Current-source map and overlap reconciliation | None | Every proposed change tagged already implemented, existing active slice, or genuinely new; reference source/PR and owner semantics recorded | S |
| F1 / P0 | Server-authoritative five-card/feature registry and shared adapter schemas | F0; current library, capability/result and project schemas | Stable IDs/versions, all/any provider requirements/exclusions, tool/job/metric references and five separate state dimensions; one existing example round-trips without result changes; no dynamic code authority | M |
| F2 / P0 | Optional worker admission using native jobs | F1; existing job/effect contracts | Pinned worker accepts staged data only; bounded runtime/memory/artifacts; cancel/failure/stale paths and license inventory demonstrated | M |
| F3 / P0 | Assurance/evidence dispositions for adapter outputs | F1; checked-results S1 | Every new metric explicitly checked by an approved profile or withheld; corruption/wrong scope cannot certify a claim | M |
| F4 / P0 | Reconcile chronological staged process support | F0; grid-to-process09 | Native prescribed hourly-year gate measured on named runner; inventory/UC/year assumptions valid or named refusal; current week cases regress cleanly | L |
| F5 / P0 | All-port product results and counterfactual bases | F0; grid-to-process04/06 | Electricity/H2/NH3/material outputs have correct signs/units and conserved totals; grey buy-in and shortfall remain distinct; finance waits for its own checked profile | M |
| F6 / P0 | Dependency resolver and standalone input-provider admission | F1 | All32 card selections resolve or refuse precisely; imported/fixed/saved context works without Energy; no hidden job/selection; ambiguous providers and cycles refused | M |
| F7 / P0 | Preview/apply selection lifecycle and invalidation | F6,F3 | Atomic reviewed diffs preserve physical constraints/objective/context/history; remove/re-add, dependent impact, in-flight jobs, stale revision and idempotency tests pass | M–L |
| F8 / P0 | Exact-adapter build and availability register | F1,F2,F3 | Every candidate has exact component/scope/reference/tolerance/license/runtime contract or stays feasibility-only; each shipped version/platform has real worker + reference + native round-trip evidence | M |
| F9 / P0 | Legacy grouping, registry-version and missing-module migration | F6,F7 | Older three-group/five-group records mapped explicitly; Hand-off not mislabelled Reliability; unknown features retained unavailable; rollback/pins/constraints and unrelated valid context preserved | M |
| C1 / P1 | Native C&I meter and bill reference | F1,F3 | Known bill reconciles on supported terms; gaps/DST/monthly peaks explicit; useful even without new dependencies | M |
| D1 / P1 | oemof-demand synthetic profile adapter | F1,F3,C1 | Versioned archetype with source and annual-energy normalization; peak remains independently reported, not falsely fitted | S–M |
| D2 / P1 | OpenDSM measured baseline worker | F2,F3,C1 | Data-sufficiency gate, held-out error and failure cases demonstrated; output remains fitted, not measured or physical | M |
| B1 / P1 | BLAST-Lite frozen-dispatch ageing evaluator | F2,F3,F8 | One supported chemistry/range benchmarked; physical calendar/duty repetition and replacement resets explicit, not optimization weights; units/SOC/temp trace; no universal pack/power claim | M |
| B2 / P1 | Reviewed lifecycle feedback into finance/reliability | B1; native finance/adequacy semantics | Replacement and usable-energy assumptions applied once through reviewed diff; rerun source hashes change; power capability remains separately supported | M |
| H1 / P1 | H2Integrate feasibility, then one exactly admitted H2 evaluator | F2,F3,F8 | Reproducible pinned environment; zero/partial input, water-unit and controller conservation concerns reproduced and fixed/versioned or excluded; fixed-capacity reference; no alternate finance imported | M |
| H2 / P1 | Bounded H2 planning envelope and replay | H1,F1; chronology appropriate to case | Native dispatch stays in admitted bounds; replay mismatches surfaced; mass/energy/water and LHV basis verified | M–L |
| A1 / P1 | Extend merged ammonia boundary | H1,F5,F8; process editor where required | Explicit N2/ASU or clearly excluded scope; simple upstream ammonia not used as chronological feedstock oracle; selected synloop reference validated; compressor/startup losses not duplicated | M |
| R1 / P1 | Production-aware reliability and method evidence | F4,F5; existing ticket08 | Outage/inventory/restart replay gives lost product; method/weather/dependence/seed/sample/stopping/CI basis and reconstruction evidence retained or refused; missing valuation null | L |
| M1 / P1 | Native concentrator example | F4,R1; existing ticket10 | Prescribed concentrator/stockpile/21-day shipment/connection example passes native reference; no new mining engine required | M |
| U1 / P1 | Five modular analysis cards, one example card per family, Guided/Expert parity | F1,F6,F7,F9; existing workspace slices | Eight preset feature maps; IPP controls off; microgrid GFM explicit eligible opt-in; single/staged and Select all applicable respect model/eligibility; accessible add/remove UI | M |
| T1 / P2 | TESPy thermal performance map | F2,F3 | One heat-pump/chiller or CHP map validated against known operating points, bounded interpolation and energy balance | M |
| DC1 / P2 | HPE-derived calibrated cooling evaluator | F2,F3; native DC profile/envelope | PUE boundary documented; comparison on heatwave; critical/flexible IT and thermal constraints preserved; no RL runtime required | M–L |
| DC2 / P2 | Data-center integrated example | DC1,B2; existing staged/native DC paths | Weak-grid growth + cooling + aged UPS sensitivity shares consistent scenarios and defensible evidence; no uptime certification claim | M |
| I1 / P2 | Reconcile PR453 then extend IPP scenarios using native contracts/finance | F1,F3,B2 as applicable; existing IPP tickets | Same-basis cases/revenues; price/circulation guard intact; seller-PPA post-dispatch limitation explicit; no duplicate template or hidden control study; pypsa-ppa reference only | M |
| I2 / P2 | Optional supported market-data acquisition | I1; confined data-access implementation | ENTSO-E reused; US gridstatus only when needed; source/terms/timezone retained; offline fixtures; no unbounded fetching | M |
| Q1 / P2 | Extend tariff oracle coverage | C1; existing PySAM/SSC fixtures | Native and external supported bill subset reconcile including boundaries; unsupported terms visibly excluded from total claims | S–M |
| O1 / P2 | Microgrid rule-based/feedback policy comparison | F3; native island paths; R1 when production present | Same disturbances/initial SOC/fuel; nonanticipative decisions; cost and violations compared to baseline | M |
| O2 / P2 | python-control adapter for one admitted plant | O1,F2 | Plant/model provenance, initialization and held-out disturbances verified; strategy output is study-only | M |
| M2 / P2 | Mining counterfactual and restart/pickup coupling | M1; existing tickets11–13; grid review | Throughput/voltage/restart references; losses replayed without downstream double-count; static screen distinct from dynamic validation | L |
| A2 / P2 | Ammonia operational/reliability refinement | A1,R1; validated startup/thermal inputs | Feedstock/start/standby/heat boundary explicit; startup replay matched; different native/external assumptions remain visible | M–L |
| P1 / P3 | One additional H2-to-product recipe | H2,A1,F5; actual feedstock/model data | Choose methanol OR DRI; complete ports, feedstocks, cost and emissions boundary; valid product-specific comparison | L |
| W1 / P3 | WaterTAP treatment/desalination envelope | H2 or DC1; site water need | Selected water quality/recovery/energy/cost reference passes; no generic water-network claim | L |
| O3 / P3 | do-mpc strategy for a calibrated plant | O1/O2; measured state/forecast model | Constraints and estimation defined; held-out forecast-error replay beats/advisably differs from baseline; infeasibility handled | L |
| G1 / P3 | Choose and validate one dynamics adapter | F2,F3; actual unsupported phenomenon and dynamic data | ANDES OR approved OpenIPSL/FMPy path; model mapping/init/timestep/reference validated; precise assessed phenomena | L |
| G2 / P3 | Detailed distribution adapter | G1-style admission; proven native gap | OpenDSS binding/engine/binary terms reviewed; one unbalance/QSTS case mapped and checked; default native solver unchanged | L |
| V1 / P3 | Independent bulk-adequacy benchmark | F3; native method/scenario specification | PRAS and native matched subset cross-checked; temporal/weather/outage differences explained; no campus continuity claim | M–L |
| M3 / P3 | Haulage-to-energy demand adapter | M1; measured routes/duty/charging data | OpenMines or simpler duty model evaluated; transport output converted via validated energy model; no automatic whole-mine optimizer | L |

## Suggested release order

1. F0–F3,F6–F9 plus C1: prove five-card standalone/composition/lifecycle and one trusted end-to-end current-engine outcome. Run bounded H1/B1 feasibility before selecting versions for product support; unresolved source findings are explicit admission gates.
2. D1/D2 and B1/B2; F4/F5 and H1/H2/A1. This expands demand, lifecycle and molecular fidelity on shared foundations. F4 and R1 are prerequisites for the agreed mining tracer, not generic prerequisites for every C&I case.
3. R1/M1/U1: expose the native mining example and consistent planning selection after their actual gates. Keep the current ammonia example available while extensions mature.
4. T1/DC1/DC2/I1/Q1/O1 and selected I2/O2/M2/A2: choose useful sector outcomes, not a simultaneous install of all packages.
5. P3 depth only after data and specific demand justify it.

## Prioritized external-module summary

Rank is recommended product value/reuse, not an instruction to install immediately. Worker/schema feasibility precedes runtime adoption.

| Rank | Candidate | Adoption mode | Why / initial scope |
| --- | --- | --- | --- |
| 1 | H2Integrate | First **feasibility** candidate, exact component admission required | Shared H2/ammonia/product potential; simple ammonia is annual-reference only; PEM water/controller concerns must be resolved or excluded; native planning/finance retained |
| 2 | BLAST-Lite | Isolated chronological postprocessor | Lifecycle/replacement/aged-energy sensitivity across storage-heavy sectors |
| 3 | TESPy | Optional map-generation worker | Reusable industrial thermal/cooling/process performance |
| 4 | oemof-demand | Small versioned profile adapter | Explainable greenfield demand archetypes, limited initial integration |
| 5 | OpenDSM | Isolated fitting worker | Brownfield meter/weather confidence and later M&V; requires suitable real data |
| 6 | PySAM/SSC utility-rate reference | Extend existing oracle use, not another default engine | Supported tariff assurance; existing SAM finance fixtures reused |
| 7 | HPE SustainDC/SustainCluster selected physics | Bounded calibrated evaluator or derived reduced model | DC-specific cooling/workload/water realism; no research RL platform |
| 8 | python-control | One explicit offline plant-analysis adapter | Lightweight first control-strategy depth |
| 9 | Regional market-data adapter such as gridstatus | Optional geography-specific data ingestion | Only for missing applicable data; reuse existing ENTSO-E elsewhere |
| 10 | One of ANDES or OpenIPSL/FMPy | Conditional detailed-study worker | Actual dynamic-model/data need required; do not install competing dynamics stacks |

Conditional later: do-mpc, WaterTAP, IDAES, PRAS, OpenDSSDirect.py and OpenMines. HOPP and pypsa-ppa are reference/benchmark candidates first. No new external package is needed just to build the agreed native concentrator template or improve native tariff/PPA UI.

## Merge and release expectations for future implementation

Follow current repository rules and actual applicable CI, plus the acceptance matrix. Each adapter change needs deterministic fixtures and a genuine numerical reference; a mocked package smoke test is not scientific validation. Every shipped adapter/version/platform must execute its real worker and native round-trip; every newly enabled family needs its actual enabled-path E2E. Record exact source/worker versions and evidence without summing overlapping counts. Do not weaken checks when an optional dependency cannot run: withhold that capability and report why. Paid live-model evaluation remains manual, never a merge gate.

This task authorizes design/research artifacts, not a new integration, external review, deployment or automatic merge. Any implementation session must use its actual user authorization and fresh repository instructions.
