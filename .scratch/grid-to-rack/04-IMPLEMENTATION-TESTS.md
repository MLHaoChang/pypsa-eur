# Implementation sequence and validation matrix

Scope: a proposed extension to the existing data-centre example, not a new standalone design platform. No runtime implementation in this PR. The earlier readiness spec excluded in-app EMT; the proposed detailed-engine increment must pass current design/engine/licensing review before implementation. Do not reopen parked ANDES or activate equipment as a side effect.

The [workflow-fit decision](05-WORKFLOW-FIT-DECISION.md) and [integrated journey](06-INTEGRATED-JOURNEY.md) supersede the initial tool shortlist. GTR-03/04 are deferred research/specialist scopes, not automatic next integrations. The first native release preserves existing input ownership, run-readiness and results; it needs no external engine.

## Bounded work packages

| ID | Deliverable | Reuse / likely seams | Acceptance / dependency |
| --- | --- | --- | --- |
| GTR-00 | Review model boundaries and engine requirements; agree measurement planes, output labels and trace/state contract | Existing DC envelope, island schema, study artifacts, Library pins, current workflow and approval controls | No runtime change. Resolve which quantities each fidelity level can compute and which source/model parameters are missing. |
| GTR-01 | Reuse the existing assumed envelope and add optional bounded event-trace import; expose detailed cohorts only when needed | `DataCentreLoadSpec`, `dc_load_profile.py`, existing series import/source metadata and example creation | GTR-00; preserve default template behavior, trace sample/averaging/synchronization evidence, no invented higher-frequency measurement. |
| GTR-02 | Control Strategy rapid-response assessment: energy-conserving buffer/PoC analytical screen and frequency-only replay where useful | Native callable `frequency.simulate` event seam and simplified UPS/asset state interfaces | GTR-01; compare no mitigation versus explicitly limited buffer; show saturation, residual, reserve and recharge. Voltage/harmonics remain not established. |
| GTR-03 | Deferred specialist electrical-engine evaluation for a demonstrated question/requirement; no default engine selected | New narrow adapter using native jobs/artifact conventions; no embedding in solver/assistant loop | GTR-00/01 and scope/licensing review. One explicit grid–UPS/DC-link–load chain initialized at a consistent operating point. Pass physical balance, limits and convergence benchmarks before expanding. |
| GTR-04 | Deferred workload/grid/control research for an explicit computing-performance question | OpenG2G remains a research reference; no mandatory second topology/coordinator | GTR-01 and validated electrical/screening contracts. Demonstrate control delay, performance constraints and energy-consistent exchange; no gated download/model API calls. |
| GTR-05 | Dispatch-linked reserve/aging and planning feedback | Existing investment periods, adequacy/stress studies and heuristic island-frequency cut/apply path | GTR-02 and appropriate GTR-03/04 evidence. Feedback has source, applicability and heuristic/exact status; rerun to validate after changes. |
| GTR-06 | Guided/Expert Control Strategy result, linked Grid/Reliability summaries and assistant orchestration | Existing workflow sections, result provenance and bounded harness jobs/approvals | Expose only supported metrics; exact input/engine/state pins and stale behavior. No new live equipment control authority. |

The first useful release is GTR-00/01/02 plus a narrow GTR-06 view: **Control Strategy → Response to rapid load changes** is the single assessment/primary result home. Grid and Reliability link to that same receipt per variant. Preserve the owning input forms and Scenarios request; add no mandatory flow section. Grid-coupled studies require compatible operating-point evidence; a standalone analytical screen explicitly declares its baseline and exclusions. Include trace, planning linkage, active-power buffering/reserve and unknown electrical metrics. A short event proves neither annual LOLE/EUE nor voltage compliance; only supported, enforced and verified feedback affects a capacity optimum. GTR-03 is a separate feasibility spike, not a prerequisite to ship an honestly labelled screen. If the engine/model spike fails, retain screening and the existing external handoff route; do not silently lower the requested detailed-study fidelity.

Cooling-model refinement, cell-level aging calibration, commercial HIL/PHIL, switching-level vendor certification and full digital-twin visualization are later optional work. Avoid a second planner, two default co-simulation orchestrators or a large 3D UI before the physical model/data contract is sound.

## First example and scenarios

Use the existing weak-connection data-centre example, not a new duplicate single/staged template. Let users select phase dates and the planning horizon. Preserve the original synthetic load and existing default behavior unless the new scope is explicitly selected.

