# Revised decision: useful capability first, external tools only when justified

Date:21 October 2026. Baseline master `525b2587a1db0ebbb9ee3cfbc8adc0dc9c278a7c`. **This memo supersedes the earlier near-term recommendation to evaluate OpenG2G and DPsim as the next integrations.** The physical modeling opportunity remains valid; the product recommendation is narrower after reviewing the actual workflow, user decisions, required inputs and primary engineering guidance.

**Recommendation:** add a modest, optional native **rapid load changes and reserve** assessment inside the existing project flow. Do not add a grid-to-rack top-level section, engine picker, second site model, mandatory telemetry system or default external solver dependency. Hold detailed dynamics for an explicitly requested engineering study with adequate models and evidence. Keep full rack/converter digital twins outside the initial product scope.

## What the workflow actually supports

The approved project design and live source contract have ten ordered sections: Project & site; Carriers & processes; Connection & grid; Loads & demand; Assets & sizing; Time series & profiles; Commercial & parties; Scenarios & optimisation; Readiness → run; Results → reports. The user's capacity/grid/reliability/commercial/control domains are capabilities spanning these sections, not permission to add five competing navigation trees.

Reviewed code provides concrete reuse points:

- `layout/flowSections.ts` and backend `flow_sections.py` agree on section IDs/order. `flowNavigation.ts` supports opening an input in its owning section/drawer while retaining context.
- `HubDesignPanel.tsx` uses the shared active section, but still maps some bespoke cards through the older hub-step model and uses `GenericSectionCard` elsewhere. New advanced screens would compound an existing transition instead of finish it.
- `GenericSiteForm.tsx` already collects growth, load shape, critical share, battery MW/MWh and UPS power/bridge time. No dedicated `dc_load`/`ai_workload`/`rack_kw` frontend references were found at this baseline. That is evidence of an integration seam, not proof that general profile imports are unavailable.
- `GridReadinessScorecard.tsx` already supports contextual advisory rows with unknown reasons, in Guided and Expert. It does not block Run. `flow_service.py` reserves run gating to existing server-authoritative refusals.
- `CampusElectricalPanel.tsx` already takes a solved hub to electrical design and explicitly distinguishes its steady-state scope. `ChaptersSection.tsx` already joins investment, grid, reliability and operation results, while island/control views already exist.

Therefore the next product work should connect **existing** workload, reserve and result concepts in their proper homes. It should not require a user to understand OpenDSS, an external simulator coordinator, PLL tuning or DC-link capacitance to finish an investment plan. Source hashes and the exact reviewed files are in [workflow-fit-evidence.json](workflow-fit-evidence.json).

## Evidence that changes the technical recommendation

### Detailed simulation is conditional on the engineering question

