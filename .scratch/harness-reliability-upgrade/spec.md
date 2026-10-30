<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Harness reliability upgrade — implementation contract

Status: specification under independent review; no application implementation authorized by a review artifact alone.
Baseline: master `60d8332d64b2edef1b204fb5980311e6102229a8` (29 October 2026).

## Canonical slice numbering (single source of ownership)

| ID | Sole feature | Owner and detailed source |
|---|---|---|
| HH-01 | Revision-atomic native mutation admission | [01](issues/01-atomic-mutation-admission.md); owns InputVersion/residency identity and native permit/receipt. |
| HH-02 | Confirmed persistent engineering requirements | [02](issues/02-persistent-requirements.md); owns requirement schema, confirmation, CAS, constraint guard and per-turn projection. |
| HH-03 | Durable provider/reviewer call budgets | [03](issues/03-durable-call-budgets.md); owns prepared-call/bound contracts, physical-send lifecycle and accounting ledger. |
| HH-04 | Immutable domain reference pilot | [04](issues/04-typed-domain-references.md); owns ProjectRef/ComponentRef/RunRef using HH-01 identity and existing native resolvers. |
| HH-05 | Trajectory/state-survival evaluation | [05](issues/05-trajectory-evaluation.md); owns independent fixtures, semantic conformance and qualification reports. |
| HH-06 | Bounded solve–check–correct study cycles | [06](issues/06-study-correction-cycles.md); integrates existing study-harness04/05 with explicit candidate scope and durable history. |
| HH-07 | Research and sourced parameter proposals | [07](issues/07-research-parameter-proposals.md); integrates study-harness09 source ledger and always-confirmed researched values. |
| HH-08 | Report/workbook revision cycles | [08](issues/08-report-workbook-revisions.md); owns artifact-task operations over HH-09 acceptance authority, copy-only revisions and native artifact checks. |
| HH-09 | Coordinator, acceptance and delegated work | [09](issues/09-coordinator-delegation.md); owns IntentContractV1, AcceptanceItemV1, AcceptanceCoverageV1 and DecisionPacketV1 plus bounded read-only child workers. |
| HH-10 | Immutable asynchronous solve admission | [10](issues/10-immutable-solve-admission.md); owns isolated candidate snapshot, strict native queue fence, worker pickup and exact attempt/job/result scope. |

The first complete native study has the exact mapping `profile_ref=capacity_isolated@1` → `lineage_kind=isolated_capacity_candidates_v1` → `adapter_id=isolated_capacity_lopf_v1`. HH-06 owns profile registration, HH-10 owns lineage/admission. These fields are distinct and cannot be silently aliased to the existing eh_site profile or inferred from a template name.

No alternate numbering is valid. Dependencies do not transfer ownership. See the detailed contract and review target manifest for the reviewed payload.

## User value and release scope

The foundation features improve bounded native action reliability, long-chat continuity, honest resource limits, exact reference resolution and verifiable end-to-end outcomes. They strengthen the existing provider-neutral harness rather than replace it. Research origin and limits remain in [the literature synthesis](../harness-research-2026/assessment.md); paper gains are not GridSpine measurements.

The first technically reviewable implementation scope is HH-05A characterization; HH-01 **Phase A**; HH-03 protected accounting/call lifecycle; HH-02's two native input predicates; HH-04's narrow saved-project reference pilot; and HH-05B qualification. HH-01 Phase B is now elaborated in HH-10: it is a separate implementation PR and a mandatory prerequisite for the full unattended solve cycle, not an optional gap to bypass. Foundation-only releases cannot claim the expanded autonomous product is complete. Numeric/field/operation coverage is closed in each ticket; a generic numeric field or tool does not become supported implicitly.

Existing native UI and manual project work retain their own validation. Typed requirements constrain explicitly bound **assistant** effects, not all manual project edits. External edits invalidate observations/admissions and require native revalidation; the feature does not lock the user out of their own model. Unsupported assistant effects do not silently bypass an active enforced requirement: they pause/clarify or require an explicitly different ordinary-review scope, with no continued claim of requirement-guarded execution.

