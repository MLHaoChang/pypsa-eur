# GridSpine three-section guided workspace

Status: ready-for-agent — implementation specification; production integration and native separability gate remain open

Date: 2026-10-20. This revision simplifies the three-section design to nine user-facing choices. Capacity planning includes dispatch and economics; grid-code assessment includes its required load-flow work. Detailed process and reliability assessments remain optional.

## Agreed direction

- Normal sign-in opens a workspace overview with explicit Resume.
- Start a guided workflow directly. No “Start with a question” entry or forced chatbot intake.
- An example and a generic/own-model start converge on the same workflow builder.
- The user selects activities inside three large sections: Energy & system planning; Grid & electrical design; Controls & dynamic validation. A standalone fourth Reliability section is not recommended for the primary navigation. Reliability remains a shared design objective with domain-specific assessments.
- The user selects the analyses to connect. Capacity expansion can stand alone, without load flow; reliability can be integrated without adding irrelevant screens.
- Each selected stage presents relevant inputs, related Advanced settings, validation, native actions and results. Unselected analyses do not appear as required steps or completed checks.
- Expert and the same GridSpine companion remain available throughout, including during workflow composition. The companion can propose detailed parameter changes through the existing harness; it does not bypass review or edit unsupported fields.
- Use a brighter red action accent on cool white/steel surfaces, with a graphite/navy dark theme as the preview default. Dark text on the bright primary fill preserves contrast; darker red serves links and small text. Keep Manrope, GridSpine naming and the dimensional companion. The previous sage palette is superseded for this design. Semantic failure/warning/success indicators and scientific color scales keep their meanings.

## Recommendation: two equal starts, one builder

Offer “Build your own workflow” alongside examples. Examples are convenient model presets, not separate applications or hardcoded end-to-end pipelines.

1. **Example first:** choose a data center, island microgrid or hydrogen hub, with a single-period or staged-growth variant → review/edit suggested analyses → enter the guided workspace.
2. **Own model first:** choose generic model/import → select analyses → choose or create the model → enter the same guided workspace.
3. **Scope first remains possible:** choose analyses in the builder, then choose/change the example or own model there. Changing the starting model preserves the chosen scope and rechecks its eligibility.

An example may suggest a chain, but the user can remove any optional analysis. New examples/templates must come from the canonical bundled catalogue, including hydrogen and staged-growth variants, rather than a second manually maintained production list. Suggested scopes need a versioned mapping onto real template identifiers; they must never certify eligibility, source compatibility or engineering success.

### Backend separability is an open implementation gate

Before wiring native execution, complete `backend-separability-assessment.md`. Determine whether each visible activity is separately triggerable, requires a shared solve/source, is inseparably bundled, or is unsupported. Preserve native physical coupling and required stages even when an optional detailed assessment is unselected. Adjust the UI to the proven backend contract rather than weakening backend checks to fit the design.

Initial source reconnaissance found a separately triggered AC-PF stage that still requires solved dispatch and changes shared results; a campus design/MILP path that can perform load-flow/least-cost work; and a hub orchestrator with mandatory pack/ENS stages. These are coupling clues, not completed runtime verification. Native activity independence remains unapproved.

### Example presets and selection controls

Presets initialize an editable draft, not execution or permissions. Proposed default scopes are:

| Example | Preselected scope | Remains an optional addition / qualification |
| --- | --- | --- |
| Data center (`eh_datacenter`) | Capacity, reliability, infrastructure sizing, grid-code/load-flow work, controls | Island transition/recovery, detailed dynamics, contingencies and process detail according to the actual site. Control/electrical setup still needs native eligibility. |
| Island microgrid (`eh_microgrid`) | Capacity, reliability, grid-code/load-flow work, controls, island operation | Infrastructure redesign, contingencies and detailed dynamics when required; a screening preset is not proof of stable transitions. |
| Industrial hydrogen (`eh_h2_hub`) | Capacity and detailed process modeling | Reliability and electrical/control/dynamic studies when project requirements justify them. Detailed process setup must be verified, not inferred from the example name. |
| Generic energy hub / own model | User chooses the scope, with capacity planning as a possible starting point | Do not infer detailed network/control needs just from “hub.” No distinct generic bundled template is invented. |
| Mining/process plant | Proposed design preset: capacity plus detailed process work; reliability according to criticality/data | No mining template is currently shown as bundled in the inspected catalogue. Confirm/build a real template before offering a runnable example. Grid/drive/control needs remain site-specific. |
| Ammonia plant (staged), #418 | Proposed draft: capacity plus detailed process modeling | In development and not merged, according to the owner. Targets: 50, 100, 150 t/day NH₃. Do not offer a bundled/runnable template until native integration lands. |

