<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# HH-09 — Bounded coordinator delegation and evidence-based decision routing

Status: needs-triage
Type: implementation specification; documentation only
Priority: high
Baseline: master `60d8332d64b2edef1b204fb5980311e6102229a8`
Owner: harness/backend implementer; gatekeeper owns admission/persistence review; UX reviewer owns Activity disclosure
Dependencies: HH-03 before any enabled worker inference; HH-04 for source bindings; HH-05 characterization first. HH-01/02 gate later application; HH-06 owns study-cycle advancement, HH-07 research evidence, HH-08 report/Excel acceptance. None is claimed implemented by this document.

## 1. Outcome and verified boundary

One coordinator owns the user's study, decision packet, and authoritative model-change path. It can commission two concurrent workers for scoped research synthesis and parameter proposals. Workers return evidence and suggestions; they cannot edit the shared model, approve anything, contact reviewers, change requirements, widen tools, or spawn children. A future explicitly configured limit of three workers retains the same contract and needs its own release evidence. There is no arbitrary recursive agent tree.

The first production slice is deliberately concrete: two inference workers receive independently serialized, immutable packets of already acquired project/evidence data and have **no tools**. One extracts competing evidence and gaps; the other synthesizes candidate parameters and questions. Packet adapters initially allow ordinary coordinator dispatch of `get_component` for HH-04-supported Generator rows and `get_run_evidence` for explicit completed recorded runs, plus shipped template metadata and supported HH-07 ledger reads. Other source adapters refuse. Live web research, candidate-project execution, and automatic patch application are separate adapters, disabled initially. Do not advertise fresh research when only retained sources were read.

Audit findings below are source facts, not promises about future modules. Paths are relative to `pypsa-gui/backend/` unless prefixed otherwise.

| Source at baseline | Existing behavior and consequence |
| --- | --- |
| `harness/README.md`, `loop.py:dispatch_tool_call`, `_dispatch_real_tool_call` | Ordinary dispatch is the authoritative allowlist/guard/validation/card/handler seam. Threaded handlers use `contextvars.copy_context()`. Reuse that seam for coordinator actions; never call a mutating handler from a worker. |
| `harness/runner.py:Runner`, `services/assistant_tasks.py:claim_step`, `_advance_run` | Runner advances deterministic task steps, with task claims, persistent leases, bound owner/project contexts and job reconciliation. It is not a generic agent factory. Delegation needs a new bounded scheduler; a task being completed does not establish a child's conclusions. |
| `harness/reviewer.py:review` | One bounded no-tools qualitative review, 512 output tokens, 128 events, five seconds and two slots. Timeout can leave a live worker. It has no subtask DAG, result reconciliation or authority to certify truth. Do not repurpose its `passed` verdict as decision approval. |
| `harness/protocol.py:LLMRequest`, `LLMProvider`, `providers/wiring.py:_provider_for_profile` | Neutral streaming seam exists; no generic child lifecycle or capability-certified delegated-call factory exists. Provider details remain inside `providers/`. |
| `services/run_profiles.py:STAGES`, `GENERIC`, `PROFILES` | Fixed native stage sequence; only `eh_site@1` is registered here. Explore/report are skipped in generic defaults. Template existence does not imply a runnable specialist profile. |
| `services/flow_scope.py:CARDS`, `TEMPLATE_PRESETS`, `AVAILABILITY` | Scope controls effective cards/options and retains unavailable intended options. Capacity is currently required; an IPP with no load has no adequacy study. Never turn an unavailable option into a worker action. |
| `services/run_envelope.py:admit`, `RESEARCH_KEYS`, `NEVER_ADMITTED`; `harness/confirm.py`, `approvals.py`, `restrictions.py` | Only an explicit user grant establishes an envelope; restrictions can narrow rights. Researched values require a card; publication and excluded operations retain their gates. Child agreement cannot amend those rules. |
| `harness/effects.py:validate`, `reserve`, `execute` | Existing checked effects cover a specific `update_component` contract, with durable deduplication/read-back. They are not a generic patch transaction. HH-01 owns revision-atomic application. |
| `harness/eventlog.py`, `activity_history.py`, `attention.py`; frontend `components/AssistantActivity.tsx` | Persistent history and owned Activity observations exist. Observation digests are not approvals; Activity cannot restore permission or prove numerical correctness. |