All confirmed chat input edits retain the existing explicit old→new card in Expert and Guided. Other tool confirmations follow their existing mode-specific policy. Provider wire offers may differ: the official OpenAI cap is not a demand that every wire expose identical tools. Every dispatched tool must have been eligible and actually offered to that request.

No new mode, framework picker, arbitrary code execution permission, vector memory store, autonomous policy rewrite, live model benchmark, domain certification or broad checked-tool admission is introduced. The single companion, five workflow sections, Choice/card/Activity surfaces remain the product flow. Frontend changes are limited to HH-02 attachment/projection/refusal display and existing stream/card types; the server owns availability.

## Existing baseline and ownership

The baseline already contains durable history/parent conversations, replay, tool discovery, finite retries/caps, immutable cards, effects/reconciliation, evidence MAC/rederivation, native result profiles, runner/envelopes and the checked pilot suite. [baseline.md](baseline.md) maps existing symbols and parallel work. Do not recreate these systems or infer missing behavior from stale Status lines.

Existing Assurance issue10 owns checked plausibility after K#851; study-harness04/05own bounded correction loops and09owns research. HH-06/07 are coordinated implementation deltas on those owners, not competing rule/runner implementations. This package does not silently change their frozen bounds, engine formulas, thresholds or checked publication. General get_results and newly introduced requirement tools remain subject to existing strict guards until their explicit assurance adapters are approved separately. Reports/voice/model subcalls excluded from HH-03 must not be presented as covered account-wide spend.

## Shared contracts and ownership

| Contract | Normative owner | Consumers and interpretation |
|---|---|---|
| InputVersionv1 | HH-01 admission_identity.py | Exact `{version,project_id,residency_id,network_revision,exact_fingerprint}`. Native inputs/config captured coherently under existing locks. HH-04 reuses, not rehashes; neither identity nor MAC proves physics. |
| MutationPermit/MutationReceipt | HH-01 | Private bound actor/project/context/attempt/args/card/grant + optional requirement tuple. Native before/after/read-back captured within actual guarded effect; success JSON/tool_running is not authority. |
| RequirementScope/records revision+digest | HH-02 | Explicit user-confirmed conversation binding; active records are native input predicates/preferences, not permissions. Logical named-row identity and validation freshness remain distinct. |
| PreparedCall/CallBounds/physical attempt | HH-03 | Immutable actual translated request and conservative advertised dimensions; missing capability refuses strict dimension, never guesses. Token/currency receipts are not action authority. |
| ProjectRef/ComponentRef/RunRef | HH-04 | Frozen identity observations, no mutable network or bearer permissions. Historical evidence is not made stale solely by a later live edit; its exact source integrity still rechecks. |
| CaseSpec/Outcome/Report | HH-05 | Independent finite-suite measurement. Manifest/fixture/oracle/source identity required; fake provider spend is labelled synthetic; no certification from model prose. |

No ticket may redefine another ticket's owned type under a similar name. A type-level mismatch is a blocker, not something the implementer resolves by implicit conversion. Examples of scope/basis fields are not permission tokens or client-authoritative values.

## Binding and transaction integration

All paths first resolve current actor and authorized project through native services. An explicit conversation attachment is HH-02's consented scope. HH-03may create a server-owned accounting-only parent for a previously unbound ordinary chat, but this **cannot** activate old goals, requirements or referenced results. The accounting association is unique, protected from generic checkpoints and preserved across episode/generation changes. Explicit conversation and budget purposes are distinguished by native metadata, not by model narration. Concurrent parent writers share the existing durable owner-directory transaction and preserve fields they do not own.

When a path needs native state and parent records, the ordering is:

1.Native project mutationRLock;
2.Native solver-stateRLock when coherent config/input reads require it;
3.Owner/project conversation directory transaction;
4.Brief/task/receipt leaf locks in their source-checked order.

No code waits for native locks while holding the parent transaction or global durable-conversation lock. Parent-only budget/settlement paths never acquire native locks or call model/network/user waits. Source ACL resolution occurs before the native interval and the matching binding/restrictions are rechecked at admission. Verify all touched call paths before changing this order; new inverse acquisition is a blocking review finding. HH-01's short native undo/snapshot IO must follow its inspected leaf-lock order, not be moved into generic card/flow decoration.