These are proposed UX recommendations, not domain-reviewed defaults or blanket rules about sectors. Hydrogen production and mining may need grid/control assessments too. Staged-growth variants inherit the corresponding base scope through explicit template IDs, with period/source validation; never use fuzzy name matching to grant capabilities.

Opening an example applies its recommended selection. Changing the source of an already-edited draft preserves the selection; **Use example preset** explicitly restores the recommendation for the newly chosen example. It changes the selected activities only, not files, field values, job state or approvals. Resume restores the saved edited scope rather than resetting to the preset.

Add global **Select all** beside **Clear selection**, while retaining per-section checkboxes and their partial state. Select all selects all offered, enabled activities in the current draft and is idempotent. Production must distinguish eligible, setup-required, unsupported and unknown activities: setup-required work may be selected for planning, but unsupported/disabled work must not be silently enabled. The prototype offers all nine as draft choices, not as certified runnable features. Neither Select all nor a preset schedules calculations, changes models, changes role/mode or bypasses validation.

## Deliverable

`GridSpine-modern.html` is the updated portable preview. Only this latest generated HTML is included in the published package. CSS, JavaScript, the bright-red logo, five original industrial illustrations, Manrope font and its OFL license are embedded; no neighboring files are needed to view the artifact. A styled static overview remains if script execution is disabled. Interactive controls require JavaScript.

Editable sources: `template.html`, `styles.css`, `prototype.js`, `sections-preview.js` and `expert-preview.js`; regenerate with `build-preview.mjs`. Browser checks exercise only the design, not the real application or scientific calculations.

All models, parameter values and drawings are illustrative. No API, paid inference, model mutation, approval, simulation or report generation occurs. The production branding work already present in this worktree remains untouched by this design revision.

Inspected implementation baseline: local `/workspace/gridspine-design`, HEAD `8119972cebf1c50d4d8caaed086c4d8a28dd616f`, plus preceding uncommitted branding changes. This is not a claim of fresh remote-master synchronization.

## Screen 1 — Workspace overview

Header: GridSpine, workspace/account identity and role-appropriate account actions. Sidebar: Overview, Examples, My projects. No modeling palette or duplicate Assistant button.

Main hierarchy:

1. “Start a guided workflow.” A single explanation of starting models and selectable analyses.
2. Compact Resume for the last accessible project and its saved composition/location. Missing/inaccessible/deleted projects do not offer a dead-end Resume; running activity stays visible.
3. “Build your own workflow”: generic model, native import or an example chosen later.
4. Existing example models, each offering “Customize workflow.” Three representative cases initially; the full catalogue is available under Examples.
5. Expert entry for manual modeling, through a project/model chooser when no project is selected. No implicit blank project or overwrite.

Avoid duplicated New/Blank/Import/Clone bands. These variants belong to the model chooser and My projects. Preserve authorized admin access, authenticated/local differences, legacy project claiming and local folder migration in appropriate utilities. Normal sign-in goes to overview, while validated explicit authenticated return links still reach the intended project. Do not globally replace `getPostLoginPath`: it also builds explicit project links.

## Screen 2 — Workflow builder

A model selector and selectable analysis cards sit beside a live composition summary. Shared model inputs and final review/results stay present; the user selects at least one analysis. Empty selection cannot enter a misleading runnable workflow. Expert access is still available.

### Three main work sections

| Section | Scope | Reliability contribution |
| --- | --- | --- |
| 1. Energy & system planning | Multi-carrier demand and energy conversion, energy-asset capacity expansion, economic dispatch, supported production-process coupling, cost/value and adequacy planning | Define the cost–reliability objective here. Compare cost and risk or minimize cost subject to explicit service targets, under declared outage/availability assumptions. |
| 2. Grid & electrical design | Accompanying electrical infrastructure sizing/parameterization, topology and load flow, supported steady-state grid-code requirements, contingencies and redundancy | Validate network security/resilience and identify electrical bottlenecks or redundancy needs. Feed their implications back into energy/equipment sizing. |
| 3. Controls & dynamic validation | Supported control strategies, island operation and recovery assumptions, frequency/dynamic assessment and specialist-tool handoff | Assess operational security, transition stability and recovery under explicit disturbance assumptions; feed reserve/control/dynamic requirements back into the design. |