Read root/GUI `AGENTS.md`, `TESTING.md`, harness README and `docs/agents/outsourced.md` before implementation. There is no separate `GUIAGENTS.md`; GUI instructions are `pypsa-gui/AGENTS.md`. Existing outsourced edits to dispatch/wiring/catalogue require coordination before touching those files.

## 2. Shared intent and executable acceptance contract

`harness/decision_packets.py` owns shared strict `IntentContractV1`, `AcceptanceItemV1`, `AcceptanceCoverageV1` and `DecisionPacketV1`. HH-06/07/08 import these definitions; extensions retain shared IDs and schema versions. Intent captures what the user requested, separately from HH-02's native engineering predicates. A chat summary, worker conclusion or successful solve cannot replace it.

`IntentContractV1` contains `schema_version=1`, `intent_id`, monotonic `intent_revision`, canonical `intent_sha256`, owner/project/parent identity, originating user-turn reference, objectives, requested deliverables, selected scope/cards, source policy, budget-policy references, approval-policy references, and `acceptance_items[]`. Store the user request reference and bounded verbatim excerpts so omission can be audited. Every requested part maps to at least one stable `acceptance_id`; software validates complete mapping before work starts. Ambiguity becomes `needs_user`, never a guessed omission. Only an explicit user instruction supersedes/removes a requested part; retain its replacement ID and originating decision.

`AcceptanceItemV1` contains ID, requested-part references, deliverable ID, concrete predicate/expected outcome, dependencies, required evidence selectors, expected checker `{kind: native|artifact|user_review, adapter_id, version}`, applicable units/window/basis, and required user-decision category. Checker IDs resolve a closed server registry, never model-provided executable expressions. Unsupported checkers remain unsupported; they cannot become prose checks. Budget and approval references constrain execution, without granting rights.

`AcceptanceCoverageV1` binds `(intent_id, intent_revision, intent_sha256, acceptance_id)` to current state `pending|running|software_passed|needs_user|unsupported|incomplete|failed|accepted|superseded`, evidence/claim/artifact/check receipt references, checker version, source digests, decision reference and reason. These are server transitions: `software_passed` requires the prescribed deterministic checker receipt; `accepted` requires all declared checkers and required user decisions. `superseded` requires an explicit user decision. Evidence loss/revision change invalidates affected closure. Unknown, failed or unsupported items remain visible and block an unqualified “request complete.”

`DecisionPacketV1` carries packet ID/revision/digest, immutable intent reference, coverage rows for **every** item, per-claim/action assessments below, conflicts, remaining decisions and worker lineage. HH-06 study-cycle receipts, HH-07 research entries and HH-08 artifact/acceptance tasks carry the same intent triple and acceptance IDs. Report-specific child schemas may extend fields; they cannot renumber, drop or silently close parent items. Completion is a deterministic coverage-ledger reduction, with explicit partial completion when unresolved items remain.

## 3. Coordinator and dependency graph

Introduce versioned, server-shipped **delegation profiles**, separate from native study profiles. A profile declares node purpose, prerequisites, evidence requirements, allowed adapters, mandatory/optional joins, and output schema. Client/model text may request a supported profile and narrower scope; it cannot upload executable profiles or tool names. Freeze profile version, selected cards/options and the complete dependency graph at admission. Reject cycles, unknown nodes, duplicate IDs and unsatisfied references before scheduling.

The common graph is:

```text
intake + HH02 requirements + selected scope + HH04 source capture
  -> [evidence extraction || parameter/synthesis proposal]
  -> source validation + conflict arbitration
  -> decision packet + user decisions where required
  -> HH06 authorized study cycle -> native result gates
  -> HH08 report/Excel acceptance -> hand-off
```

Only the parallel bracket and join are enabled initially. Existing study execution remains in its current stages; delegation does not install a second solve scheduler. Edges into HH-06/08 are typed prerequisites and acceptance references, never hidden execution. A source refresh creates a new node generation and invalidates dependent conclusions. An optional node can be skipped only with its declared reason and visible coverage loss; missing mandatory evidence blocks the downstream claim/action.

“Energy” in the requested five-card vocabulary maps explicitly to current `capacity`; Grid→`grid`, Reliability→`reliability`, Commercial→`commercial`, Control→`control`. Hand-off is an artifact destination, not an added study obligation. `assistant_tasks.CARDS` uses `hand-off` while `flow_scope` uses `handoff`: adapt explicitly at their boundary, with a regression test. Do not add ambiguous aliases or silently change persisted briefs.

