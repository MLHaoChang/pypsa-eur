# Current implementation and gaps

Baseline master: `525b2587a1db0ebbb9ee3cfbc8adc0dc9c278a7c`. Evidence is source inspection, not a fresh successful application simulation. [native-evidence.json](native-evidence.json) pins exact files, SHA256 hashes and relevant symbols/line numbers. Research/publication only; no engine code changes.

## Existing code worth reusing

| Capability | Evidence | Interpretation |
| --- | --- | --- |
| Staged campus investment | `project_templates/eh_templates.py`: `build_eh_datacenter`, `DC_PHASES`, investment periods | Existing phased 2027/2030/2035 example, not proof of a calibrated 20-year supply/workload forecast. |
| AI workload parameters | `models/flex_archetypes.py`: `AiWorkloadSpec`, `DataCentreLoadSpec`, `DcPhase` | Assumed training utilisation, ramp/checkpoints, oscillation, PUE, phase MW and rack density; field presence does not prove a detailed rack model. |
| Profile and envelope | `services/dc_load_profile.py`: `hourly_profile`, `dynamic_envelope`, `_phase_envelope` | Hourly training mean/PUE; subhourly terms stored as an envelope, not simulated into the planning LP. Envelope says IT-side at PoC before mitigation and excludes cooling. |
| Creation opt-in and compatibility tests | `routers/projects.py`; `tests/test_dc_load_profile.py` | Opt-in `dc_load` on data-centre templates, default unchanged behavior covered by existing tests. This assessment did not rerun those tests. |
| Advisory grid readiness | `services/adequacy/grid_readiness.py`: scorecard and dynamic-requirement rows | Solves nothing; before-mitigation ramp/oscillation checks; missing/dynamic-only evidence remains unknown. It does not grant run readiness or engineering compliance. |
| Static campus network and reactive assets | `gridspine/static/campus_flow.py`, `campus_reactive.py`, `campus_invest.py`, `campus_milp.py` | Load flow and equipment sizing including STATCOMs/shunts; some modules explicitly say steady state only. |
| Converter modes and source tags | `gridspine/schema/island.py`; `services/adequacy/island_config.py` | GFL/GFM/droop/VSM/FFR and simplified UPS behavior already have shared schema/presets. Reuse rather than invent another incompatible control catalogue. |
| Frequency transient screening | `gridspine/static/frequency.py`: `simulate`, `Unit`, `UpsLog` | Fixed-step RK4 with callable power disturbance, aggregate frequency, governors and simplified saturation/UPS energy. Explicitly no voltage dynamics, inter-unit oscillation or current-limit dynamics. |
| Planning feedback | `services/campus_island_cuts.py`; `frequency.py` heuristic-cut label | Existing proposal/apply path for limited-validity frequency cuts. Extend provenance/validity as needed; do not claim a new first-ever planning-feedback feature. |
| Dynamics handoff | `gridspine/handoff/dyr_writer.py`, `campus_bundle.py`; island plan I8/Q4 | Existing PSS/E handoff work; optional ANDES path is parked pending Q4. A .dyr export is not an executed/validated RMS or EMT study. |

`gridspine/schema/dc.py` refers to DC power-flow sensitivities/PTDF/LODF. It is **not** a physical direct-current rack/DC-bus model. Do not infer existing 800 VDC functionality from that filename.

## Gap inventory

| ID | Gap | Why it matters / proposed response |
| --- | --- | --- |
| G01 | Explicit measurement planes and energy boundaries | Define GPU board, server, rack input, protected AC/DC bus, UPS input/output, campus and PoC. Replace ambiguous use of an IT envelope as a PoC proxy with explicit transformations/losses. Preserve legacy semantics until a deliberate opt-in migration. |
| G02 | Workload trace and cohort model | Add phase-aligned compute/communication/checkpoint/restart patterns, correlation, stagger and hardware vintages. Linear scaling of one GPU trace assumes synchronisation; square-root scaling assumes independence. Neither is a universal campus law. |
| G03 | Adequate measurement bandwidth | Existing defaults are assumed, not calibrated. Preserve sample interval, integration/averaging window, timestamp accuracy, measurement plane and gaps. Faster polling or interpolation cannot recover lost subsecond dynamics. |
| G04 | Workload service constraints | Existing curtailment/shift fields and recorded shares do not establish a training scheduler, deadlines, convergence-quality preservation or inference SLA model. Add explicit allowed controls and performance/quality evidence. |
| G05 | Physical UPS/DC-link/rack model | Model rectifier, DC link/capacitance, battery or BBU/DC-DC, inverter/bypass/distribution and constant-power/voltage-sensitive load behavior at suitable fidelity. Existing UPS screen is not this chain. |
| G06 | Active-power smoothing with real limits | Add power/current/voltage limits, delay, ramp, efficiency, recharge, SOC/SoH and repeated-event handling. Purely filtering a load waveform without an energy balance invents power. |
| G07 | Voltage/PLL/control interactions | Multi-bus voltage response, converter inner/outer loops, current-priority/anti-windup, weak-grid and instability interactions require appropriate dynamic equations and validation. Small timestep alone is insufficient. |
| G08 | Shared service allocation | Smoothing, island reserve, backup autonomy, frequency support, arbitrage and other revenue services share the same MW/MVA/MWh. Require joint allocation, dispatch-linked initial state and explicit priority. |
| G09 | Power quality and protection | Add phase-aware fault/ride-through and rack-dropout/restart behavior. Switching harmonics, unbalance, flicker and DC ripple require suitable waveforms/models and metrics; a PoC power envelope cannot certify them. |
| G10 | Architecture comparison | Existing ticket 05 proposes AC/MV UPS/800VDC/SST comparisons; it is not evidence of detailed implemented topology models. Compare actual chains, losses, support capabilities and cost boundaries. |
| G11 | Long-horizon coupling and asset aging | Connect growth, utility energization, equipment replacement, power-density changes, hourly adequacy, fast service duty and degradation over the selected operating horizon. Preserve chronological SOC and operating-year weights. |
| G12 | Deterministic engine/co-simulation contracts | Define initialisation, physical ports, power signs/units/bases, solver/communication timesteps, delays, interpolation, error/failure semantics and reproducible versioned artifacts. Start with one-way replay before tightly coupled control. |
| G13 | Scenario selection and feedback validity | Select fast events by ramps, correlation, SOC, grid strength, outages and controller saturation, not only annual peak MW. Derived cuts may be heuristic/local and must state their domain. |
| G14 | Evidence, UX and assistant boundaries | Trace source, study fidelity, modeled quantities, initial state, limits and residual gaps must be visible in Guided/Expert/report/chat. No “checked compliance” result without the corresponding evidence and scope. |

## Existing decisions that the proposal does not silently override

The data-centre readiness spec currently excludes in-app subsecond EMT and keeps requirements generic/assumed unless confirmed. This assessment proposes a **future extension**, initially an optional external worker/adapter; it does not claim EMT is already authorized for implementation or bundled deployment. Any scope change needs the normal owner/design and repository gatekeeper process when engine implementation begins.

The island plan parks ANDES under Q4 and keeps multi-bus dynamics distinct from native aggregate frequency screening. Do not reinterpret a separate process as a legal/licensing exemption. Existing generic code profiles and template ramp/oscillation limits remain assumed placeholders, not universal TSO rules.