A requirement-bearing HH-01 effect keeps the matching requirements revision/digest transaction guard through actual native candidate validation/write/read-back. A writer cannot update requirements in the gap after validation. A later model/provider call happens only after native/parent effect locks are released. HH-03reserves before each physical send, not while a project lock is held. These lifetimes have deterministic concurrency tests in [test-matrix.md](test-matrix.md).

## Stop, recovery and evidence rules

- Drift, stale generation, missing native prerequisites, unsupported required coverage and denied/expired confirmation stop before an effect where positively established. A generic exception is not proof of no effect.
- Once native execution or a provider send may have started, cancellation/timeout alone cannot erase its reservation or receipt. Preserve unknown state, stop continuation and use the existing bounded reconciliation path.
- A successful native observation is captured before releasing its defining lock. Late runner/global rehash cannot absorb an unrelated user edit into its authority chain.
- Requirement mutation invalidates future permits/cards/task bindings; it does not retroactively relabel or abort a running effect. New steps need explicit re-review/regrant when prescribed.
- Resuming, importing a file, deserializing a reference, resetting history, switching providers or changing feature flags cannot recreate approval, clear unknown spend or turn old evidence current.
- All model-visible state projections are logged and bounded. Quotes/labels/uploads are fenced untrusted data. A projection is an instruction/fact view, not a numerical evidence record or hidden system prompt change.

## Delivery order and concrete gates

1.HH-05A: characterize current semantics with offline independent fixture/checkpoint tooling; validate negative controls. It must distinguish currently expected limitations from future behavior, not label absent features passed.
2.HH-01A: first reproduce native gap with an actual non-target edit barrier, then build exact covered worker admission/captured settlement. Unsupported unattended shapes pause before effect; queued snapshot support is future HH-01B.
3.HH-03: protected parent/accounting migration and prepared-send lifecycle, then main/reviewer hooks including actual transport retry/fallback sends. Default off; strict money/input mode cannot activate on unknown model rates/bounds. A helper-only implementation is incomplete.
4.HH-02: confirmed typed records, explicit binding/card display, candidate guard and state projection. Validate against HH-01A and shared owner transaction; existing active enforced records fail closed if a reader/guard is unavailable.
5.HH-04: pilot internal reference types on the exact read/preview paths; preserve public payloads and tool count. Remove/defer the abstraction if it consolidates no real invariant.
6.HH-05B: fixed full trajectory/domain profile, actual CI qualification step and explicit coverage report before activating the combined feature set.

Use isolated implementation PRs branched from then-current master. These slices share loop/parent/edit-card paths, so implement/merge them in this order rather than concurrently hand-merge overlapping production code. Keep successful source tests and exact-head CI separate from trial-merge tests. All ordinary repository GK/UX/premerge rules apply; a research/spec agent GO is not a merge approval.

Each implementation begins with a test proving the intended behavior fails on the pre-implementation head, plus current positive compatibility controls. Collection/setup/import failures are recorded separately and do not alone prove a behavioral red test. Native read-back, handler spies, event receipts and deterministic barriers supply oracles. No sleeps, assertion weakening, skips or larger timeouts to obtain green. Record exact commands/head, red→green cases, real counts, existing skips and source/fixture digests. No historical count summing or path-filtered green substitutes for actual required steps.

## Rollout, rollback and completion

Detailed [rollout.md](rollout.md) governs activation by capability, data migration and failure. Default-off observation/pilot modes make no enforcement claim. The expanded controllers are enabled only after their exact dependency/capability rows pass qualification; a foundation-only GO cannot be used to enable an incomplete controller. Retain typed records, unknown accounting and uncertain effect receipts on rollback; stop scoped guarded effects instead of treating unsupported data as absent. No actual model prices, provider limits, activation or paid experiments are invented by this plan.

The final independent technical verdict is under reviews/ and binds the canonical payload manifest. Every blocker must have a recorded resolution and re-review. GO means the explicit bounded initial slices are detailed enough to implement under normal project gates; it does not mean runtime changes exist, tests passed, all fields/engines are covered, or live calibration/domain approval has been granted.

## Coherent product workflow and shared acceptance authority