Template routing is a dependency declaration, not a claim that all adapters exist:

| Template/profile family | Conditional dependency path |
| --- | --- |
| C&I / own site | Demand, tariff and connection evidence → Energy sizing/dispatch; selected Commercial adds contract/finance basis; selected Reliability adds critical-load/outage basis; Grid and Control add their own prerequisites. Resolve own-site metadata rather than inventing a C&I template ID. |
| Data center (`eh_datacenter`, staged variant) | IT-demand/phasing and connection → Energy; critical load and outage data → selected Reliability; converter/island assumptions → selected Control; tariff/capex/party basis → selected Commercial. Staged years and terminal year remain pinned. |
| IPP (`eh_ipp_hybrid`) | Resource/export constraint → Energy; offtake/PPA basis → selected Commercial; electrical limits → selected Grid. No fabricated critical load or mandatory adequacy study for a generation-only plant. |
| Mining (`eh_concentrator_staged`) | Throughput/process demand and operational calendar → Energy; process availability → selected Reliability; power-quality limits → selected Grid/Control; commodity/revenue assumptions → selected Commercial. Unsupported process coupling stays a gap. |
| Hydrogen (`eh_h2_hub`, staged variant) | Offtake, conversion efficiency and storage chronology → Energy; demand/service basis → selected Reliability; water/product/contract assumptions → selected Commercial; Grid/Control only as selected. |
| Ammonia (`eh_ammonia_staged`) | Hydrogen/nitrogen/process coupling and ammonia offtake → Energy; selected Reliability/Control require process interruption/ramp evidence; selected Commercial requires product and cost basis. Missing process-detail adapter cannot be replaced with prose. |
| Microgrid (`eh_microgrid`, staged variant) | Demand/resource/storage chronology and mainland-tie state → Energy; selected Reliability uses island/outage evidence; selected Control uses frequency/ride-through prerequisites; selected Commercial uses ownership/tariff basis. No automatic dynamics claim from an optimal energy solve. |

`project_templates/eh_templates.py` and registered specialized builders supply identity/ledger data; `flow_scope` supplies effective availability. These are not the same registry as `run_profiles.PROFILES`. Store the actual chosen identifiers in every result. An unavailable specialist run profile may still support a no-tools evidence packet, labeled “Evidence review only; study execution unavailable.” Never force every card, research service, feature or study method on every template.

## 4. Strict delegated-task and result schemas

Add new `harness/delegation_types.py` strict version-1 schemas (unknown fields, duplicate JSON keys, nonfinite numbers and excessive nesting refuse). IDs are server-generated canonical UUIDs except bounded profile/node IDs. Store at most 32 nodes, two concurrently running workers, 64 claims and 32 patch proposals per delegation; input packet ≤128 KiB and result ≤64 KiB. Limits are server policy, and can only be narrowed by the request.

`DelegatedTask` contains:

* Identity: `delegation_id`, `node_id`, `generation`, `attempt_id`, `parent_thread_id`, `parent_generation`, `episode_id`, `task_id?`, authenticated `owner_id`, `project_id`, `actor_id`, `replica_id`.
* Inputs: `purpose` enum (`evidence_extract|parameter_proposal|synthesis`), bounded objective, profile/version, dependency result digests, selected cards/options, HH-02 requirement revision/digest, project input revision/digest, HH-04 typed `source_refs`, immutable packet ID/SHA-256, and necessary report/study acceptance IDs.
* Rights: `allowed_tools` (empty initially), `source_scope`, `effect_policy=none`, restriction revision, server policy revision. These describe narrowing constraints, not grants. Credentials, envelope tokens and foreign project data never enter packets.
* Resources: per-attempt input/output/event limits, physical-send allowance, tool allowance, monotonic execution deadline plus persisted UTC deadline, HH-03 parent budget scope/reservation references, and frozen provider-profile/configuration revision. No child supplies its own budget ledger.

`DelegatedResult` contains exact task/attempt/input bindings; `outcome` (`returned|incomplete|refused|cancelled|timed_out|failed`); `claims[]` with stable IDs, bounded statement, domain/unit/window/basis, cited evidence IDs, exclusions and unresolved questions; `evidence[]` with HH-04/07 locators, digest and extraction span; `proposed_patches[]` with typed component/record/attribute, before-value/digest, proposed value/unit, purpose and source references; limitations and optional self-reported confidence.