The first section owns the planning objective; the second and third test different conditions under which that objective may fail. They do not simply repeat the same reliability optimization. A study may iterate: sizing → electrical checks → control/dynamic checks → reviewed design changes → renewed sizing/checks. Reuse the same model and source bindings, but never advance this loop automatically without native approvals, limits and durable receipts.

Use three large cards, each with selectable subordinate activities. Section checkboxes select/clear that section's activities and have a native indeterminate state when partly selected. Do not force all activities just because a section is included. The guided rail groups selected activities under these same three names; shared Model, Review and Results are workspace utilities rather than extra work domains.

Configure the planning objective inside the optional Reliability assessment stage, not through another global setup-page selector. It offers cost–reliability comparison, cost minimization subject to explicit reliability targets, or an economic baseline with reliability unestablished. Selecting a goal neither enables an unsupported optimizer nor establishes a target was achieved. Without a selected/eligible adequacy method, capacity sizing cannot claim a cost–reliability result.

### What reliability means here

- **Planning adequacy/continuity:** relevant energy-not-served, outage duration/availability or other method-specific service targets, with critical demand, horizon, scenario basis and confidence limits. Electrical LOLE is not automatically a valid metric for heat, hydrogen or production delivery.
- **Network security/resilience:** supported deterministic contingency cases, electrical limits and equipment redundancy. An N-1 screen does not itself establish probabilistic annual adequacy.
- **Dynamic/operational security:** supported voltage/frequency behavior, island transition, reserve, grid-forming capability, recovery and resynchronization. Adequate installed MW/MWh does not prove a stable transition or black start.

Do not collapse these into one generic reliability score or average pass percentage. A unified report shows the planning target and each assessment's source, method, scope, outcome and unassessed requirements. Steady-state grid-code screening is not dynamic ride-through validation or a compliance certificate.

### Coupled processes

Coupled process modeling is an explicit activity within section 1. Reuse the native process schema and product/inventory relationships. The current domain design distinguishes fixed client-specified process-unit size from the throughput/operating decisions and optimized energy capacities. Do not make process-unit capacity an investment variable merely because the activity is selected.

Production targets, energy demand and inventory need product/carrier-specific units. MWh of electricity, MWh LHV and tonnes cannot be mixed. Lost-production value is not interchangeable with electrical value-of-lost-load; missing valuations stay unavailable. Support and eligibility are adapter-specific, not established by this preview's card.

### Simplified selection catalogue

Exactly three choices appear in each section:

| Section | Choice | Included work / optional detail |
| --- | --- | --- |
| Energy & system planning | Capacity expansion | Energy-asset sizing, economic dispatch and investment/operating costs as one core planning activity; expose demand, carriers, profiles, horizon, bounds and economic assumptions in its forms. |
| Energy & system planning | Detailed process modeling | Optional supported process throughput, energy coupling, product inventory and production targets. Preserve product units and fixed process-unit sizing rules. |
| Energy & system planning | Reliability assessment | Optional adequacy and cost–reliability work, with the target/method/outage controls configured within this stage. |
| Grid & electrical design | Electrical infrastructure sizing | Supported equipment design/parameterization and its native electrical prerequisites. |
| Grid & electrical design | Grid-code assessment | Required load-flow calculations and loading/voltage evidence are included in this choice; supported clauses, source and remaining dynamic requirements stay explicit. |
| Grid & electrical design | Contingencies & resilience | Explicit supported outage/security cases and redundancy assessment; distinct from probabilistic adequacy. |
| Controls & dynamic validation | Control strategies | Supported equipment/control settings, objectives and checks. |
| Controls & dynamic validation | Islanding & recovery | Supported islanded-operation, reserve and recovery assumptions; transitions require appropriate dynamic evidence. |
| Controls & dynamic validation | Frequency & time domain | Supported screening and external simulator handoff/read-back; general orchestration is additional backend work. |

