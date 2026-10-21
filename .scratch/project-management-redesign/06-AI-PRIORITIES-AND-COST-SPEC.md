# Three AI integrations with the best initial cost/usefulness trade-off

Status: proposed design and implementation priorities, not deployed features or measured ROI. Focused source assessment: actual master `87546ef5d184a9b20c80a65e2164f7fac0930f15`, 21 October 2026. The broader project-management audit remains pinned to `a68db6cfcf254df17a5b0d96da7a4c995f26423f`; its tests are historical evidence, not validation of these new features.

## Recommended order

Use one existing configured provider and one GridSpine companion initially. These are three task profiles, not three continuously running agents or mandatory model subscriptions. Native services remain the source for calculations, validation, navigation and effects.

| Rank / ID | Feature | Immediate user benefit | Marginal inference cost | Incremental engineering effort | Reason to start here |
| --- | --- | --- | --- | --- | --- |
| 1 / ROI-A | **Explain what to do next** — guided setup and assumption assistance | User understands a blocker/assumption and reaches the right field without searching the tool | Very low: native answer where sufficient; otherwise a short bounded explanation, no automatic optional second reviewer | Low–medium | Existing server flow verdicts, owning targets, acknowledgement rules, Ask/Delegate entry and same-section navigation already exist. |
| 2 / ROI-B | **Explain these results** — source-bound result and supported comparison narrative | User understands cost, performance, trade-offs and what remains unassessed in the view they are already using | Low: selected evidence packet, short answer; native facts rather than additional model calculations | Low–medium for current supported results; higher for new comparison/checked profiles | Existing typed result registry, native readers, source/freshness checks, retained evidence and native A/B computations provide most of the foundation. |
| 3 / ROI-C | **Suggest useful variants** — bounded sensitivity drafts | User gets a small, interpretable set of alternatives instead of manually cloning and editing cases | Low–moderate drafting cost; simulation compute is a separate cost and never automatic | Medium | Native case allowlist, isolated preflight, fingerprint/reuse and four-case sweep exist; the missing value is a safe draft/preview and concise rationale. |

Costs/effort are ordinal engineering judgments from reuse and scope, not measured dollars, timelines, conversion rates or latency benchmarks. Rank 1 helps users throughout setup; rank 2 makes existing outputs easier to use; rank 3 is valuable but can trigger expensive solves if poorly designed. This refines the earlier brainstorm: do not start by adding a second independent AI reviewer on every turn or a full multi-agent orchestration layer.

The first two are read-only help profiles. The third produces a draft only; existing native approval/admission governs creation/execution. Free-form chat remains available through the normal harness, but a bounded shortcut does not silently expand to that unrestricted-cost path when its scope is exhausted.

## What the code actually provides

| Source seam | Existing behavior | Addition / limit |
| --- | --- | --- |
| `services/flow_service.py:project_flow`, `routers/flow.py` | Canonical section state, blocking vs advisory items, target pointers, run_ready/run_blocker/run_request and value-bound acknowledgements | Build a bounded explainable packet; do not invent new Run gates or resolve unknown findings by prose. Flow read is for the open project. |
| `services/workflow_tools.py:project_readiness` | Validation/solver/dirty/evidence readiness for supported project kinds, including saved non-active targets | Not identical to the full ten-section Run verdict. Do not substitute its ready flag for the actual flow/native Run gate. |
| `pages/hubDesign/SectionView.tsx`, `shared/CardShell.tsx`, `delegate.ts`, `layout/flowNavigation.ts` | Readiness reason, in-place owning-field navigation, Ask/Delegate actions with contextual text | A typed context-bound help action, not another chat panel or duplicate parameter editor. |
| `services/result_views.py`, `services/workflow_tools.py:get_study_evidence`, `services/run_evidence.py` | Registry of native result reads, modeled limits, units, checked coverage and current/saved evidence contracts | Use the selected source/window and software-computed facts; no whole-project dump or guessed missing metrics. |
| `pages/CompareView.tsx`, native compare services | Rich A/B calculations and availability states | Narrative wrapper can use supported pair now; base+N remains gated by the PM09/S6 native service. No model-computed deltas/ranking. |
| `harness/reviewer.py`, `assurance.py` | Optional bounded read-only reviewer with deterministic checks authoritative; required checked interpretations can withhold when disabled/unavailable | Do not build another universal critic or bypass a configured mandatory review to meet cheap-response caps. Checked mode follows its own current policy. |
| `services/workflow_tools.py:SensitivityChange/SensitivityCase/run_sensitivity_sweep` | Six component classes, explicit field allowlist, finite typed values, saved baseline, 1–4 child cases, up to 20 changes/case, isolated validation and native queue | Existing tool creates AND queues; it is not a preview endpoint. Add a genuinely side-effect-free draft preparation path before offering the user a reviewable draft. |
| `harness/budget.py` | Global turn/session/tool limits and optional per-project daily token cap | Current defaults are broad: 8192 output tokens/turn, 25 tool calls/turn, daily cap disabled when configured as 0. New task caps and spend reporting are real additional work, not already guaranteed by these defaults. |

