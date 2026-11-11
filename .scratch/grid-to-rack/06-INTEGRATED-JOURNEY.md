# Integrated journey and product acceptance specification

Proposed behavior, not implemented UI. This document applies the [revised fit decision](05-WORKFLOW-FIT-DECISION.md). Preserve current native section IDs/order, project ownership, server-authoritative run states and one input editor per value.

## What the user should experience

The initial interaction is a contextual extension to normal project work:

**Configure the same site and loads → plan assets and energy → establish the relevant electrical operating point → assess rapid changes in Control Strategy when requested → review linked Grid/Reliability implications → explicitly apply supported changes and rerun.**

This is a decision journey, not a forced backend execution order. Reliability can participate in capacity sizing, and grid/control feedback may require iteration. Do not make load flow or dynamics mandatory for every project, or suggest that one completed stage guarantees all later engineering questions.

## One assessment home, shared implications

The optional study is **Control Strategy → Response to rapid load changes**. This is its user-facing execution and primary result home within the existing capabilities/results, not a new top-level flow section. Scenarios & optimisation still owns the study request, Assets & sizing owns supported controller/response parameters, and existing jobs own execution. A shortcut from Grid or Reliability opens this same study and result, preserving the originating context; it does not start a second simulation.

| Context | Owned input or result | Boundary |
| --- | --- | --- |
| Loads & demand | Event size, shape, duration, source and physical plane | One event editor; hourly demand does not establish a measured rapid event. |
| Connection & grid | Connection limits and electrical model/operating point | Static load flow establishes its supported operating-point quantities; it does not calculate transient response. |
| Assets & sizing | UPS/BESS power/energy, supported response/control settings and joint reserves | No copied asset parameters or implicit extra reserve. |
| Scenarios & optimisation / Readiness → run | Optional study selection, parent case and scoped input readiness | Existing run contracts apply; missing detailed-study inputs do not globally block valid preliminary planning. |
| Control Strategy results | Primary rapid-response result: achieved storage response, residual PoC disturbance, energy/SOC, recharge and remaining reserve | Initial power/energy/reserve screen; frequency only when explicitly modeled. Voltage, harmonics and converter transients remain unassessed. |
| Grid results | Linked original/achieved PoC and entered-limit summary from that same result | Preserve original before-mitigation readiness; no independent rerun or compliance certificate. |
| Reliability results | Linked backup/autonomy and shared-reserve implications from that same result | No annual LOLE/EUE, uptime or failure-probability claim from a short event. |
| Capacity planning | Proposed sizing/cost/reserve changes tied to validated feedback | Claim an effect on the optimum only when the corresponding constraint is supported, enforced and verified. |

A grid-coupled study references the compatible grid operating-point receipt plus the parent dispatch/SOC, topology, period, controller, event and limits. Missing or stale prerequisites produce scoped Inputs needed/Stale, never fabricated grid results. A standalone analytical power/energy screen may use an explicitly declared baseline without forcing a load-flow run, but cannot then claim network voltage or grid-coupled dynamic behavior. Each assessment has one canonical result identity shared by all summaries and exports; comparisons retain a separate identity for each variant. Cancellation, failure or changed dependencies must propagate consistently to every view.

### 1. Capacity planning stays familiar

In Project & site, choose the same horizon/growth phases as today. In Loads & demand, choose or import the normal load profile. A data-centre preset may offer a collapsed **Rapid load changes** subsection alongside that load, not a new top-level setup page.

The basic subsection offers a source-tagged example or authorized imported event trace, a concise variability summary and a clear choice to include a screening study. It must not invent a measured event from hourly demand. Default examples remain explicitly assumed; a user with no event data can continue planning with fast behavior marked not assessed.

When expanded, show only what the chosen screen actually uses, such as step/ramp/duration and the measurement plane. Advanced cohort/synchronization settings can live under Advanced for this input. No engine selector, PLL gains, DC-link model or GPU account is needed for normal capacity work. Do not create parallel single/staged examples to hold event data.

### 2. Connection and assets show the same facts

Connection & grid owns operator-agreed limits, source/assumption status and grid-strength inputs. It shows the selected load's variability summary read-only with an in-place link back to its load editor. No second copy of the trace or limit.

Assets & sizing owns UPS/battery ratings, reserve requirements and source-backed response capability. Existing power/energy/bridge-time inputs should be reused where their semantics match. Response latency/current capability cannot be inferred from a battery's MWh rating or a control-preset name. Any new behavior fields belong in that asset's form, with Advanced for this asset where appropriate.