There are no separate Dispatch, Economics/investment or Load-flow checkboxes, rail activities or independent summary cards in the guided composition. Their native forms/charts/results remain accessible inside the owning activity and directly in Expert. Detailed commercial/decision studies retain their own eligibility; the desktop-only decision flow must not make ordinary capacity optimization desktop-only or imply that all commercial analyses are included in its cost output.

The nine choices simplify navigation, not the execution contract. Review must disclose required bundled calculations, their source and effects. Each native action still receives applicable validation, admission and approval; do not treat the parent checkbox as permission to run substeps. Shared exact compatible solves/evidence should be reused rather than duplicated. Separate incompatible sources/clauses remain separate and unavailable when needed.

These are subordinate activities of one project-scoped composition under three work sections, not disconnected application entry points. Native features exist in differing forms; the generic connected shell and adapters are new integration work.

### Selection and connection rules

- One selected activity is valid: capacity planning alone; grid-code work using a compatible existing operating source; or reliability using an eligible existing plan and required data. Choosing an optional assessment does not silently force a new capacity solve.
- Multiple stages share a model and explicit source bindings. The visual order is a reasonable work order; it is not proof that every adjacent pair is a data dependency.
- Production plans form a validated acyclic dependency graph with typed input/output references. Common inputs may feed branches (e.g. adequacy and network studies); dependency edges are established by the actual adapter, not by card position.
- Missing prerequisites offer explicit choices: provide data, select compatible existing evidence, add the relevant activity, or remove the dependent activity. The normal included work of a chosen activity is disclosed in review; unrelated work must never be silently inserted or mode switched.
- Unsupported/configuration-required stages may remain in a draft so the user can see what is missing. They are not runnable and cannot pass final admission until resolved.
- Example defaults are suggestions. Changing source in the builder preserves the selected analyses but invalidates source-dependent eligibility and results.
- Unselecting a stage removes it from future work. It does not cancel a job, delete evidence or undo applied changes. Identify dependent stages and ask the user to choose a replacement source or remove them; do not silently drop those dependents.
- Adding/removing a stage while a run is active is a draft revision; freeze the admitted execution plan. No mutation of an active job's meaning.
- Selection/typing/navigation never authorizes a run. Retain separate native admissions, confirmations, cancellation and durable effect receipts.

The prototype demonstrates three sections with nine selectable activities, full/partial section selection, deferred reliability targets, bundled capacity/dispatch/economic detail, bundled grid-code/load-flow detail, process coupling and contingency/islanding scope, example suggestions, source changes, grouped presentation, conditional prerequisite text and selected-only views. It does not implement the dependency validator or orchestration backend.

## Screen 3 — Guided execution workspace

One rail: Model & inputs → selected activities grouped under the three work sections → Review & run → Results & report. Detailed adapters may need their own inner forms, but do not add a competing rail for the same workflow. Existing Energy Hub and decision flows keep meaningful native substeps; their integration must not misrepresent native completion states.

A stage exposes its relevant GUI, shared/source context, related Advanced settings and contextual parameterization help. The full asset catalogue stays in Expert. “Advanced for this stage” does not change mode or permissions; native schema supplies field names, units, ranges and defaults. Prototype values are not prescribed scientific defaults.

Every stage must distinguish editing, validated, awaiting approval, queued, running, completed, failed, cancelled, partial, stale, unavailable and unresolved-effect states as applicable. Advancing a UI rail does not set a stage to validated or completed. A read-only role may browse and inspect without acquiring write authority.

The final review lists exact changes, selected calculations, sources, solver/simulator setup, bounds and unresolved issues. The ordinary native harness/admission contract decides what may execute. No custom second execution engine or hidden approval that arms an entire graph.

Results show the selected analyses and their own states, source identifiers, units, methods, time windows, warnings and limitations. Unselected load flow is “outside scope,” not passed; unsolved/failed/partial reliability cannot yield a positive reliability conclusion. Current and retained evidence remain distinct. Comparisons/reports require compatible native sources; no chat-derived numeric claims or invented missing results.

The selected native result tabs must be allowed within the owning guided activity; do not silently switch to Expert to access Capacity, Dispatch, Economics or Load Flow. Load Flow is a detail of the selected grid-code work in the simplified guided flow, not evidence that all grid-code requirements passed. Runtime visibility is not authorization. Pending work and approvals remain reachable even when the launch panel is hidden.