At the focused master, the Energy flow registry adds `energy-balance/1`: checked totals are bounded and validated per bus; Source→Via→Destination attribution rows remain outside that profile. It does not make every energy-flow statement checked. Reuse the actual descriptor/receipt rather than a hardcoded “all energy flow is checked” badge.

## ROI-A — Explain what to do next

**Entry:** Overview next-action card, section readiness/blocker row, or Ask about this beside an assumed input. Preserve the direct native Open here action: if a simple native message and link suffice, do not call a model just to repeat them.

**User journey:** user requests an explanation → native authorized read of current flow and selected item → short explanation of why it matters, the information needed and one owning-field action → user edits in the normal form. At most three prioritized actions, using native blocking order first. Wider assumption/design suggestions are labelled suggestions, not detected native errors.

**Input packet:** exact project/variant, revision, section/item/target IDs, native verdict/unknown state, selected scope, source/assumption basis and relevant display values. Limit to six relevant items; carry native counts/truncation. Do not attach all assets, customer files or full chat history merely because a user asks why an input is needed.

**Output:** short explanation referencing packet item IDs, unresolved assumptions and authorized next targets. The native UI still renders the authoritative verdict and values. The model cannot acknowledge a warning, clear an error, choose a reliability target or mark calibration complete through an explanation.

**Fallback:** show native issue text, counts and Open here if the model is unavailable, budget is exhausted or validation fails. No hidden alternate provider. A request to fix a value is a separate ordinary edit preview/confirmation, not part of this read-only help call.

**Code/test targets:** PM03/PM04/PM10. Proposed `services/assistant_help_packets.py` (bounded native projection), `harness/task_profiles.py` (common task-policy definition), `components/ContextualAssistAction.tsx` (single contextual trigger), existing flow readers/targets and normal provider-neutral dispatch. New `tests/test_assistant_setup_help.py` and `ContextualAssistAction.setup.test.tsx`; no new validator or duplicate readiness engine.

## ROI-B — Explain these results

**Entry:** selected result card/tab, a supported A/B comparison or a retained run. Label Explain these results; the request carries canonical source references rather than inferring them from the last chat message. The response opens in the same companion with source chips and links to the native data.

**User journey:** user asks → native source/ACL/freshness/basis check → bounded existing reader facts → explanation of up to three outcomes, important trade-offs and modeled limits → optional next-study suggestion. A why explanation distinguishes a recorded mechanism from a hypothesis requiring another sensitivity; correlation of an edited parameter and outcome is not a proof of causation.

**Input packet:** exact variant/run/window/period; at most 12 native fact blocks with units, money/time basis, availability, comparison status, claim limits and source IDs. Include thresholds/goals only when the native input/source establishes them. Missing, stale, incomplete or incompatible evidence remains unavailable; no automatic rerun/restore or current-result substitution.

