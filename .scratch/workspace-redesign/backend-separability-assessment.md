# Required gate: backend separability and workflow dependencies

Status: ready-for-agent — source reconnaissance started; runtime assessment not completed

Owner request, 2026-10-20: assess whether the individual activities inside the three UI groups can actually be run separately. UI grouping, a preset or Select all is not proof of backend independence.

## Initial source evidence

- `pypsa-gui/backend/routers/simulation.py`, `run_ac_pf`: explicitly offers a standalone AC-PF trigger, but requires solved dispatch, acquires the PyPSA lock and changes flow/voltage result arrays after snapshotting the LP state. It is separately triggerable, not independent of operating-source state.
- `pypsa-gui/backend/routers/campus_electrical.py`: drafting/running parses a network and solves load flows; the joint MILP path makes a least-cost run first unless a fresh matching run exists. An apparent sizing or compliance action may therefore include other calculation work.
- `pypsa-gui/backend/services/adequacy/eh_study.py`: the hub orchestrator has optional stages but explicitly requires `apply_pack` and `ens_solve`. Sibling result tables are cleared when a study starts. Skipping a UI activity cannot be assumed to skip a required solve or preserve old evidence.
- Native process, island/control and planning/dynamics entry points exist in different modules. Finding files/handlers does not establish their independent lifecycle, supported combinations or execution readiness.

These are read-only source observations, not a completed audit or an executed native test. Reassess current master before implementation.

## Assessment to complete before enabling execution

| UI activity | Native starting points | Questions / proof required |
| --- | --- | --- |
| Capacity planning, including dispatch/economics | `routers/simulation.py`; `services/solver/`; native result oracles | Which solve modes genuinely produce sizing, dispatch and the declared cost basis together? Which inputs/locks/results are shared? No duplicate solve or unsupported financial claim. |
| Detailed process modeling | `services/solver/process_uc.py`; `services/process_products.py`; `services/results/process_view.py` | Which refinement can be optional? Basic physical coupling already in the model must remain when detailed process assessment is off. Units, fixed process sizes, production targets and valuation remain valid. |
| Reliability assessment | `services/adequacy/eh_study.py`, `eh_stages.py`, `frontier_loop_runner.py`; `routers/adequacy_worksheet.py` | What can consume existing evidence, what requires a new ENS/planning solve, and which stages are mandatory? Identify outage data, method limits, locks, budgets and any pack/model mutations. |
| Electrical infrastructure sizing | `services/campus_electrical_service.py`; `routers/campus_electrical.py` | Does sizing require load flow, grid-code constraints or least-cost setup? If these cannot be split, expose the bundle honestly and disclose its included work. |
| Grid-code work, including required load flow | `routers/simulation.py::run_ac_pf`; `services/ac_pf_service.py`; `services/campus_grid_code_service.py`; campus electrical study | AC-PF trigger vs campus-study calculations, operating-source freshness, valid PCC/code version, clause/method coverage and state mutations. A PF result cannot stand in for all grid-code evidence. |
| Contingencies | `routers/gridspine.py`; native study pipeline and contingency services | Which project/source/topology prerequisites and base-case calculations are compulsory? Can a bounded contingency study run alone? What does its absence mean for other claims? |
| Control strategies | `services/campus_island_tuning.py`; campus control/settings handlers | Read/propose/apply effects, existing island-report dependencies, valid supported equipment fields and native approvals. Generic controller support cannot be invented. |
| Islanding/recovery | `services/campus_island_report.py`, island configuration/solver modules | Steady/islanded screening vs transitions/recovery, source requirements, reserve/control dependencies and unavailable outcomes. No adequacy-to-stability substitution. |
| Frequency/time domain | `routers/gridspine.py`; supported native checks and external handoff/read-back | Project and saved-dispatch requirements, source provenance, simulator support/configuration, what is screening vs actual time-domain execution and required receipts. |

For every activity, produce a versioned matrix recording: separable/bundled/not supported/unknown; exact native entry point; required input and output schemas; upstream/existing-source options; shared mutable state; locks; native approvals; implicit substeps; cost/attempt/time budgets; cancellation/partial/uncertain effects; evidence/freshness boundaries; template/install/role eligibility.

Run focused native tests through the ordinary authenticated/harness seams. Cover standalone and bundled paths, missing/changed sources, disabled optional stages, denied/blocked writes, cancellation/failure/partial effects, source/evidence contamination and result reuse. Do not weaken native prerequisites just to match UI checkboxes. Use existing oracles for numeric claims; no paid calls or external simulator execution are required merely to complete source classification.

## Acceptance

1. All nine choices have an evidence-backed classification; unknown entries remain non-runnable.
2. Mandatory internal calculations are disclosed in review and separately admitted where the native contract requires it.
3. Disabling an optional activity neither deletes required physical constraints nor silently launches a different study, cancels work or certifies old results.
4. Presets and Select all modify only the proposed scope. They never turn missing prerequisites into readiness or approve included operations.
5. Any necessary change to the simplified UI is documented before native execution is wired in. The current preview is not a promise that every combination is independently executable.

## Latest owner-supplied template and scenario scope

The design now groups six templates by three industries: `eh_datacenter` / `eh_datacenter_staged`, `eh_h2_hub` / `eh_h2_hub_staged`, and `eh_microgrid` / `eh_microgrid_staged`. Keep IDs, period/horizon/discounting and source compatibility explicit in the assessment. DC 01 / #421 includes optional AI-training/profile-envelope work according to the owner; verify its actual merged schema and eligibility rather than treating a marketing label as availability.

The staged ammonia template in #418 is owner-reported unmerged and precedes mining. Neither local design-only ID (`concept_ammonia`, `concept_mining`) may enter native template creation or execution. Confirm/implement real canonical IDs before enabling either.

Add native tests for shared heatwave/dark-still/low-wind inputs, affected asset assumptions and source invalidation. Synthetic office/two-shift load shapes require explicit provenance and annual-MWh normalization under snapshot weights; they are not measured loads. The prototype stores input drafts only and does not establish any backend support for these transformations. All readiness observations above require reassessment against fresh master.