## Expert and companion throughout

At every stage, Expert opens the same project for detailed native parameterization. Returning restores composition, current stage, draft and conversation; native input/source changes revalidate affected steps and stale outputs. Explicit mode preferences and legacy project migrations need preservation.

The single bottom-right companion knows the active project, composition and selected stage through untrusted UI identifiers. Tools re-read authorized model/evidence. It may discover supported fields, explain missing settings and prepare exact proposed changes with unit/type information. Normal validation, permissions, write locks, confirmations, checked-answer selection and effect receipts remain authoritative. Unsupported fields/actions receive a truthful limitation, not invented edits or a pretend successful run.

Contextual “Help parameterize this stage” opens the same companion and prepares a draft. It does not send automatically. No duplicate sidebar Assistant, second engine or unreviewed summary endpoint. Closing chat retains drafts, streaming and decisions; Escape restores launcher focus. The closed mobile launcher has its own tray below the scrolling workspace so warnings/actions remain visible. Continuous live voice retains its existing readiness limit.

The graphite/navy dark theme and cool-neutral light theme use bright red primary actions, a light red dark-theme link color and a darker red light-theme link color. Red-tinted branding must stay distinguishable from actual danger states through semantic colors plus labels/icons, and must not recolor scientific voltage/carrier scales.

## Integrated planning entry — latest owner decision

This decision supersedes the earlier single-period/staged-growth buttons. Each industry has one example card. Normal click, right-click (context-menu activation on that card only), keyboard Enter/Space or tap goes directly to workflow setup; do not add a variant chooser or confirmation screen.

The next phase has a **Planning period** with start/end year, retaining the existing editable activity scope. Optional investment dates belong under advanced planning settings. A same-year horizon can represent a point study; a multi-year horizon can require multiple native investment periods, with dates and growth inputs configured for the project. Users do not have to choose a “single period” or “staged” product mode.

Keep horizon/period inputs project/revision-owned. Validate whole-year bounds, end >= start, date uniqueness/order/range, horizon weights, period costs/discounting, staged asset/load/connection schedules and native capabilities. Missing growth inputs stay missing; do not interpolate the data-center 12/24/36 MW values or invent utility connection limits for arbitrary years. The existing template variants are reusable native assets, not duplicate public examples or automatic conversion authority. Current drafts must not change existing saved projects or running plans without ordinary validation and approvals.

Preview defaults are illustrative years 2027–2035. Native implementation must reconcile the latest Model Horizon and project-flow contracts and template/simulator eligibility. The preview does not configure the application or solve a model. Both pointer buttons enter setup only; right-click never creates/runs a project, starts capture or approves an operation. Scope recommendations remain editable and all nine activities still need the backend separability gate.

## Companion modes and dimensional animation

The owner requests both a design preview and application implementation. Hover or keyboard focus on the single companion exposes **Write**, **Dictate** and **Live chat**. Touch users open the same choices by tapping the character. All modes reuse the mounted assistant/session and ordinary harness admission; no second conversation engine or duplicate sidebar entry.

- Write opens the native composer and focuses the retained draft.
- Dictate opens reviewed dictation. Hover, focus or selecting the mode does not itself request microphone access, record or submit text; use its explicit recording control and native consent/readiness/error states.
- Live chat opens the existing in-page text conversation. Continuous voice is currently unavailable and must remain clearly labeled. Do not describe an open text panel or a decorative animation as listening, recording, speaking or connected voice.
- The dimensional character gives a short greeting/tilt on hover or focus. In-page chat uses a separate restrained orbit/breathing presentation; response work remains bound to actual work state. Approval/input waiting, errors and queued work retain their accurate labels. A decorative loop cannot certify a job, model result or media state.
- Reduced-motion preference disables animation/transitions while keeping mode/state changes legible. All options work by keyboard/tap with explicit labels, visible focus, adequate targets, Escape/outside dismissal and focus restoration. Closing a menu never cancels running work or changes an approval.
- The local preview shows mode/animation demonstrations only; it captures no audio, connects no voice/model and starts no work. It retains the draft when switching modes.