Start with one source-tagged training workload and explicit correlation/measurement-plane assumptions, fixed comparable growth/topology/dispatch inputs, and:

- A: no additional fast smoothing.
- B: limited local UPS/BBU/DC-link or campus storage smoothing; topology and response capability explicit.
- C (deferred): coordinated workload/storage control only after performance evidence and fit gates pass; its penalty/limits must be reported.

Compare **actual achieved** PoC response, required/available power and energy, reserve state, losses/costs and useful-work outcomes. A target filter is not the achieved result. Include increasing and decreasing load, repeated oscillations/checkpoint events, grid-loss/weak-grid conditions and low initial SOC. Add phase unbalance and converter faults only under a suitable detailed model.

Use explicit finite caps on number of cases, event horizon, solver step budget, output bytes and wall time, plus cancellation/terminal receipts. Smaller timesteps are a convergence question, not a loophole around resource limits. No recursive automatic replan/engine runs without the ordinary approved job scope. Start with offline artifacts rather than live GPU or site endpoints.

## Test matrix

| ID | Scenario | Required evidence / outcome |
| --- | --- | --- |
| C01 | Unit/sign and plane conversion: W/kW/MW, seconds/hours, RMS/peak, 50/60Hz, GPU versus rack versus PoC | Exact declared conversion or refusal; independent arithmetic and energy totals. A plane cannot be renamed without a physical mapping. |
| C02 | Averaged/low-bandwidth trace resampled to 1 ms | Still labelled averaged/synthetic interpolation; refuse claims of measured 1 ms ramps or harmonics. |
| C03 | Missing/duplicate/out-of-order timestamps, clock skew and nonfinite values | Bounded parser, explicit gap/quality handling; no silent smoothing/zero-fill. |
| C04 | Trace/cohort replay with same seed/config; same data with different correlation | Reproducible exact case identities; distinguish synchronized from staggered cohorts and preserve aggregation assumptions. |
| C05 | Engine restart/cancel, resource exhaustion, unavailable model, failed initialization or nonconvergence | Durable failed/cancelled/unknown result, bounded resources and no partial “pass”; no paid or live fallback. |
| C06 | Co-simulation rate/delay/interpolation changes | Check power/energy balance and timestep/communication-step convergence; reject unsupported rate relationships or unstable coupling. |
| C07 | Changed topology, control, SOC, workload, engine or source semantics | New input digest and stale prior result; raw traces and derived evidence reference the exact original case. |
| W01 | All cohorts fully synchronized versus staggered/independent scenarios | Aggregate step/spectrum reflects specified phase relation; no universal diversity factor or arbitrary square-root scaling. |
| W02 | Training compute/communication/checkpoint/restart and mixed inference | Preserve event definitions and source labels; no inference benchmark presented as measured training data. |
| W03 | Workload power cap/concurrency control with delay and throughput/deadline/quality bounds | Electrical improvement traded against explicit service penalty or infeasibility; no free instantaneous curtailment. |
| W04 | GPU telemetry versus rack/UPS meter; instant/average API fallback | Correct plane/bandwidth/API metadata; no repeated averaged sample called a new high-speed measurement. |
| W05 | Annual mean/PUE and fast-window explicit losses | No loss double count; thermal/cooling response not instantaneous annual-PUE scaling. Reconcile integrated means within declared scope. |
| P01 | Ideal 20 MW ×100 ms pulse | Independent ideal exchange is 0.0005556MWh ≈0.56kWh; correct power rating remains 20 MW. Label this a lower bound, not equipment sizing. |
| P02 | Buffer target feasible versus power/current/SOC saturation | Simulate achieved response and residual PoC disturbance; no energy creation by a filter. |
| P03 | Equal positive/negative IT steps; charger/export limit and repeated dips | Correct charge/discharge signs, DC over/undervoltage where modeled, recharge/recovery and no fake zero net degradation. |
| P04 | Finite DC-link capacitance, constant-power load and voltage-sensitive/trip behavior | Energy/voltage balance and valid load characteristic; below-voltage behavior and restart assumptions explicit. |
| P05 | UPS transfer, bypass, input loss, walk-in and post-outage recharge | Correct energy source, isolated/connected topology and constrained recovery. Upstream rectifier not assumed GFM because output inverter is. |
| P06 | BESS simultaneous active smoothing and reactive support under voltage sag | Respect joint P/Q/current and voltage-dependent capability; no independent full rating assigned to every service. |
| P07 | Grid strength/X:R, PLL/control parameters, delay and limit anti-windup | Detailed model captures or explicitly excludes interactions; parameter sensitivity and suitable benchmark, no one-bus screen substituted. |
| P08 | Island step and GFM/FFR/governor response | Cross-check native aggregate frequency where applicable, correct 50/60Hz and initial headroom; distinguish native screen from multi-bus result. |
| P09 | Phase asymmetry/fault/ride-through, harmonic/DC ripple/flicker request | Suitable waveforms/model/metric required; otherwise not established. A smooth P trace is not a power-quality certificate. |
| P10 | Detailed solver timestep halved and tolerances tightened | Key energy/voltage/current/frequency metrics converge within documented source/engineering tolerances; no unexplained rounding away of violations. |
| L01 | Same battery allocated to smoothing, backup, island reserve and arbitrage | Joint MW/MWh/MVA and priority constraints; no duplicate reservation/revenue credit. |
| L02 | Fast case starts at planning dispatch's low SOC/aged SoH, not full battery | Exact state transfer and credible capacity; depleted reserve can produce an infeasible/failed case. |
| L03 | Growth/energization/hardware density across selected 20-year horizon | Consistent period/vintage mapping and explicit demand/weather scenarios; no extrapolation presented as forecast certainty. |
| L04 | Representative planning snapshots, stress chronology and event-day SOC | Correct time weights, no reset that invents stored energy; compare against a chronological small oracle case. |
| L05 | Aging/replacement/augmentation under repeated fast service | Declared degradation method and costs, no duplicate calendar/cycle effects or unverified failure-rate substitution. |
| L06 | Local/heuristic capability cut applied after topology/controller changes | Reject stale/out-of-domain feedback; revalidate candidate plan, no global-feasibility/optimality claim. |
| L07 | Large storage MWh but insufficient fast MW; adequate annual MWh but unavailable firm MW | Distinct infeasibility causes and corrective choices; planning adequacy and fast service both checked. |
| U01 | Guided/Expert selection of the same example/case and rapid-response entry from Control Strategy/Grid/Reliability | Same assets, assumptions, study request/job and canonical result per variant; secondary views open the Control Strategy detail without duplicate execution. No duplicate hidden topology. |
| U02 | Compare A/B/C with different initial state or workload evidence | Refuse incomparable claim or disclose exact differences; no misleading “improvement” from easier inputs. |
| U03 | Chat/report asks for rack ride-through, compliance, annual LOLE/EUE or optimized reserve using only a short screen | Evidence-bounded answer; no invented voltage/harmonics/compliance, annual reliability or unenforced planning effects. Grid before-mitigation verdict stays separate from linked achieved response. |
| U04 | Private measured workload, untrusted archive/model binary or cross-tenant reference | Access checks, bounded safe import, reviewed executable/model whitelist and no arbitrary execution/secret exposure. |
| U05 | Study/plan/operating-point edit, cancelled worker, partial export or stale result | Exact compatible parent operating-point/dispatch/SOC evidence for grid-coupled claims; missing/stale parents refuse those claims. One canonical state across Control Strategy/Grid/Reliability and exports; no checked-answer authority from a worker observation or missing evidence. |

## Independent oracles and release decisions

- Use simple power/energy balances and known linear/DC-capacitor limits before comparing complex engines. Independently derive expected quantities rather than mirror implementation calculations.
- Require source/calibration evidence and appropriate reference cases for voltage/PLL/current limits. A second codebase agreeing with the same mistaken assumptions is not sufficient hardware validation.
- Source operating limits/rounding/tolerances before declaring pass. The numerical tolerance must be materially tighter than the engineering limit margin; exact values and interval treatment belong in each case's receipt.
- Run existing relevant DC-template, grid-readiness, island/frequency, solver/cost and evidence suites for the implementation paths touched, plus the applicable new matrix cases. This docs-only assessment does not claim they were run.
- Before a detailed-study release, obtain the repository's applicable design/licensing/engine/UX reviews, exact-head CI/premerge and independent domain acceptance. Production measurement/hardware and representative real-use calibration remain separate, pending work.

Research decision: **proceed with design and a bounded screening prototype proposal; detailed electrical simulation remains conditional on a successful engine/model validation spike.** This is not an external review approval or authorization to waive existing guardrails.