[User requirements](user-requirements.md) are the review source for this expanded package. [Workflow strategy](workflow-strategy.md) maps the templates/cards and user journeys. This section overrides the earlier foundation-only delivery scope wherever the user requested the expanded controller.

One server-owned `IntentContractV1` freezes the user's requested objectives, deliverables, scope, source and budget/approval policy, with one `AcceptanceItemV1` per requested part. HH-09owns these definitions and the exact `AcceptanceCoverageV1` state vocabulary: `pending|running|software_passed|needs_user|unsupported|incomplete|failed|accepted|superseded`. Controllers retain the exact intent ID/revision/SHA and acceptance IDs. A child/stage cannot omit or supersede a requested part. Only an explicit authorized user decision supersedes it. Artifact/study local statuses are operational projections, not competing sources of closure truth.

A parent may finish only when every required acceptance item satisfies its declared native/artifact checker and required user decision. `software_passed` reports mapped checks, not scientific approval; a mandatory unresolved/unsupported/needs-user item prevents complete. No language-model confidence, fluent summary, majority vote, missing row or empty check list proves acceptance. If criteria cannot be met within limits, preserve an honest partial/needs-user/incomplete outcome and its durable next actions instead of silently stopping or forcing a pass.

The model-agnostic path is:

1.Capture/clarify the user's intent and selected template/modules using existing cards/Choice; backend capability verdicts decide applicability.
2.Build a typed stage/dependency plan. Research and independent synthesis may run in parallel on immutable scoped packets; native model mutations stay on one authoritative coordinator path.
3.Gather parameter proposals with verified source records, units/basis/ranges and explicit conflicts. A researched value always uses ordinary user confirmation even when a study envelope exists.
4.Freeze authorized scenario requirements, tunable fields, thresholds, source identity, budgets and deadlines. A later material intent change creates a new revision and invalidates unstarted admissions.
5.Submit the first immutable candidate through HH-10, read exact native results, evaluate HH-06's registered feasibility/plausibility/design criteria, then propose only admitted corrections. Rerun only after a justified changed candidate within original constraints/caps. Use native engine loops where already specified, never nest generic agent loops around them.
6.Record every proposal/decision/input/result/check/correction with concise reasons and source/effect receipts. Users can leave the page and work elsewhere; server leases/checkpoints own continuation. Browser presence does not grant authority or keep work alive.
7.Generate a decision packet and native report/workbook copies. HH-08maps each feedback item to an exact version/section/range/check and iterates within its approved copy-only grant. Unaffected parts remain semantically preserved; partial outputs never replace the final/published artifact silently.
8.Route per-item review by declared evidence coverage/risk and client policy. Inspect-only/read steps and preauthorized deterministic checks may proceed without more clicks; unsupported/high-impact judgments remain explicit. Required existing edit/export/parameter confirmations are not removed by agent confidence.

HH-03B covers every physical model send of the new controllers, including coordinator/worker/stage/report generation and any billed research feature. If a call path lacks strict accounting/capability support it cannot execute inside a task advertised with that cap. Legacy STT/TTS/probe coverage remains explicitly separate. Parent quotas are shared across workers; child delegation never creates free allowance.

## Expanded implementation sequence

Foundation sequence above remains valid for incremental safe delivery. Complete-product order is HH-05A → HH-01A → HH-03A/B and shared HH-09 contract types → HH-02 → HH-04 → HH-10 → HH-06/07/08 plus HH-09 runtime → HH-05B expanded qualification. Types may land before the coordinator runtime with no new autonomous authority. Parallel authors may write independent specs/tests, but overlapping loop/parent/queue implementation PRs merge sequentially from fresh master.

HH-06 and HH-07 absorb the existing study-harness stage tickets; HH-08extends shipped report/workbook engines; HH-09adds the previously absent generic delegation controller; HH-10extends the native queue with strict admission rather than treating legacy best-effort queue writes as proof. Each slice's allowlist is explicit. Future changes to controllers, domain adapters, user-review waivers or paid capability scope need a reviewed amendment rather than being inferred from this roadmap.

The final agent must review both the original five foundation specs and all expanded controllers/dependencies. GO for a coherent detailed plan is bounded by their declared adapter/capability rows; it is never evidence that the functionality is already implemented or that every domain/template can run unattended.