**Output/publication:** fact values, deltas and ranks come from native calculations. The first bounded shortcut can render a native fact panel plus qualitative text whose references and claim schema are validated. Any model-supplied numeric claim must resolve exactly to admitted fact IDs and the appropriate current validation/publication adapter; otherwise withhold that claim/text. Do not assume number stripping alone establishes truthful prose or scientific validity. Qualitative interpretation limits and reference validation must be tested; a model reviewer is supplementary.

**Checking:** ordinary help says Native source / Not checked as applicable. If the user selected strict checking, use supported existing profiles and their required interpretation review. The capped cheap profile refuses/escalates explicitly when that policy cannot fit; it must not silently disable checking/review. Freshness, a mounted tab and model agreement confer no checked or compliance badge. General comparison claims stay outside checked coverage until an adapter admits them.

**Code/test targets:** PM06/PM10/PM12. Reuse result registry/readers and comparison services. Extend the packet service with a source-specific descriptor adapter and the existing native publication/checking path; no copied formulas or new per-domain numerical model. New `tests/test_assistant_result_explanation.py` and `ContextualAssistAction.results.test.tsx`; exact source/claim refusals via ordinary dispatch.

## ROI-C — Suggest useful variants

**Entry:** section 8 / the canonical Variants surface. Ask a specific objective or uncertainty, e.g. Compare storage-cost uncertainty while keeping demand unchanged. Offer a draft of two or three alternatives; hard cap four for the initial new-case workflow. Default at most two changed parameters/case, smaller than the existing 20-change technical ceiling.

**Input packet:** exact saved baseline/revision, selected objective/uncertainty, supported eligible scalar parameters with native units/basis/current values and meaningful user/source-supplied ranges. Suggestions must cite the source/rationale for a range. If none exists, ask the user for a bound or label explicit user-selected illustrative values; do not fabricate a market-derived ±20% default. Existing profile overrides and unsupported parameter families are visible.

**Draft output:** case label, purpose, canonical entity/field, current→proposed value, unit/basis, declared unchanged assumptions and an explanation of what the case can test. No benefit/cost/reliability result is predicted as fact. Ambiguous targets, horizons or intended existing-variant bulk edits route to the correct native preview/choice instead of a new-case sweep.

**Preview service:** refactor/reuse the isolated native preparation/validation logic into a side-effect-free preview function, with tests proving no project rows/files, checkpoints, jobs, saved acknowledgements or active-context changes. Do not invoke the current create-and-queue function in order to “preview”. Freeze baseline/preview digest and expire/recheck it at the ordinary admitted execution.

**Apply/run:** only after the existing reviewed composite confirmation, or separate native creation/run actions when that is the chosen supported contract. The UI states exactly how many copies and solves the action will create. Model inference budget, native preflight/solver CPU budget and job limits are separate. Existing-variant common-assumption updates remain PM07/PM08, not disguised as a sweep. The user can edit the draft manually with no further model generation.

**Code/test targets:** PM07/PM09/PM11/PM12 with an early preview-only increment. Proposed `services/sensitivity_preview.py`, native workflow_tools reuse and `VariantAssumptionsDrawer`/existing case-creation surface. New `tests/test_assistant_sensitivity_drafts.py`, `tests/test_sensitivity_preview_no_effects.py`, `SensitivityDraft.test.tsx`. Complete creation, group continuation and base+N comparison still depend on their native packages; do not call the draft the entire LC-17/18 implementation.

## Proposed request and presentation contract

One common authenticated task-profile request is proposed for these shortcuts, using the normal provider-neutral harness admission/event log rather than a direct model SDK call. Required fields: `profile` (ROI-A/B/C), canonical project/variant ID, exact selected item or result/baseline references, expected source/input/policy revision, explicit user question and idempotent request ID. Native code selects allowed readers/schema; a client cannot submit an arbitrary tool allowlist, prompt, filesystem path, provider credential or execution instruction.