The [PNNL DML report](https://www.pnnl.gov/main/publications/external/technical_reports/PNNL-38817.pdf), §3.1 around printed pages 18–20, distinguishes cases where a trusted phasor model can answer voltage/frequency questions from cases warranting EMT. It also explains that useful power-quality modeling is often constrained by the quality of available equipment data, and does not recommend automatically using EMT to derive all harmonic behavior. This supports choosing the least detailed validated model that answers the question, not making EMT the normal next click.

The [NERC April 2026 large-load guideline](https://www.nerc.com/globalassets/our-work/guidelines/reliability/RG_Risk-Mitigation-For-Emerging-Large-Loads.pdf), chapters on modeling and studies, recommends site-specific dynamic data and operator-defined study/screening needs. It distinguishes positive-sequence and EMT study domains and notes that EMT models may be required by planning entities. The guideline describes itself as voluntary/non-binding; actual obligations and applicable operator requirements must be checked separately. **Deferring a bundled engine must not hide an interconnection requirement or turn an unassessed design green.**

As an industry example, [ETAP's generator step-loading case](https://etap.com/case-study/how-etap-helps-in-modeling-and-analyzing-generator-stability-during-step-loading) describes validated load-flow/short-circuit inputs and OEM governor/excitation data before its transient study. It supports staged model preparation; it is a vendor case study, not independent evidence of GridSpine accuracy or a universal process requirement.

### The strongest apparent library match is narrower than the full problem

[OpenG2G's data pipeline](https://gpu2grid.io/openg2g/guide/data-pipeline/) focuses on inference benchmark replay and synthetic training data. Its [grid integration](https://gpu2grid.io/openg2g/guide/building-simulators/) runs OpenDSS power flow per step. It would be useful for investigating workload/concurrency/latency control, but does not itself close the missing physical UPS/DC-link/rack transient model. It brings a second topology/backend, scheduler/controller interfaces and dataset/model assumptions that need mapping to the existing project.

DPsim supplies an electrical solver and component building blocks, not a prevalidated campus device chain. The inspected inverter interface requires filter parameters, nominal frequency, PLL and multiple control gains; the full requested UPS/rack model also needs other source-backed parameters. Those are not currently supplied by the example's normal asset ratings and cost fields. Wrapping them in a button would conceal missing engineering work rather than make a trustworthy simple experience. Its [model-development documentation](https://sogno.energy/dpsim/docs/developer-guide/writing-a-model/add-model/) also makes implementation and initialization conventions explicit.

### Good UX cannot be inferred from a hidden engine alone

[NN/g's progressive-disclosure guidance](https://www.nngroup.com/articles/progressive-disclosure/) supports keeping infrequent advanced controls out of the primary path. Its [complex-application guidance](https://www.nngroup.com/articles/strategies-complex-application-design/) emphasizes viability, workflow integration, risk and data needs. These are design principles, not usability results for this application. A confusing physical abstraction stays confusing even when its solver selection is hidden.

## Tool decisions

| Candidate | Incremental user decision it could support | Workflow/data burden | Revised disposition |
| --- | --- | --- | --- |
| Native trace/envelope, reserve and existing campus/frequency functions | Does the assumed rapid load behavior challenge the connection or consume reserve needed for backup? | Modest extension using current load/asset inputs; still requires defensible response/energy assumptions | **Proceed with bounded native design/implementation proposal.** No new external engine required for the initial screen. |
| OpenG2G | Can workload scheduling/concurrency reduce electrical stress while respecting computing service limits? | Its workload/performance model, optional gated inference data, second OpenDSS topology and coordinator must be reconciled | **Defer integration; retain as R&D/reference.** Revisit only for an explicit compute-SLA/dispatch use case and a single-project adapter demonstration. |
| DPsim | Do device/network electrical transients meet a specific modeled voltage/control requirement? | Equipment/control parameters, validated physical chain, initialization, numerical benchmarking, native deployment/licence work | **Defer bundled integration.** Consider one specialist external adapter only after an actual study need, supplied parameters and validation case exist. It is not selected as the product's default engine. |
| Zeus/DCGM | Capture customer workload power evidence | Requires supported hardware, measurement-plane/bandwidth/timestamp validation and measurement authorization | **Support a generic measured-trace import contract.** Optional external acquisition tools; no baseline runtime dependency, live setup or mandatory GPU access. |
| HELICS | Synchronize multiple genuinely concurrent independent simulators | Additional time/exchange/initialization/failure semantics and deployment | **Do not integrate now.** Existing immutable artifact exchange suffices for staged studies. Revisit only after a demonstrated closed-loop need. |
| Vessim | Software-in-the-loop datacentre energy/dispatch experiments | Overlaps native planning, SOC, dispatch, comparison and result pipelines | **Do not integrate into the current planning product.** Keep as an external research alternative. |
| OpenDSS as another grid backend | A particular distribution model unsupported by current campus modeling | Duplicate network translation, device/control semantics and validation | **No new backend for the initial scope.** Reuse pandapower; decide later from a demonstrated missing study capability. |
| Modelica Buildings | Cooling/thermal architecture or control decisions with material energy/availability consequences | Chiller/pump/control/thermal data and an additional runtime/model | **Later conditional scope.** Existing PUE-based planning remains the starting model with limitations disclosed. |
| PyBaMM | Cell/pack aging or thermal behavior for a calibrated duty cycle | Cell chemistry/pack parameters and surrogate validation | **Leave outside the baseline.** Use transparent, bounded source-backed life/augmentation assumptions; refine only when they change a relevant decision. |
| MIT UPS research code / PSCAD references | Model-structure and independent benchmark exploration | Research assumptions, calibration and source/runtime rights | **Reference or specialist validation only.** No automatic production library import or compliance authority. |

Dynawo, OpenIPSL and PowerDynamics.jl remain alternative research routes, not simultaneous integrations. The existing ANDES/Q4 hold remains unchanged.

## Hard integration gates

A library should enter an implementation PR only when all applicable gates can be answered with evidence:

1. **Decision:** it answers a named user question that current native capability cannot answer, or fulfills a defined external study requirement. More detailed plots alone are insufficient.
2. **Inputs:** a representative project can supply the required data, its provenance and uncertainty. Assumed demonstration parameters must not masquerade as vendor calibration.
3. **Project continuity:** one native project/topology/asset identity and state mapping; no re-entering the campus in a second tool, no competing SOC, cost or workload truth.
4. **Validation:** independent reference cases, scope-appropriate physical checks and tolerances. A small timestep, matched annual mean or agreeing uncalibrated models do not suffice.
5. **Experience:** meaningful outcomes and next actions in existing sections; no mandatory engine choice, unrelated navigation, extra setup for unaffected projects or assistant-only edits.
6. **Operations:** supported deployment/licensing, bounded execution, cancellation, version pins, failure states and maintainable model ownership.

Without these, the correct outcome is defer/metadata/reference, not a hidden experimental engine behind a green card. Benefit assessment must include explicit compliance/safety obligations even where a detailed study does not change the capacity optimum. This review has no customer dataset or usability study proving actual benefits; those remain release gates.

## Recommended product boundary

**Initial capability:** an optional “Rapid load changes” subsection on the existing load, cross-shown read-only on the connection and selected storage/UPS asset. The assessment lives under **Control Strategy → Response to rapid load changes**, with its primary response/reserve comparison there and linked Grid/Reliability summaries of the same result. Scenarios owns the study request, existing jobs execute it and existing owning forms hold the inputs. Use the compatible electrical operating point for grid-coupled studies; a declared-baseline analytical screen makes no network-dynamic claim. A short event establishes neither annual reliability nor transient voltage compliance. The generic envelope can support other large varying loads; data-centre archetypes supply relevant defaults. Preserve the ten-section flow and current server run-readiness authority.

**Advanced capability:** if an operator requirement, declared engineering objective or screening concern needs a detailed study, expose “Detailed electrical assessment” in the existing electrical/control context. Start with the current validated handoff capabilities and report their limits. Any future executed-model adapter must meet the gates above. Do not promise a complete PSCAD handoff merely because PSS/E exports exist.

**Outside current scope:** a full GPU-to-chip/cooling/EMT digital twin, live GPU controls, a general multi-engine co-simulation workbench, automatic component gains, production controls or universal grid-compliance certification.

The [journey specification](06-INTEGRATED-JOURNEY.md) defines the exact placement, non-confusing states and acceptance tasks. The existing engineering architecture remains a possible future capability map, not a commitment to integrate every engine listed there.