Acceptance: hover/focus/tap reveal, pointer travel into choices, outside/Escape dismissal, callbacks exactly once, no automatic capture/send, retained draft, actual live-open vs running vs queued/approval/failed labels, continuous-voice unavailable explanation, reduced-motion startup/change, mobile/menu positioning, and native AssistantDock integration parity.

## Industry library and model-input presets — latest owner revision

Use original dimensional industry illustrations, cooler graphite/steel surfaces, thin borders and a bright red accent. The supplied screenshot informs the image-led library approach only; none of its imagery, meeting content or branding is copied. The prototype contains five original, locally generated vector dioramas with all media embedded. These are illustrative scenes, not exact asset inventories or engineering readouts.

Show one card per industry, with integrated planning years selected in the next phase. Normal click, right-click or keyboard activation goes directly to workflow setup. Do not expose single-period/staged-growth as separate example choices. The six existing native template identifiers below are implementation details; native source/project conversion still requires explicit, validated binding and cannot reuse incompatible evidence.

| Card / variant | Template identifier | Owner-supplied scenario basis |
| --- | --- | --- |
| Data center, single period | `eh_datacenter` | 50 MW campus, weak 40 MW connection, critical IT on UPS, gas gensets and rooftop PV; gensets/battery expansion. Optional AI-training profile and dynamic-load envelope referenced in DC 01 / #421. |
| Data center, staged | `eh_datacenter_staged` | 2027 / 2030 / 2035; IT 12 / 24 / 36 MW; staged utility energization. Do not equate IT demand to the single-period total campus peak. |
| Hydrogen hub, single period | `eh_h2_hub` | 120 MW wind, 80 MW PV, 60 MW electrolyser, H₂ storage, fuel cell, site battery, 40 MW critical process load and 25 MW H₂ offtake. Preserve electrical/product energy bases. |
| Hydrogen hub, staged | `eh_h2_hub_staged` | The hub over 2027 / 2030 / 2035; use actual native period-specific values rather than inventing growth quantities. |
| Island microgrid, single period | `eh_microgrid` | Operate without a 20 MW subsea tie; PV, wind, battery, three diesel units, critical hospital and residential demand. |
| Microgrid, staged | `eh_microgrid_staged` | Three-period island system across 2027 / 2030 / 2035; native period-specific inputs remain authoritative. |
| Ammonia, in development | No invented native template ID; local `concept_ammonia` is preview-only | #418 is not merged according to the owner. Electricity/H₂ → NH₃, staged 50 / 100 / 150 t/day. First grid-to-process template. |
| Mining, planned after ammonia | No invented native template ID; local `concept_mining` is preview-only | Proposed energy/process workflow. Actual mining assets, load/production basis and backend template remain to be implemented. |

This scenario inventory records the owner's supplied context; it is not a fresh independent verification of GitHub issue status or current runtime capabilities. Production integration must reconcile the current canonical template catalogue, merged source, role/install eligibility and actual parameter/evidence schemas. The preview does not implement DC 01, staged investment, ammonia or mining solvers. Upcoming examples open draft layouts with clear template-setup notices, never native creation/run calls.

Remove IEEE, Belgian and small tutorial grids from this focused onboarding gallery and source picker. Preserve the underlying native templates, imports and existing projects; this is not a backend deletion request.

Within **Model & inputs**, offer shared scenario options, separate from the three workflow sections:

- Baseline, heatwave (cooling peak plus PV derating), dark/still week (Dunkelflaute), and low-wind week. Keep profiles, severity, seed/horizon, energy conservation and applicable affected assets explicit in native implementation. Revalidate and invalidate stale evidence after changes; do not invent numeric stress factors from the label.
- Template load profiles, synthetic commercial-office shape, or synthetic two-shift industrial shape. Synthetic shapes are made up, not measured. Scale using the site's annual MWh and the native snapshot weighting/horizon; validate finite non-negative demand and a positive normalization integral. Treat each supported carrier and annual energy basis explicitly.
- Preview condition drafts are per source and survive Expert round-trips; they neither create profiles nor change a model. Production persistence is project/revision-owned and must use ordinary validated effects and receipts. A new source cannot silently inherit another source's synthetic/stress parameters or validation.