Confidence is stored only as untrusted commentary and never used for admission, routing or verification. Software stamps `authority=none` independently of the model; a returned `authority`, certification, approval token, executable code or arbitrary filesystem/JSON-patch path refuses schema validation. Proposals cannot contain demand/targets/solver policy changes forbidden by `run_profiles.NEVER_TUNABLE`; such concerns become questions. A worker cannot declare its evidence software verified. Citation validation checks the exact permitted packet, source span, revision, unit and basis; a plausible URL or another child's assertion is insufficient.

Do not give workers a shared `ChatSession`, message list, PyPSA network, mutable parent dictionary, database handle or live source object. Serialize then independently deserialize each packet/result. Parent history receives bounded references and summaries; full owned artifacts are inspected separately. Worker content remains fenced untrusted data throughout synthesis and export.

## 5. Persistence, scheduling, cancellation and joins

Add `harness/delegation.py` with `prepare(parent_ref, profile_ref, request_id, scope)`, `start(prepared_id, expected_digest)`, `inspect(id, expected_revision?)`, `cancel(id, expected_revision)`, `claim_node(id, node_id, expected_generation)`, `accept_result(attempt_id, result)` and `join(id, expected_generation)`. These are native internal APIs; first slice exposes no model-callable spawn tool.

Canonical delegation records live under the saved project's private `delegations/<uuid>.json`, using `harness.durable.transaction` and the owned snapshot atomic-write/fsync pattern. Parent linkage is immutable and resolves through `services/durable_conversation.py`; HH-03's parent file remains sole budget authority. Generic checkpoint payloads cannot rewrite delegation state, leases or worker results. Persist write-ahead state before inference. Strict bounded reads, no-follow paths, owner/project authorization and corrupt-record refusal match existing stores. Unsupported/foreign IDs return non-disclosing 404.

Parent states: `prepared → running → joining → ready_for_review|incomplete|needs_user`, with `cancelled` and `failed` terminals. Node states: `blocked → queued → leased → dispatch_intent → running → returned → validated|rejected`; terminal alternatives include `cancelled`, `timed_out`, `stale`, `unavailable`, `failed`, `unknown`. “Returned” is transport/schema progress, not successful engineering work. `ready_for_review` means packet assembly finished, never that the study/report is accepted.

Under the delegation transaction, claim a node by CAS on record revision, node generation and lease epoch. Persist holder/epoch and a 60-second lease; renew every 20 seconds while live. Run no network call under this lock. First slice supports one application worker/authoritative replica only; leases prevent duplicate scheduling, not distributed PyPSA safety. A stale epoch cannot accept a result or publish a join. Lease expiry after `dispatch_intent` makes the attempt unknown; it does not establish no send or authorize replay. HH-03 alone settles inference liability.

Idempotency key is `(owner, project, parent, request_id)` with canonical payload digest. Identical prepare/start retries return the same record; changed payload refuses `delegation_request_conflict`. Result identity is `(node, generation, attempt_id, result_sha256)`; duplicate identical returns acknowledge once, conflicting returns quarantine both. Join CAS pins all validated result digests, input bindings and graph generation. Two joiners publish one packet. Event append failures leave a recoverable pending projection; no notification or UI event can advance canonical state.

At every claim and join, recheck parent generation, ACL, requirements, restriction policy and source revisions. Stale inputs invalidate affected nodes/dependents and pause for an explicit refresh proposal. Never substitute newest evidence. Restart preserves terminal results, marks orphaned dispatched work unknown, and waits for bounded live stragglers; no automatic retry. An explicit replacement attempt needs current inputs, another HH-03 reservation and remaining parent allowance.

Cancel commits a generation fence before signalling worker abort events; queued work cannot start. Python threads may continue after timeout/cancel: retain their semaphore slot until the stream actually closes. Late usage settles the original attempt; late results are historical only and cannot enter a cancelled/new generation. No thread force-kill, cancellation-as-no-cost inference, or automatic replacement while a straggler occupies capacity. Mandatory timed-out work yields incomplete; optional work yields an explicit omission. Parent cancellation stops future work, not unrelated queued solver jobs.

Conflicting findings are grouped by claim, source, unit and basis. Deterministic source checks discard unsupported assertions; genuine conflicting sources remain visible with their provenance. Coordinator may propose a bounded additional check within existing quota, or ask for user judgment. Two agreeing workers do not outvote contrary evidence. Never average incompatible parameters or use majority voting as a truth test.