If both backup and smoothing use an asset, show a concise **shared reserve** explanation and the quantities each calculation can actually establish. Do not present the same full MW/MWh as independently available for every duty. Keep pricing/financial assumptions in Commercial & parties and consumption/criticality on the demand.

### 3. Request an answer, not a simulator

Scenarios & optimisation owns the optional study request and comparable variants. The user-facing choice is **Response to rapid load changes**, shown under Control Strategy and described as assessing the connection and available reserve. It must accurately state whether it is screening only. Entry points elsewhere link to the same request and result.

Readiness → run summarizes missing inputs and requested-study status. Existing planning Run is not globally blocked by a new advisory or missing specialist model. If a requested detailed study lacks required inputs, that study cannot run or pass; other valid preliminary work may still proceed. An actual operator/delivery requirement remains visible as unresolved in the relevant results/report rather than being hidden by a general Ready to run label.

### 4. Explain results where users already look

**Control Strategy is the primary result view.** Show achieved response, residual PoC load, energy consumed/recharged and remaining reserve there. Grid and Reliability provide linked summaries of this same result: entered connection-limit outcomes in Grid, shared reserve and the modeled backup/autonomy implications in Reliability. Use existing operation/control result integration rather than a new “Grid-to-rack” silo or top-level flow section. A successful short event does not establish annual reliability; a separate supported adequacy calculation is needed for LOLE/EUE. Ordinary load flow establishes no transient response.

The existing grid-readiness endpoint/scorecard explicitly screens the original envelope **before mitigation**. Preserve that meaning. Do not replace the source envelope with a filtered trace or overwrite its advisory verdict with a post-mitigation result. A smoothing assessment needs its own case/result receipt, linked from the existing result context, showing the original and achieved values and their respective scope. Any future checked-answer profile must validate that exact derived result and its dependencies before calling it checked.

At screening depth show a small set of meaningful outcomes:

- Assessed event and physical plane; actual/synthetic/assumed source.
- Achieved PoC ramp/residual versus entered limits, with model and limit basis.
- Buffer peak power, energy/SOC and remaining modeled backup/reserve.
- Any supported cost/loss or workload-service consequence, with unknown values left unknown.

Use a read-only comparison between current and proposed mitigation with common workload, operating state and horizon. Show an optional single trace view with input load, achieved PoC and storage power on named planes; detailed solver logs are secondary evidence.

Results use precise states such as **Not requested**, **Inputs needed**, **Screening within entered limits**, **Screening exceeds entered limits**, **Detailed assessment needed**, **Running**, **Failed/cancelled** and **Stale**. Map them to existing result/quality contracts where possible; do not add a competing global project-status enum. A screening pass never marks rack voltage, harmonics or operator compliance established.

Likewise, acknowledging an assumed input in the existing flow is not equipment calibration or compliance approval. Reuse value-bound acknowledgement/staleness behavior; changed event, asset, controller, reserve, limit or source quality must invalidate the relevant assessment. Job completion alone cannot supply a missing engineering result.

### 5. Make the next action concrete

If a screen exposes a shortfall, offer the applicable existing input action: review connection/event assumptions; adjust proposed UPS/battery power or reserve; change a supported workload assumption; compare another native design. Open its owning editor in place, retain the user's place and show exact old→new changes. The assistant can help explain and prepare the same ordinary approved edit; the UI must remain usable without a model API key.

“Apply and rerun” requires a valid supported parameter/constraint and explicit ordinary confirmation. A required fast reserve that the native planner cannot yet represent must remain a reported limitation; do not assert the 20-year optimum includes it. Native planning-reserve work can be a separate bounded extension without adding another solver engine.

Offer a specialist assessment only when it answers an explicit question/requirement beyond the current model. Show why, what data is needed and what the existing handoff actually supports. The default path never asks a capacity planner to choose DPsim, OpenDSS, an FMU or a hardware-in-the-loop environment.

## Representative paths and stop conditions

| User/task | Expected path | What must not happen |
| --- | --- | --- |
| Developer comparing 20-year supply/capacity costs with hourly demand | Normal project/asset/price/scenario flow; optional variability summary clearly not assessed if no trace | Mandatory GPU account, engine setup or extra required fast-control fields. |
| Data-centre engineer with a weak connection and known load step | Enter source-backed event/limit in owning forms, request native screen, compare reserve-aware options | Automatic “compliant” verdict, hidden response assumptions or rerouting to another campus editor. |
| Reliability engineer sizing grid-loss support | Existing adequacy/island flow, with shared-asset reserve implications shown | “Smooth PoC” mistaken for uptime, failed-component resilience or available backup energy. |
| Electrical specialist with an operator-required dynamic study | Existing electrical/control context; explicit requirements/model inputs; validated handoff or admitted adapter | Uncalibrated model guessed by the assistant, loss of project/state mapping or silent deferment of a real delivery obligation. |
| General C&I/H2/microgrid user | Generic envelope only where useful and selected; otherwise normal existing work | Data-centre/rack jargon or mandatory training concepts across the whole tool. |
| Cooling/thermal designer | Existing planning assumptions and a clearly separately scoped thermal question | Automatic Modelica integration simply because a data-centre example is selected. |