Additional acceptance: one example per industry; normal click/right-click/keyboard direct entry; retained per-example start/end year and optional investment dates; invalid ranges/date schedules blocked before execution; correct underlying native ID and period/IT basis; no silent existing-project conversion; staged costs/discounting/weights and source compatibility; ammonia unmerged and mining planned notices across builder/guided/Expert/results paths; stress combinations and unsupported sites truthful; synthetic annual-MWh normalization, measured/synthetic provenance, input boundaries and stale-result invalidation; all five embedded images load in normal and script-disabled viewing; both themes remain legible and use the single companion.

## Expert navigation map

| Section | Native surfaces retained |
| --- | --- |
| Model & inputs | Assets/Properties/tables; profiles; time series; model horizon; economics; import/export |
| Planning & investment | Capacity bounds; capacity expansion; dispatch; desktop-eligible decision study |
| Network analysis | Load-flow results; campus electrical; project-eligible planning → dynamics |
| Reliability | Native adequacy, outage/risk inputs and FMEA; no invented separate engine |
| Controls & dynamics | Supported frequency/island checks and control parameters; simulator handoff/read-back; generic unsupported tools labeled |
| Results & deliverables | Summary/overview, prices, economic results, emissions, curtailment, lost load, storage cycling, investment case, asset detail, compatible run comparisons and reports |
| Run & validation | Solver settings, Issues with actual unknown/error state, solve queue with live activity |
| Project utilities | Save/save-as/rename/autosave, scenarios, snapshots, history, native IO, Home |

All old destinations need a parity mapping, including map/site/3D/connect/zoom controls and bottom tables. One navigation definition should drive expanded/icon/search/mobile presentations; keep existing panel identifiers and slide-panel architecture. “All tools” remains subject to authorization, project-kind, installation and data/simulator readiness.

## State and backend integration

If older composition IDs exist, preserve their intent during migration: do not silently turn dispatch-only work into a new sizing solve or load-flow evidence into grid-code compliance evidence. Require an explicit reviewed scope binding when meanings differ.

Reuse `uiStore`, native project identity, project-scoped Query keys, current workflow stores and the mounted ChatPanel/AssistantDock. Production persistence needs a versioned project-owned composition with selected stage IDs, adapter versions, explicit dependency/source bindings, draft revision and resumable UI location. Do not put engineering truth or approvals into browser storage.

Keep composition drafts separate from admitted execution checkpoints. Frozen execution binds exact project/owner/input revisions, job/effect receipts and per-stage outcomes. Invalidated downstream stages cannot reuse stale upstream receipts; identical-looking later jobs are not interchangeable sources. Interrupted/uncertain effects stay unresolved until the ordinary native reconciliation proves their state.

Stage interfaces should resolve eligible native forms and operations, validate prerequisites, describe required source contracts and expose evidence-bound outcomes. Reuse actual native capability/ACL checks; a cached chatbot search or a checked checkbox grants no authority. No speculative universal controller adapter or arbitrary workflow generator.

## Implementation sequence

0. **Backend separability gate.** Complete the native activity/dependency matrix and focused tests in `backend-separability-assessment.md` before admitting execution for the composed workflow.
1. **Overview and assistant consolidation.** New example/generic entry and explicit Resume; normal-login behavior; preserve role/local/legacy utilities; one mounted companion. No calculations needed for browsing.
2. **Expert regrouping.** Native destination parity, search, compact/mobile navigation and truthful conditional availability; no duplicate editors.
3. **Composition draft and guided shell.** Nine choices in three sections, with included substeps rather than redundant peer choices. One project-owned selection; example suggestions; typed dependency/source validation; stable stage IDs; source/mode round-trip; selected-only screens. Preserve existing hub/decision subflows rather than claiming universal integration.
4. **First vertical slice: capacity alone, with explicit reliability status.** Shared model → native demand/profiles/economic assumptions/capacity bounds → validation → ordinary admitted planning solve → source-bound sizing, dispatch and cost results/report. Prove no grid-code/load-flow activity is invoked when not selected; document and separately admit the calculations included within selected grid-code work. Treat reliability as unestablished unless the selected native adequacy/cost method has run and its target evidence is available.
5. **Connect network and reliability.** Add optional native adapters, existing-source branches and compatible shared outputs. Test capacity+network, capacity+reliability and all three. No universal “reliability needs load flow” rule.
6. **Bundled detail and evidence parity.** Dispatch and economics are part of capacity planning from its first vertical slice, with native charts/forms/evidence. Grid-code work includes required load-flow detail. Reuse compatible sources without duplicate solves; preserve horizon/weights/cost basis and separate desktop-only decision constraints.
7. **Controls/dynamics eligibility and specialized adapters.** Connect only supported settings/checks/handoff; separately specify missing generic controller/time-domain backend work and simulator execution evidence.
8. **Native owner journeys and release gates.** Frontend suites/build, authenticated native HTTP/harness checks, relevant scientific oracles, accessibility, both source-review axes, fresh-master reassessment and required current-head CI before any PR/merge.