Response metadata identifies task/request/source, native packet digest, profile/prompt/model-policy revision, native fact/target references, truncation, checking disposition, generation status and actual usage/cost availability. Text is separate from authoritative fact/target cards; links resolve only validated native IDs. The server validates source/actor again at publication and cache read. A mismatch/refusal includes a clear native fallback without substituting another project/source.

ROI-C additionally returns a native-validated **draft** descriptor and preview digest, with `executed=false` and no project/job IDs unless they are read-only existing references. A later ordinary action consumes the exact reviewed draft/expected baseline; no “preview” endpoint delegates to create-and-queue. Model-proposed labels/values are untrusted and need the same typed native validation as manual input.

Implement action availability from native source capability, not template-name promises. Unsupported detailed Control/Reliability/Commercial result readers display the limitation; do not enable a generic model answer merely to make every card appear supported. All three actions use the single companion; existing manual next-action, charts and case editors remain primary native controls.

## Shared inference and cost controls

Proposed initial controls, subject to offline fixture validation and actual supported provider policy:

| Profile | Task-specific evidence ceiling | Primary output ceiling | Primary inference rounds | Native read/preview attempts |
| --- | --- | --- | --- | --- |
| ROI-A | 8 KiB / 6 relevant items | 320 tokens | Target 1, hard cap 2 | ≤2 |
| ROI-B | 12 KiB / 12 fact blocks | 512 tokens | Target 1, hard cap 2 | ≤3 |
| ROI-C | 12 KiB / selected eligible fields only | 640 tokens | Target 1, hard cap 2 | ≤3; finite native preparation budget |

These are **new task caps**, not claims about existing configuration. Evidence-byte limits are not total input-token ceilings: include system/prompt/tool/history overhead in provider-specific pre-send accounting. One admitted native-prepared packet can make one generation sufficient; a normal tool-selection round plus final response may require two. Both must pass ordinary authentication, history/event logging, configured provider admission and budget gates. A task ceiling cannot weaken a stricter global/profile/user policy.

A required strict-review call is additional to primary rounds and needs its own explicit reserved budget; reuse the existing bounded reviewer where policy requires it. Do not silently skip it, add a second optional reviewer to every ordinary shortcut or advertise the task as one billable call when review/SDK retry adds calls. Preserve bounded existing transient retry semantics, pre-reserve the maximum permitted attempts, count all actual requests/usage and show unknown accounting as unavailable. Cap/timeout exhaustion returns the native fallback or explicit incomplete result, not an automatic larger-model retry.

No inference on hover, page load, each keystroke, every project edit or each job poll. Debounce/coalesce duplicate clicks by actor/project/action/source/selection/request digest. A fresh user-requested re-explanation may run again within budget; a reconnect must not silently resubmit generation or execution.

Private response reuse can avoid repeated inference for an identical question and unchanged source. Scope a cache to actor/org/project/variant, source/revision, question/selection, locale, prompt version, provider/model settings and checking/policy revision. Reauthorize/revalidate source on every read; expired/revoked/changed state cannot reuse it. Cache only admitted presentation, never approvals, mutation tokens or evidence of future action authority. Do not cache private answers in a common Library item.

Use normalized disjoint billable usage categories and the configured provider's price metadata to compute observed cost. Conceptually: sum(category_tokens × configured_category_rate) / 1,000,000. Cached-read/write pricing may differ; do not double count input categories or treat missing usage/rates as zero. No current public price, fixed dollar estimate, saving percentage or promised latency is asserted here. No paid benchmark calls were performed.

**Measurement before expanding:** count action uses, assisted task completion/owning-field resolution, repeated clarification/navigation, source/claim rejection, accepted/useful drafts, total billed tokens/cost when known, generation requests/retries, latency and native solves created. Compare against the existing native flow on representative tasks; raw acceptance rate is not proof of correctness. Define numeric release targets after an observed baseline; none are invented here.

## Delivery order and release boundaries