## 6. Resources, capabilities and actor propagation

Add `harness/delegation_workers.py`: process-wide pool of four slots, at most two live worker streams per parent and two per project initially; queues ≤16 per process and ≤2 per parent. First slice grants one physical send per node, no inference retries or compatibility resend; an adapter needing another send refuses unless separately admitted within an explicitly approved larger allowance. All caps AND together. Fair scheduling rotates parents, with queued deadline expiry before pickup. Coordinator inference is separately metered and cannot bypass owner/project limits by using another session. Expose configured scope and observed outstanding work, not invented time-to-finish.

Every physical worker send, retry, compatibility resend and synthesis/reviewer call must pass HH-03's admitted provider seam. Child allocations partition remaining parent allowance; parent+all children+reviews share lifetime and owner/project-day quotas. Reserve a complete upper bound immediately before actual pickup/send, including cache categories and provider search fees if a future adapter supports them. Unknown pricing/capability/usage refuses or retains liability under HH-03; never silently downgrade monetary coverage. A child session restart, another profile or new delegation gives no free budget. Existing runner EUR gates, research default €2/cap €10, session/tool/round/deadline restrictions remain additional ceilings, not duplicated charges.

Use `providers/wiring.py` only through a new neutral adapter `resolve_delegated_provider(profile_ref, expected_config_revision, required_capabilities)`. It returns an immutable handle and HH-03 capability description. Model availability is dynamic: validate configured/enabled profile, credentials available without disclosure, no-tools streaming support, output/input bounds and usage receipt support at admission and pickup. Missing local/cloud endpoint or unsupported provider-native search returns typed `delegation_capability_unavailable`; do not substitute a named premium model or install a connector. No probes or paid availability tests during preparation.

Scheduler work captures identity explicitly from authenticated admission. Before any coordinator-side packet read, resolve the saved project as that owner, obtain its background base context, and follow `runner.py:_bound`/dispatch's copied-context pattern: request-scope bindings, acting user, dedicated child session identity, parent/task bindings and harness lock marker. Copy context separately for every submitted thread; reset bindings in `finally`. No ambient foreground `PyPSAService` context, owner inference from packet text, or shared context reuse. The no-tools inference worker itself has no project service access. Future read-tool adapters need exact bound-context tests and fresh ACL/restriction checks per call before being allowed.

## 7. Decision packet, review policy and authoritative application

Add `harness/decision_packets.py` to derive a versioned packet from validated sources, native checks and the join. Each claim **and each proposed action** carries `assessment`:

* `software_verified`: an identified deterministic validator established the precise claim against current referenced data, with validator/version, coverage, units, basis, source digests and check receipt. This establishes only the stated computation/predicate, never independent engineering certification.
* `needs_user_judgment`: evidence is readable but interpretation, preferences, risk acceptance, contractual assumptions or an explicitly required approval remains. Include the exact unresolved choice and consequences.
* `unsupported_or_incomplete`: missing/stale/conflicting evidence, unsupported adapter, unverified parameter, unavailable computation or interrupted worker. Include typed reason and affected downstream actions; no successful action is inferred.

A packet can contain all three states. Never put a whole-answer truth badge on it. “All workers returned” cannot satisfy a metric, requirement, review gate or acceptance task. HH-08 consumes individual accepted claims and artifact checks, keeping report drafting, workbook validation, artifact delivery and user acceptance distinct. An Excel file existing or a report generation job succeeding is not study completion. HH-06 advances only from its own authorized actions/native gates. HH-07 retains researched values/source conflicts independently of whether the coordinator likes their answer.

Add `route_action(packet, action_ref, current_policy)` returning `blocked|needs_user|eligible_within_grant`, reasons and exact grant/receipt references. Eligibility requires all of: supported effect adapter; current owner/project/revision; current requirements and personal restrictions; basis coverage for the action; explicit task authorization or standing preauthorization whose effect class, fields, bounds, expiry and quota cover this action; and no mandatory approval exclusion. Risk is server-defined effect/risk policy, not model confidence. Missing or ambiguous policy routes to the existing user card or blocks unsupported actions; it never becomes automatic permission.