## UX acceptance tasks

These are proposed acceptance criteria, not results of a user study. Validate with representative users and domain reviewers before claiming improved usability.

| ID | Task / condition | Acceptance evidence |
| --- | --- | --- |
| UX01 | Finish a normal capacity plan with no fast-event data | Zero new mandatory fast-model fields, engine choices or external accounts. Existing valid planning Run remains available. |
| UX02 | Select another template with no requested rapid-change study | No mandatory data-centre controls; existing section/order/state contract preserved. |
| UX03 | Edit one load event or one connection limit from its result | One owning editor, in-place target/return behavior and exact read-back; no duplicate value. |
| UX04 | Switch Guided↔Expert during the task | Same active section, project, trace, asset IDs and values; no reset/recreation. |
| UX05 | Add both backup and smoothing duties to a battery | Understandable shared-reserve display; underlying joint MW/MWh/MVA constraints checked, not just UI copy. |
| UX06 | Interpret a successful analytical screen | User can identify the assumed/measured basis and explain which voltage/harmonic/compliance questions remain unassessed. |
| UX07 | Missing/low-bandwidth event trace or response parameters | Specific missing input and no fabricated detailed result; normal preliminary planning still usable. |
| UX08 | Request a detailed study with missing equipment model | Scoped refusal/inputs-needed, clear specialist path and unchanged valid preliminary results. |
| UX09 | A real operator dynamic requirement remains unresolved | Visible unresolved delivery requirement in relevant results/report; no screening pass or global Ready label implies it is met. |
| UX10 | Compare mitigation options | Common parent plan/dispatch/trace/state or explicit incompatibility; differences and supported cost/reserve effects understandable. |
| UX11 | Apply a proposed change | Correct owning asset/section, explicit supported old→new confirmation, stale dependent results and ordinary rerun scope. |
| UX12 | Assistant unavailable or disabled | All required input edits and study actions have native controls. |
| UX13 | Failed/cancelled run or lost response | Existing job/evidence semantics; no silently retained earlier result presented as the new assessment. |
| UX14 | Export results or ask chat about compliance | Same source/fidelity/unknown/requirement status; no stronger claim than the corresponding supported evidence. |
| UX15 | Use a read-only project or private customer trace | Native ownership/access rules preserved; no common-scope leak or new live-control authority. |
| UX16 | Navigate capacity→grid→reliability→controls and back | One project; one owner for every input; engineering dependencies shown without a mandatory new navigation tree. |
| UX17 | Open rapid-response assessment from Control Strategy, Grid and Reliability | One canonical result per variant and same request/job; secondary views link to the Control Strategy detail. Navigating does not submit a duplicate job. |
| UX18 | Complete a short response screen and interpret other domains | Grid retains before-mitigation readiness and scoped achieved-limit summary; Reliability distinguishes remaining backup/autonomy from uncomputed annual LOLE/EUE; planning shows no optimum effect from an unenforced suggestion. |
| UX19 | Change the parent operating point or cancel the assessment | Grid, Reliability and Control Strategy show the same stale/failed/cancelled receipt state. A grid-coupled study refuses missing/incompatible operating-point evidence; a standalone analytical screen clearly labels its declared baseline and excluded network claims. |

Use observed task success, misunderstanding of limitations, unassisted completion and navigation errors to assess the design. Numeric time/success targets should be set from a real baseline, not invented in this document. Existing component tests establish contract behavior only; they do not replace these user tasks or physical validation.

## Delivery order

1. Reconcile existing Guided/native editors for the workload and asset inputs actually needed. Preserve section ownership and existing run-readiness behavior.
2. Deliver the smallest trace/envelope/reserve screen under Control Strategy, with Grid/Reliability summaries linked to the same result. State precisely which operating point, limitations and planning constraints apply.
3. Add bounded native reserve/planning feedback only when independently validated and clearly previewed; compare the resulting design under the same assumptions.
4. Revisit a single external adapter only after representative user need, required data, project-state mapping and independent benchmark gates pass. Detailed electrical/thermal/measurement tools stay optional specialist capabilities.