No production code was changed for this composition prototype. Prior branding checks do not certify these future integrations. Publishing this documentation does not authorize or certify native execution; follow the implementation and review gates for later code PRs. No paid model/media calls, deployments, reviewer contact or full-domain-approval claim. Independent domain review and real-use calibration remain pending.

## Detailed implementation tests

| Test family | Acceptance |
| --- | --- |
| Entry paths | Normal login → overview; explicit authorized deep link; Resume restores exact project/composition/location; missing/foreign/deleted project stays unavailable |
| Presets/bulk selection | Data center/microgrid electrical-control recommendations vs hydrogen/process recommendations; editable choices; explicit preset reset; source changes preserve edits; Resume preserves edited scope; global Select all/Clear, repeated use, group partial states, unsupported/disabled/setup-required eligibility and zero implicit execution |
| Backend separability | Every activity classified with native prerequisites, mandatory internal work and focused execution/evidence tests; no UI-only claim of independence |
| Example/generic convergence | Example suggestions editable; generic starts unselected; stage-first then source selection; canonical catalogue/availability; changing source retains scope, revalidates eligibility and does not leak another project's drafts |
| Grouping/objectives | Exactly three work domains and nine choices; no separate dispatch/economics/load-flow choices; parent/full/partial selection; reliability target controls only inside the optional assessment; selected activities keep section grouping |
| Process/grid/controls | Supported product units and fixed process-unit sizing; source-bound electrical sizing; contingency vs adequacy distinction; code/version/clauses; island-transition vs adequacy distinction; source-compatible feedback into sizing |
| Composition | Core planning with dispatch/cost detail included; grid-code work with load-flow detail included and existing source; reliability-only with eligible existing plan; mixed subsets; empty selection; unsupported stage; add/remove/re-add with inputs retained but no inherited validation |
| Dependency graph | Native prerequisites vs superficial order; branches; cycles rejected; compatible source reuse; missing source; horizon/weight/unit/currency mismatch; changed adapter version; no silently inserted stage or run |
| Scope changes | Removing an upstream stage exposes dependent requirements; active plan frozen; no implicit cancel, undo, evidence deletion, retry or reapproval; edited drafts cannot alter an active job |
| Stage inputs | Relevant GUI only; schema-bound types/ranges/units; advanced values survive toggle; shared fields have one owner; manual or companion changes invoke the same validation |
| Native execution | Applicable confirmations/denials, native locks, queued/running/cancelled/failed/partial/infeasible work, stale source and uncertain effects; exact durable receipts bind each stage; skip/failure cannot become success |
| Results/report | Selected-only views; unselected grid-code/load-flow work explicitly outside scope; source and method visible; no missing-result interpolation; dependency failures propagate unavailable, not fabricated data; verified download/export |
| Companion | One owner and persistent launcher; stage/composition context; help prepares draft; supported field discovery; exact reviewed parameter changes; no send/apply on opening; draft/approval/streaming persist across modes |
| Mode/source persistence | Same project and rail on return; Expert edits invalidate dependent results; changed source never reuses old value/evidence claims; deep links/tours do not expose unrelated panels |
| Permissions/environment | Read-only/expired/changed role, lost ACL, desktop-only decision, missing simulator/configuration, unsupported control schema; UI visibility never substitutes for admission |
| Accessibility/layout | All four screens, light/dark, mobile/desktop/zoom, keyboard focus and checkbox semantics, reduced motion, semantic contrast; closed companion never covers warnings/actions |
| Portable artifact | Only bundled HTML available; embedded industry illustrations/logo/font/CSS/JS, all interactions and static no-script fallback; no external/API/model requests |

The present browser checks cover only composition-preview journeys. Native execution and scientific acceptance remain implementation work.
