# Proposed integrated model and interface specification

This is a design proposal, not a list of existing classes or a certified solver. Reuse the [current native seams](01-CURRENT-GAPS.md). The detailed-engine path is an extension beyond the earlier data-centre-readiness scope and must be reviewed before implementation.

This document describes possible capabilities. The [revised workflow-fit decision](05-WORKFLOW-FIT-DECISION.md) controls near-term scope: native contextual screening first; external engines, live measurement and full rack models are deferred. The [journey specification](06-INTEGRATED-JOURNEY.md) owns user-facing placement and integration gates.

## Timescales and question ownership

| Level | Representative horizon / resolution | Questions / appropriate model |
| --- | --- | --- |
| Investment | Selected 20-year operating horizon; explicit asset/build periods | IT growth/hardware vintages, connection energization, firm MW and energy supply, costs, aging/replacement and adequacy; existing PyPSA planning plus explicit scenario assumptions. |
| Operations | Chronological hours and stress days/weeks; finer dispatch if needed | SOC, reserve scheduling, fuel, prices, weather, cooling, availability and workload deadlines; preserve inter-period storage constraints rather than reset every representative day. |
| Supervisory workload/energy control | Seconds/minutes, with relevant subsecond actions if modeled | Job staggering, power caps, checkpoints/restarts, UPS/BESS recharge and control priorities; deterministic policies with explicit delays and service limits. |
| Electromechanical / averaged converter | Short event windows; timestep chosen by model bandwidth | Frequency/voltage, rectifier/inverter/DC-link dynamics, current saturation and weak-grid/control interactions. Native frequency screen covers only part; detailed network model requires additional engine/model work. |
| Switching / detailed power quality | Selected very short windows; potentially microsecond-scale steps | PWM/DC ripple, harmonics, fast protection and converter interactions when switching fidelity is necessary. Do not run the entire planning horizon at this resolution. |

The numerical step is chosen by the actual equations and convergence requirements, not by the word “millisecond.” A rapidly sampled steady-state solution is still a steady-state model. Average-value EMT can represent selected control/DC dynamics without resolving switching; it cannot validate switching harmonics it omits.

## Physical hierarchy and ports

Represent aggregate cohorts and electrical supply zones; it is not necessary to simulate every GPU or server individually. Preserve stable native asset IDs and a mapping between each cohort and its actual supply path:

1. Grid equivalent and connection: voltage/frequency base, source strength/X:R or impedance model, normal/weak/fault states, import/export and operator-agreed ramp/oscillation limits.
2. Campus transformers/cables/busbars/switchgear and reactive equipment; include alternate feeds and dependencies.
3. UPS topology: rectifier/DC bus/storage/inverter/bypass, or the actual MV/DC alternative; model the direction in which support is physically available.
4. Rack/BBU/PDU/server supply: explicit DC/AC voltage domain, capacitance and permitted voltage window, ratings, transfer/droop/current limits and load characteristic.
5. IT cohorts: training/inference/mixed jobs, rack density, synchronization/correlation, permissible control and performance constraints.
6. Cooling/pumps/fans/auxiliaries as separate slower branches, with any outage/thermal ride-through assumptions identified.

An UPS inverter forming the protected output does **not** automatically make its upstream grid-facing rectifier grid-forming or bidirectional. A 2N architecture does not mean double-counting the same IT demand on two active supply paths; carry feed-sharing and failover assumptions.

Every port declares direction, quantity, unit and measurement plane: P/Q, voltage, current, frequency and/or DC-link state as supported. Specify RMS versus instantaneous/peak, line-to-line versus phase, 50/60Hz base, kW/MW and W·s/kWh/MWh. Convert only with explicit lineage. Avoid treating a schematic rack as a perfect constant-power load outside its valid voltage range.

## Proposed artifacts

Names below are proposed file/schema concepts, not current APIs. Prefer extending compatible native envelopes/sidecars over duplicate stores.

| Artifact | Minimum content |
| --- | --- |
| Workload trace reference | Source hash/rights, timestamps and sample/averaging window, plane and units, measured/synthetic/assumed tag, workload/hardware/cohort IDs, phase relation, gaps/uncertainty and supported controls/performance metrics. |
| Power-path map | Native topology/asset IDs and digest, AC/DC domains, upstream/downstream ports, feed sharing, converter/UPS/BBU/DC-link parameters with source tags, limits and loss scope. |
| Fast-event case | Parent plan/run hashes, operating period/weather/dispatch instant, pre-event state and SOC/SoH, grid equivalent, event definition/trace, control settings/priorities and permitted constraints. |
| Engine request | Pinned executable/model/license manifest, approved model/data dependencies, initialization, solver/communication steps, tolerances, horizon and resource caps, output channel/plane definitions and cancel/failure behavior. |
| Engine result receipt | Exact request/model/input hashes, engine/version, convergence and coverage status, signed physical quantities/units, event/limit violations, raw evidence references, computed metrics and explicit omitted phenomena. |
| Planning feedback | Required MW/MWh/MVA/reserve/response constraints, cost/degradation corrections, derivation/evidence, scenario/topology/controller validity domain, heuristic/exact status, expiry and explicit proposed apply action. |

All are project/owner scoped and immutable once published; edits create new versions and invalidate stale dependent results. No opaque uploaded script, FMU or native model binary executes merely because it is attached. Execute only through reviewed engine adapters and ordinary bounded job controls.

## Energy and current balance

Define storage discharge as positive support to the load. At a declared aggregate plane, a simple loss-explicit balance is:

`P_grid + P_generation + P_buffer = P_IT + P_cooling + P_aux + P_losses`.

For a discharge-positive battery:

`dE/dt = -P_discharge/eta_discharge + eta_charge*P_charge`, with consistent seconds/hour conversion.

For a lumped DC link:

`C*Vdc*dVdc/dt = P_rectifier + P_storage - P_downstream - P_link_losses`.

These are illustrative interface balances, not a replacement converter model. Respect SoC/SoH-dependent power/current limits, nonzero response delay, charger/discharger behavior and voltage constraints. Available AC apparent power limits simultaneous P/Q: `P^2 + Q^2 <= S_available^2`; current-limited capability generally falls during voltage sag and needs the appropriate model.

A low-pass target for grid demand creates a **required residual buffer power**, not guaranteed achieved smoothing. Simulate saturation, energy depletion, recharge and controller anti-windup; propagate the actual residual to the PoC. A large negative IT step can cause overvoltage or an import/export violation when the buffer cannot absorb energy. Do not model only load increases.

Reserve policy must explicitly partition/coordinate peak smoothing, fast support, sustained island operation, critical UPS autonomy, arbitrage and other services. Include N-1 failures and dispatch-linked SOC. Nominal battery size alone is insufficient. STATCOMs/shunts address reactive/voltage needs; active-power buffering requires storage, other generation/loads or permissible workload changes. A hybrid active/reactive device must carry its actual energy and MVA constraints.

## Workload, telemetry and losses

Support measured trace replay and explicitly synthetic archetypes. For training, carry compute/communication, checkpoint, restart and synchronization structure; for inference, preserve request/throughput/latency conditions. Control variables may include staggered starts, concurrency, supported power caps or hardware frequency settings, but an arbitrary power reduction cannot be claimed performance-neutral. Bound job completion/latency/quality and report lost/deferred useful work separately from electrical energy.

Public single-GPU or inference datasets do not establish campus training fluctuations. Repeated data must disclose aggregation/correlation assumptions. Preserve high-resolution event evidence alongside planning aggregates: mean-energy agreement alone does not preserve ramps, spectra or extremes. Synthetic millisecond traces remain synthetic even if their hourly mean matches measured data.

GPU board telemetry excludes other server/rack loads and converter losses. NVML's architecture/API-specific averaging means fast polling can repeatedly read an averaged signal. Require verified measurement bandwidth and synchronization for each plane. Rack/PDU/UPS meters or authorized higher-bandwidth electrical measurements are needed to calibrate the full chain.

The existing hourly PUE convention already includes UPS/distribution losses. Preserve that behavior for existing projects. In a detailed model, separate IT, cooling and electrical losses and reconcile the annual mean to the planning representation; do not add converter losses again on top of a PUE that already includes them. Do not make cooling jump instantaneously by multiplying a fast IT pulse by annual PUE.

## Linking to 20-year planning

Use scenario-dependent IT/energy growth, phase dates and rack-density/hardware changes rather than one fixed trace for all future years. Carry demand uncertainty, weather and grid-strength scenarios, connection stages and equipment retirements/augmentation. Renewable nameplate MW and annual purchased MWh do not prove firm hourly energy or critical-load reliability. Existing adequacy/stress pathways remain necessary.

The outer loop is:

1. Solve a candidate plan with normal chronological operating and reserve assumptions.
2. Select stressful windows by ramp/spectral content/correlation, weak grid, lowest reserve SOC, high P/Q loading, heat/cooling and component failures, including cases away from annual peak MW.
3. Initialize the fast case from that exact plan/dispatch/state. Compare allowed mitigation/control variants with the same input evidence and clear differences.
4. Return achieved ramp/oscillation, voltage/frequency/energy/current constraints, workload penalty, losses and degradation/service duty, with model validity.
5. Propose revised capacities/reserves/control choices and costs. Re-run only after ordinary review/apply controls; repeat until the selected cases meet scope or report infeasibility/unresolved evidence.

Existing heuristic frequency cuts are a precedent. A simulated local controller envelope is not automatically a globally valid linear planning cut; retain trust region/scenario/topology and revalidate after changes. No claimed global optimum or comprehensive robustness from a finite event suite.

For aging, distinguish fast-service power/C-rate/RMS heating and repeated cycling from annual energy throughput. Start with transparent bounded life/cost assumptions and existing BESS fields; use a validated degradation surrogate if needed. PyBaMM is a later offline calibration option, not a requirement to run cell electrochemistry inside every planning solve or every 20-year event.

## Fidelity and user interaction

Use existing example and workflow sections with an optional **Grid-to-rack assessment** study scope. Guided shows workload/growth, architecture, mitigation choices and actual missing inputs; Expert exposes trace mapping, grid equivalent and controller/solver detail without duplicating asset definitions. A common trace view compares raw IT demand, protected-bus behavior, PoC demand and storage response on explicitly named planes.

Separate result labels: planning/adequacy, analytical screen, native aggregate frequency, averaged electrical transient and switching EMT. Each card lists modeled/omitted quantities, measured/assumed inputs, limits and provenance. Unknown voltage/harmonics cannot become pass merely because PoC active power is smoother. Grid-code limits need exact applicable operator/profile evidence; generic placeholders remain assumed.

The chatbot prepares studies, explains evidence and proposes native approved edits. It is not a millisecond controller and never sends live GPU, inverter or site commands as part of this assessment. Optional software/hardware-in-the-loop is a separate approved sandbox experiment, not production activation.