Standing preauthorization is an explicit user-authored grant, revisioned/revocable and narrowed by existing floors. This ticket adds no new grant mechanism. Use existing approved envelopes where supported; researched values still require their individual card under SH-RESEARCH-1, and existing destructive/publication exclusions remain. An apparent low-risk action without grant still needs its ordinary authorization. A “review required” routing record prepares a local decision packet only. Never email/message an engineer, stakeholder or reviewer unless the user's task explicitly authorizes that contact, recipient and content.

Only coordinator ordinary dispatch may apply a reviewed candidate, one authoritative commit path through HH-01 revision-atomic admission, HH-02 requirements, existing confirmation/restrictions, effect reservation and read-back. Revalidate proposal values and source bindings at pickup. A stale reviewed packet refuses and requires a fresh packet/card. There is no direct result-to-mutation callback. Isolated candidate project branches are future scope: explicit approval must name branch creation, scoped edits, solves and budgets; each has its own owned context and provenance. They cannot mutate/activate/merge into the parent implicitly. Git branches are not part of runtime engineering model branching.

## 8. APIs, Activity and implementation allowlist

Extend existing authenticated `routers/durable_conversation.py` with prepare/start/inspect/cancel under `/{thread_id}/delegations`; bodies contain supported profile, narrower scope, request ID and expected revision/digest only. Server resolves actor/project/parent. Start requires a user-origin request within task scope; neither generic checkpoints nor worker output can start work. GET is side-effect-free and cannot recover a live session. No `retry`, grant-editing or model-selection endpoint in slice one. Typed refusals follow the GUI's existing `error_kind/message` convention, including `delegation_stale`, `delegation_limit`, `delegation_incomplete`, `delegation_checkpoint_unreadable` and capability/conflict errors above.

Persist coordinator/worker lifecycle events with parent, node, generation, attempt, call and result references in existing event history. Do not log credentials or unrestricted full prompts. Add a bounded owned Activity projection: purpose, progress, remaining/unknown work, cancellation, source gaps, packet status and inspect link. Reopening Activity shows persisted facts, not fabricated liveness. Inspection reruns ACL checks; stale snapshots hide actionable controls. Keep Guided/Expert behavior and existing density/layout; add no new mode or standalone agent-control dashboard.

Later implementation allowlist:

| Files | Responsibility |
| --- | --- |
| New `harness/delegation_types.py`, `delegation.py`, `delegation_workers.py`, `decision_packets.py` | Schemas, durable scheduler/join, no-tools execution, deterministic assessment/routing. |
| `services/durable_conversation.py`, `routers/durable_conversation.py` | Immutable parent linkage and authenticated narrow control/read APIs; no parallel budget truth. |
| `harness/providers/wiring.py`, HH-03 neutral call adapter/protocol additions | Frozen provider resolution/capabilities and actual-send admission. SDK changes only under `providers/`. |
| `harness/eventlog.py`, `events.py`, `activity_history.py`, `attention.py`, `routers/assistant_activity.py` | Closed event additions, bounded owned observation adapter; no execution authority in inspectors. |
| `harness/runner.py`, `services/assistant_tasks.py`, `run_profiles.py`, `flow_scope.py` | Minimal explicit hooks/boundary adapters; retain shipped profile gates and card availability. |
| Frontend `api/assistantActivity.ts`, `api/durableConversations.ts`, `components/AssistantActivity.tsx`, matching chat/task surface | Read/control bindings and inline packet inspection; no broad UI refactor. |
| `harness/README.md`, `tests/test_harness_layout.py`, new focused delegation/decision tests, frontend matching tests, release-note fragment | Contracts, conformance, user-visible completion evidence. |

Any additional path requires an explicit amendment explaining its necessity before implementation. First slice does not modify domain engines, exports, model formulas, template networks or solve queue execution. HH-07/08 adapters supply source/artifact contracts through their own reviewed changes.

## 9. Test-first acceptance and end-to-end journey

Use fake providers, hand-authored immutable sources, barrier-controlled schedules and fake clocks; no live paid calls, search, broad solver runs or timing sleeps. Assertions use independent stored/native state and send counters, not coordinator prose. Required cases:

| # | Case and observable result |
| --- | --- |
| 1 | Two ready nodes overlap; dependent join waits for both exact generations. |
| 2 | A third node queues; configured/future three-worker support cannot raise the initial hard ceiling. |
| 3 | Cross-parent/project/process caps and fair pickup prevent slot exhaustion bypass. |
| 4 | Cycle, unknown profile/card/tool, duplicate ID and oversized packet refuse before inference. |
| 5 | Separate deserialized packets resist one worker changing another's input or parent messages. |
| 6 | Worker requests write, search, credentials or recursive delegation: refused, no handler/contact. |
| 7 | Forged citation/changed span or wrong unit/basis is rejected despite confident output. |
| 8 | Two workers agree on a false assertion; native source rejects it without voting. |
| 9 | Genuine contradictory sources remain unresolved, with no averaged parameter. |
| 10 | Source revision changes during parallel work: affected join becomes stale; no newest-run substitution. |
| 11 | HH-02 requirements change and restore: revision still invalidates packet/action. |
| 12 | Foreground project switches while child runs: reads remain bound to original owner/project. |
| 13 | Foreign owner/ACL revocation/personal read-only change prevents pickup/action without leaking data. |
| 14 | Duplicate start/result/join and racing scheduler claims publish once; conflicting payload refuses. |
| 15 | Restart before dispatch permits safe queued recovery; after dispatch leaves unknown, no replay. |
| 16 | Expired lease/old epoch cannot publish or settle another attempt. |
| 17 | Queued deadline/cancel prevents send; active timeout retains slot until stream closes. |
| 18 | Late result after cancellation/source refresh stays historical; late usage settles original hold. |
| 19 | Every retry/compatibility resend/reviewer consumes HH-03 parent capacity; child/session reset cannot refill it. |
| 20 | Unknown pricing/missing usage/unsupported certified bounds fail closed without zero-cost settlement. |
| 21 | Dynamic profile removal, credential loss or capability change refuses pickup without automatic model substitution. |
| 22 | Corrupt record/fsync failure/event projection failure yields no false completion or duplicate send. |
| 23 | Mixed packet assessments remain per claim/action; review `passed` cannot upgrade them. |
| 24 | Standing grant expires/revokes or excludes researched value: ordinary card remains necessary. |
| 25 | Stale action review fails HH-01 pickup; authorized current action applies once and read-back binds its receipt. |
| 26 | Report/workbook exists but HH-08 acceptance fails: study remains incomplete. |
| 27 | Template matrix exercises all seven families and selected-card omissions, including IPP no-load and unavailable dynamics. |
| 28 | Activity refresh/restart shows unknown/stale/cancelled facts; inspection restores no authority. |
| 29 | Optional worker omission degrades declared coverage; mandatory omission blocks downstream acceptance. |
| 30 | Explicit candidate-branch request remains unsupported initially; no parent mutation/activation occurs. |
| 31 | Multi-part intent retains all acceptance IDs across HH-06/07/08; unsupported/superseded parts never disappear. |
| 32 | Forged checker receipt/user decision cannot close an item; stale intent revision invalidates affected closure. |

End-to-end acceptance: user opens a staged data-center study and asks for a tariff/storage evidence review while an explicitly authorized sensitivity branch is being studied through existing native tools. Coordinator freezes the parent revision, requirements, card selection and retained sources; two packet workers overlap without entering either project's live model. One invents a tariff citation; the other exceeds its deadline. Meanwhile the user edits a parent demand assumption. The invented citation fails source validation, the late result cannot join the new input generation, the timed-out worker retains its slot/HH-03 liability, and existing branch execution retains its own native status. Activity shows “Evidence review incomplete” with separate false-citation, changed-input and timeout reasons. No model patch, report acceptance, worker replacement, reviewer contact or study-complete badge occurs. User explicitly requests a refresh; a newly admitted generation reads current sources within remaining quota, prepares a packet, and presents any researched parameter for its required card. Only subsequent native application/read-back, HH-06 results and HH-08 artifact acceptance can close their respective tasks.

Implementation order: characterize the baseline; land strict schemas/store/CAS with fake workers; land HH-03-admitted no-tools execution and straggler handling; land source validation/decision routing; add authenticated API and Activity projection; run focused backend conformance and frontend typecheck/tests; then exercise HH-05 trajectories and the journey above. Default feature flag is off. Enable two-worker evidence packets only after all required cases pass; rollback blocks new work but retains inspection, cancellation and outstanding budget settlement. Release evidence must name exact covered providers, profiles, cards, validators and exclusions.

Skipped by this specification: application code, Git/PR operations, paid/network calls and heavy test execution. Remaining implementation risk is unproven scheduler/admission integration; live search, three-worker operation, candidate execution and automatic report/Excel acceptance remain unavailable until their adapters and acceptance evidence exist.