1. ROI-A read-only explanation alongside PM03/PM04/PM10; native reason/target first, minimal generation only when requested.
2. ROI-B source-bound explanation over currently supported results/A-B, alongside PM06/PM10/PM12. No requirement to finish shared writes, archive or full multi-agent orchestration first.
3. ROI-C preview-only drafts alongside PM07; admitted execution later through PM09/PM11 with current caps, ordinary approvals and native validation. No runtime safe-restore dependency for a read-only draft; any subsequent mutation still uses its native protection.

F01–F03 restore gaps remain open. These cheap read-only helpers must not pretend to fix or bypass them. New field/checked adapters and engine-facing semantics retain the existing review gates. Other specialist data ingestion, contractual interpretation, perpetual peer review, autonomous optimization and full report generation are deferred until the three initial features demonstrate value.

## Additional required acceptance cases

These supplement the 98 cases in the complete project-management plan. They are specifications, not implemented/passing tests.

| ID | Scenario | Required evidence |
| --- | --- | --- |
| RI01 | Setup explanation against real flow with error, advisory and unknown | Same native Run verdict/blocking order and owning targets; no invented blocker, acknowledgement or automatic edit. |
| RI02 | Simple native answer, no key/budget or one repeated click | Native reason/link works with no generation; eligible explicit help is coalesced and bounded; no provider fallback. |
| RI03 | Setup context changes between preparation and publication | Recheck exact project/section/revision; stale explanation withheld/recomputed only by explicit admitted request, wrong-target action refused. |
| RI04 | User asks to detect new assumptions not established by native checks | Label a suggestion or request missing input; no fabricated engineering defect/calibration fact. |
| RI05 | Setup cache read after role/policy/locale/question/source change | Reauthorize and invalidate/miss as appropriate; private cached output cannot become authority or leak across users/projects. |
| RI06 | Model setup response attempts an edit/tool or malformed/oversized claim | Read-only profile refuses the attempt or withholds invalid output; no changed model, acknowledgement or job. |
| RI07 | Explain current/retained/A-B source and window | Exact native units/basis/value/source; unavailable sections not zero, no current-result fallback or restore. |
| RI08 | Model injects a number, unsupported delta, ranking or stronger qualitative conclusion | Appropriate publication/schema/claim checks withhold unsupported claims; independent fixtures, no model self-approval. |
| RI09 | Strict checking requires unsupported profile or mandatory review exceeds cap | Explicit scoped refusal/incomplete state; no checking downgrade, omitted mandatory reviewer or unchecked compliance badge. |
| RI10 | Focused master energy-balance totals versus attribution rows | Reuse actual registry/profile coverage; totals do not certify Source→Via→Destination rows or every Energy flow statement. |
| RI11 | Usage/rate missing, cached-token categories or bounded retry | Correct disjoint accounting or unavailable, never zero; full request/retry/reviewer cost included and caps reserved. |
| RI12 | Evidence explanation fails/times out or source moves during generation | No optimistic/certified prose; native facts/limits remain, no automatic rerun, extra reviewer or larger-model fallback. |
| RI13 | Draft 2–3 sensitivities on an exact saved baseline | Small interpretable native-eligible parameter changes and sourced/user-selected bounds; no guessed ±20% or invented benefits. |
| RI14 | Side-effect-free preview and concurrent baseline edit | No rows/files/checkpoints/jobs/active-state mutation; stale digest refuses later execution; isolated native validation genuine. |
| RI15 | Prompt asks for eight cases, unsupported tariff/profile edit or existing-variant common update | Explain actual admitted cap/family; use correct shared-edit path or refuse, no silent four-case-cap bypass. |
| RI16 | User revises a draft manually or denies execution | Same native typed preview; no new model call required, no project/solve effect without the ordinary concrete confirmation. |
| RI17 | Draft key/range/units/series mapping invalid or malicious text embedded | Native schema/allowlist/unit/effective-value rejection; untrusted text cannot grant instructions or hidden writes. |
| RI18 | Regenerate/double-click/reconnect or provider unavailable | Bounded idempotent request/presentation; no duplicate cases/jobs or hidden generation; approved native execution remains distinct and source-bound. |
