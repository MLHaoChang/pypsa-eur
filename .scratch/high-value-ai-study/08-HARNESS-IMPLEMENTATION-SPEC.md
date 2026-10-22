<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harness implementation specification

Status: concrete implementation specification for review; runtime not implemented by this PR.
Source baseline: `089aea276c75b098c8d349cb5fa0eb4cb7267d64`. This document makes the five priority harness improvements implementable in the existing owning packages. H06 journey evidence and H07 procedures are included as supporting delivery work; H08 general agent teams/code workspaces remain deferred.

**Current-master amendment:** [11 — delivery reconciliation and review](11-CURRENT-MASTER-AND-REVIEW.md) supersedes historical absence claims and the initial delivery order. On `7a14d27038de424d07a832b16e2465297c7b8b7d`, LC-23 S1/S2, checked investment/site-import adapters and other foundations already ship. Preserve those implementations; build only the residual contracts below. “Runtime not implemented by this PR” describes this documentation diff, not an assertion that master has none of these features.

Normative words: **must** is an acceptance condition; **may** is optional. A source or adapter listed below is a dependency, not a claim that its proposed behavior already ships. The original assessment remains historical context; this spec and [OWNER-DISPOSITION.md](OWNER-DISPOSITION.md) govern delivery where recommendations were less precise.

## 1. Scope, ownership and accepted design

Use the existing companion, task card, ordinary dispatch, native job/effect evidence, durable conversation, registry and checked publication. No new planner, approval service, top-level product module, second progress line, evidence-map/advisor panel, generic shell or model-specific execution path.

Master now contains [LC-23](https://github.com/MLHaoChang/pypsa-eur/blob/089aea276c75b098c8d349cb5fa0eb4cb7267d64/.scratch/live-copilot/issues/23-study-copilot.md) and the owner log. **Adopt LC-23 rather than implementing a competing task schema.** It fixes the study brief to an optional `start_task(..., brief=None)` argument, existing steps `{id, tool, args}`, version-2 task records, phases that partition steps in order and card-boundary checkpoints. Plain tasks retain 12 steps/16,000 characters; a brief admits 24 steps/32,000 characters, with 1,800-character arguments per step. The task view remains at most 3,800 characters. Do not add `card_id` to each step or another brief store.

The six card IDs in this contract are `capacity`, `grid`, `control`, `reliability`, `commercial`, `hand-off`, matching LC-23. W0/flow support remains the authority on runnable combinations. Missing W0/card code is a dependency; the model cannot implement a missing combination by choosing a label.

Every write/run step in a brief must require its own current card click in Guided **and** Expert. Preserve master's `status=paused_at_card`, authenticated stream `continue_task_id` and `assistant_tasks.continue_at_card`; `resume_task` cannot clear this pause. Harden that existing path with the boundary-bound reference described in the contract, rather than adding another checkpoint state or resume protocol. Reads need no extra approval. Brief review and boundary continuation grant no effect authority. No paid calibration, reviewer contact or production activation is included in this specification PR.

## 2. HS-01: capability and schema discovery

**Owner:** LC-01/LC-06/LC-22, W0/W9, S7. **Finding:** H01.

### Changes

Extend `services/assistant_tools.py::find_capabilities` and its catalogue/wrapper signatures to:

`find_capabilities(goal=None, limit=3, overview=False, offset=0, details=False, schema_sha256=None)`.

Existing callers retain their defaults. `goal` remains bounded to 1,000 characters, `limit` to 1–8; `offset` is an integer 0–10,000. Reject booleans used as integers. `overview=True` keeps the existing family overview; its output adds the same native support dispositions, without exposing protected project data.

Rank the existing catalogue first, then apply offset. Return `returned`, `has_more`, `next_offset` so queries can be completed beyond the first eight results. Keep stable catalogue order for ties. Schema expansion is performed only when `details=True` and the query resolves exactly one existing capability/tool; otherwise return a clarification with candidate IDs and no large schema. Preserve the 3,800-character result bound. A schema field whose full constraints cannot fit must yield a typed unavailable reason, never a clipped validation contract. For `details=False`, offset pages ranked capability items. For `details=True`, the exact native tool query is fixed and offset pages its schema fields, not search matches. Subsequent detail pages require `schema_sha256` from the first page; mismatches refuse as `capability_schema_changed`. The first page returns its native hash; a changed schema requires restarting inspection.

Every successful page with `has_more=true` must advance `next_offset` beyond the supplied offset. An individually oversized schema field returns a terminal typed refusal with its native field identifier; never emit an endless zero-return page or retry it automatically. CAP-01/CAP-06 include this progress invariant.

Enrich each existing tool item with native `support`, `schema_ref`, `result_view_ids`, `job_family` and `evidence_kind`. Derive input schemas from catalogue/Pydantic/native field descriptors; do not hand-copy monetary units or enum values. Reuse `component_discovery.py` paging for service-model metadata in the same module. Resolve only internal native `$defs` references to depth four with cycle detection; include all referenced constraints on the page or report an oversized/unsupported field, never resolve a URL or import a model by user-supplied path. The tool catalogue remains the single list of callable tools; supplemental metadata does not create executable tools.

For routes without a callable tool, extend the existing LC-22/manual disposition list in `assistant_tools.py`. Records identify an actual UI destination, native reason and one of manual-only/prohibited/not-implemented. Administration, authentication and secrets remain unavailable through chat. A missing route disposition must fail the coverage check. Project-specific restrictions, locks and native readiness are re-read; cached metadata cannot override them.

### Observable behavior

“Update this tariff field” returns the native field/schema or an explicit missing billing/adapter reason. “Set the inverter parameter” resolves a real entity and supported field before preview. “Why can’t I run?” uses the native blocker title/detail/target. No discovery call writes, starts a run, installs a plugin or approves anything.

### Completion condition

CAP-01–CAP-06 pass; route/tool/GUI disposition coverage is complete for the declared shipped slice; unmapped controls are reported as gaps. Reachability tests prove eligible tools can actually be offered under the official 128-tool selection cap. Do not mark all 14 families covered merely because schemas exist.

## 3. HS-02: authoritative completion proof

**Owner:** LC-23 slice 1, LC-05/12/19, task/effect adapters. **Finding:** H02.

### Changes

Retain the shipped LC-23 native resolution rule in `assistant_tasks.resolve_task_step`: `result` is already ignored for proof and bounded at 4,000 characters. Extend its verifier coverage and durable proof contract below; do not rebuild S1. The ordinary destructive confirmation remains mandatory. Verify owner/project, account, lock/restrictions where relevant, expected step/attempt and originating worker quiescence first.

For `outcome=completed`, select a native verifier by the step's actual tool/family:

| Family | Required proof | Unavailable behavior |
|---|---|---|
| Reviewed native edit | Existing exact `_edit_receipt`, target identities, approved values, source revision and native readback | No matching receipt/readback → unresolved |
| Foreground solve / AC stage | Exact job origin, native terminal success, matching retained run/manifest/input fingerprint and declared solve mode; finite required metrics | Wrong worker, stale/missing manifest or wrong source → unresolved |
| Queued network case | Exact parent/case/submission receipt and queue job, frozen baseline/candidate input, retained run evidence; same checks as native experiment refresh | Missing queue identity or replacement same-input run → unresolved |
| Report/delivery | Exact owned artifact ID, source-evidence revision and integrity, native successful artifact receipt | An existing filename or model URL alone → unresolved |
| Read or unsupported family | No verification adapter | Refuse `task_resolution_unverifiable`; no read rerun hidden in reconciliation |

A matching verifier supplies the result and a versioned `completion_proof` itself. The user/model cannot supply those authority fields. Missing proof leaves the step To review; no status promotion, lease/cap reset or replay occurs. `retry` remains a separate explicitly confirmed action after the native quiescence checks and existing no-replay constraints. Proof checks may read native state but cannot run solvers, change inputs, send reports or retrieve an unrelated replacement result.

### Record and dependency changes

Store `completion_proof` on the existing task step: verifier kind/version, exact effect/job/run/artifact IDs, source fingerprint/revision and verification timestamp. Retain the existing result plus native verification status; no second evidence store. Reconciliation acknowledgment belongs to the existing approval/event record, not to proof.

A legacy completed effect step without proof stays visible as a historical disposition. Do not replay it or retroactively label it verified. Before a dependent effect or `$step...` reference consumes it as verified evidence, block and require ordinary native reconciliation. Existing read results may remain readable as historical observations but cannot gain checked numerical authority from this migration.

A historical effect proof remains historical if later legitimate steps change inputs. Distinguish “this effect was verified at that step” from “these are the current inputs.” Use LC-23's input-fingerprint chain and current freshness checks; do not recheck an old edit against an intentionally changed current value and silently reinterpret its history.

Use the same native verifier contract on **normal `finish_step`, late-worker receipt handling, automatic queue/foreground settlement and explicit reconciliation**, not only on `resolve_task_step`. Generic handler success, `outcome=completed` or a stored late result without the required family receipt is insufficient. Under the existing native lock/store transaction, retain the exact receipt/proof and only then publish completed or permit dependent references/effects. A crash between native effect, receipt, proof and status publication leaves recoverable uncertain state; reconstruction must not infer success or replay from a missing write. After process loss, an empty in-memory session/worker registry is not positive proof of quiescence: require a native terminal origin/fence, or stay Needs review. No matching verifier means no verified effect-dependent completion.

### Completion condition

CMP-01–CMP-06 pass. Made-up completion results, unrelated jobs and legacy status words cannot advance a proof-dependent effect or become a checked claim. Normal verified edits/runs continue without extra model calls.

## 4. HS-03: bounded working context

**Owner:** existing durable conversation/history/compose/provider wiring, W9/S7. **Finding:** H03.

### Changes

Extend the existing durable thread's structured state and the existing per-turn composition path. Build a deterministic `working_context` packet on demand from current native records; do not add a task-profile endpoint, answer cache, private summary store or background summarizer.

The packet contains source-bound user goal/constraints/units, current selected scope, current versus discussed source refs, task/phase/next-step and missing assumptions. Native identifiers and current access decide what is included. A stored assertion that a source exists must be revalidated. User preferences/decisions, model suggestions and imported text retain their origin; they never become permission rules or approved native input values simply by being stored.

Initial packing constants in `history.py`, locally configurable/testable without a new settings endpoint:

- `WORKING_CONTEXT_MAX_BYTES = 6144` serialized UTF-8 bytes.
- `MODEL_HISTORY_MAX_BYTES = 65536` for the mutable conversational message segment, in addition to the existing 400-message cap.
- At most 12 source-reference descriptors and 12 user-decision descriptors in a packet; overflow is explicit and retrievable via existing source readers.

These are context-size limits, not new feature/spend caps or a promise about a model's context window. System/tool schemas and attachments must also be accounted for in request diagnostics. Use a local exact tokenizer only where the adapter has one; otherwise report token size as unknown and byte size as exact. Never treat a generic bytes-to-token estimate as a hard token guarantee or billing figure. Keep existing provider-reported usage semantics.

### Assembly algorithm

1. Pin acting owner/project/episode/generation and source revision; resolve current native refs and active task before reading optional history.
2. Include essential goal/constraint/unit descriptors and the pending action's sources in stable order. If essential content does not fit, block that dependent action with `context_required_unavailable` and expose a clarification/read route; do not silently drop a hard constraint.
3. Add bounded optional context, with omission counts/reasons and source locators. Keep raw artifacts behind ordinary authorized readers/spill.
4. Reserve the exact packet and message-wrapper bytes inside the 65,536-byte mutable-segment bound, then trim the oldest complete conversational turn groups until both message and byte bounds hold. Recheck the final serialized segment after appending the packet; the 6,144-byte packet allowance is a subset, not an extra 6 KiB above the history bound. Preserve tool-use/result pairing and existing trimmed-history notice. Do not truncate JSON arguments or pending approval content. If the newest required group alone exceeds the bound, return an explicit request-size/context refusal; do not loop or make another model call to summarize it.
5. Append the packet through the existing eventlog-backed message path; no direct mutation of `session.messages`. Use fenced per-turn data, not a mutable system prompt or trusted uploaded instructions. Record packet hash, origins and source dispositions in existing turn metadata.
6. Re-read source/access at actual tool admission. A context hash is not a permission, proof of freshness at execution, or an approval token.

Stable system/tool prefixes must remain stable when only live context changes. On episode resume, rebuild from the durable source records; old approvals never return. If no valid durable thread is present, use native current-source reads and explicit missing-context behavior; do not create a cross-owner memory fallback.

### Completion condition

CTX-01–CTX-06 pass, including Unicode/oversized history, repeated compaction, source revocation and untrusted text. Record request composition and actual reported usage for later evaluation; do not promise a percentage cost reduction.

## 5. HS-04: wider checked result integration

**Owner:** existing checked-results S7/S8, LC-20/21, card-workspace H2. **Finding:** H04.

### Changes

Extend `ResultView`/`list_result_views` in place with per-request coverage descriptors: requested metric, mapped profile, source mode, required source/window/entity and native availability reason. Return an exact scoped unavailable disposition for an unmapped quantity. `checked_profiles` is a registry relationship, not evidence that every quantity in that tab is checked.

Use the existing strict requested-slot/assurance contract to compile a result question into the admitted profile scopes. Do not whitelist raw `get_results` or every read tool as a shortcut. Optional profiles remain off by default; no setting is enabled by this PR or merely by landing a new adapter. A request that combines supported and unsupported slots preserves both dispositions under the existing publication policy.

The initial delivered adapter work is the existing S7/S8 scope, not a new universal analysis engine:

| Result family | Required native semantics and oracle | Exclusion kept explicit |
|---|---|---|
| Energy flow / load shape (S7) | Native energy totals and conservation residual ≤ 1e-6·throughput; any percentile uses actual weights or refuses; representative snapshots cannot become a full calendar-year monthly profile | Attribution paths, unsupported groupings and absent time coverage remain withheld |
| Investment / economics (S8) | Preserve shipped `investment-case/1` equity/project NPV and unique IRR, its cash rows, declared rate/basis, native residual checks and independent oracles. LCOE is a separate future adapter using the native finance definition and exact counterfactual | LCOE, payback, cover ratios and solved PPA price remain unchecked until their own acceptance gates pass; no guessed FX/price year, hidden cost completeness or general finance/risk ranking |
| Grid loading | Preserve existing exact LOPF proxy/profile, shipped `site-import/1`, and source-bound browser ACK where supported; retain the merged Link-dispatch fix and its numerical regression | No AC thermal/N-1/voltage claim from a P-only profile; the fixed native defect does not establish universal AC/compliance coverage |

The detailed calculation semantics in the pinned [checked-results audit](https://github.com/MLHaoChang/pypsa-eur/blob/089aea276c75b098c8d349cb5fa0eb4cb7267d64/.scratch/checked-results/audit.md) are normative where narrower than this table. If a required native engine fix or retained artifact is absent, keep the path unavailable until that owning slice lands and passes its independent oracle. Do not duplicate formulas in chat prose.

LC-20 plausibility results must be native rule records (rule/version, exact evidence ref, threshold basis, verdict or not-evaluated reason). LC-21 controls supported report chapters. Explain both through existing H2/S8 source/status text, at most 12 admitted facts. No evidence map, new advisor screen, model-defined safety verdict or blanket domain confidence score.

### Completion condition

ANA-01–ANA-06 pass for each enabled profile/view. Numerical source binding, interpretation semantics and browser data rendering are separately tested. An internally consistent result from a defective native engine cannot be promoted to engineering approval.

## 6. HS-05: cross-domain study execution

**Owner:** LC-23 slices 2–4, LC-05/12/19 and native job adapters. **Finding:** H05.

### Changes

Adopt the exact LC-23 brief schema, card checkpoint and limits. Use `ask_user` outside durable steps for the initial brief review, then `start_task` to persist. Existing task controls and project-rebinding tools remain excluded. A brief's phases partition step IDs in order; no hidden 25th step. Repeated same-owner/project/request with identical content returns the existing task; changed content conflicts.

A case label is not execution authority. First delivery admits at most two supported network cases via the existing experiment preflight/advance/refresh path. A one-step sweep submission under a brief must represent one case/job; a two-case brief uses two explicit admitted submissions. A direct multi-job step is refused at preflight until a native aggregate receipt adapter is implemented. Ordinary sweep behavior outside a brief is unchanged. More cases or other execution domains require a separately reviewed future brief/adapter, not an internal loop.

Preserve S2's shipped queue-backed settlement from each owned queue record and run/effect evidence. Extend only missing family/proof contracts and regressions. Do not pass a queue UUID to a foreground-only reader or certify whichever job happens to occupy live state. `wait_for_job` is already excluded as a brief step per LC-23; native waiting settlement avoids interpreting its observation timeout as solve failure.

Native family matrix:

| Family | First delivery | Required terminal interpretation |
|---|---|---|
| Foreground `fg:` solve / AC | Retain current LC-12 behavior and LC-23 proof | Completed only with exact run evidence; unknown/failed/aborted are distinct |
| Queue network case | Retain shipped LC-23 S2 adapter; harden residual proof where needed | Native case completion + exact queue/run receipt; started/queued is waiting |
| Adequacy/FMEA/MC/frontier/coupling/EH/investment `ad:` | Observation stays supported; automatic proof-dependent progression enabled only per native adapter | `done`/`met` can be success only with declared evidence and objective; unreachable/exhausted/refused are not success |
| Reports `rpt:` | Closing phase after LC-21/artifact adapter | Native successful artifact and source integrity; no file-name-only success |

On unexpected input drift, preserve receipts, mark `brief_stale`, and require revised brief review before the next effect. No changed-argument retry is automatic. User cancel stops future task steps; job cancellation is its own native action with acknowledgment. Dismiss waiting only withdraws the continuation. Retain current browser hold/dismiss behavior for unsent composer text, pending Choice/cards and active streams.

### UI changes

Extend existing `AssistantResultCard`, `ChatPanel` and `usePlanContinuation`: objective/cards/cases, current phase and bounded steps, source-linked “Open here,” Resume/Cancel and “Continue to <card>” at checkpoints. Copy follows LC-23: Complete, Next, Waiting for the run, To review, Out of date. Raw job IDs stay internal. One plan card is the only progress surface; card-workspace Next and the single H1/H2 entries stay in their existing homes. Keyboard focus and narrow-screen behavior must preserve the unsent draft and approval context.

### Completion condition

RUN-01–RUN-06 and the corresponding native LC-23 SC cases pass with fake/scripted providers, real native effects and synthetic solver fixtures. Live model quality is separate. A new family cannot be enabled by a UI button without its receipt/settlement adapter.

### Bounded validation and correction loop

Keep master's existing turn/session/daily limits, H2a explanation cap, goal-round bounds and pre-output transient-stream retry policy. They are delivered controls, not new features. Add the following native accounting to the existing task/budget records; no new planner or budget endpoint. This bounds **additional diagnostic work**, not the original reviewed step list, which keeps its own 12/24-step caps and ordinary dispatch limits.

For a given native task/step/attempt, allow at most three validation passes, two diagnostic reads and one correction draft. Across that diagnostic epoch allow at most six ordinary tool dispatches and four provider requests; lower existing limits always win. Debit before dispatch/provider invocation. A native waiter polling its exact submitted job is governed by its existing finite wait/watch limits and never starts a replacement job; polls are observations, not new validation passes. Waiting does not imply success or reserve another effect. Counters persist across turns, resumes, context trims, session reconstruction and provider changes. Missing/corrupt counters refuse dependent progression. Keep a task-wide diagnostic ledger: maxima are three validation passes, two diagnostic reads, one correction draft, six tool calls and four provider requests **per declared step**, summed across all attempts. An explicitly confirmed retry can create a new attempt but cannot reset that task-wide allowance. Starting/completing/changing a goal cannot manufacture a new task/epoch allowance. One provider request spanning several steps is charged once to the step active at request admission and to the task ledger; returned tool calls are individually charged to their actual step. Non-task diagnosis stays within the originating user turn and existing goal/session caps; automatic continuation is not a new user request or a new allowance.

Native progress means new matching evidence, a changed source revision or a successfully verified declared step, never different model wording or a repeated status poll. Record a stagnation key from canonical tool arguments (defaults applied), native target/family, source revision and native validation/refusal code. Two equivalent failures without new evidence end that epoch immediately; alternating equivalent tools for the same target/reason share the same key. Different arguments/tools still consume the same finite allowance, preventing evasion by paraphrase or ID churn. If equivalence cannot be established, enforce the absolute caps rather than guessing success. A source change invalidates the diagnosis and follows the existing stale/review path; it does not silently restart the epoch.

Validation passes and supplemental diagnostic reads are distinct categories: the allowance of two reads does not prevent three native validator calls, but both consume the six-tool total when dispatched as tools. Internal synchronous validation consumes a validation pass without pretending to be a separate tool call. Atomically reserve/debit counters before work across concurrent sessions, including each provider SDK retry/fallback or rejected output; debit a correction draft before publication. Tokens/usage reporting never controls these counters. Task-wide limits are `3*N`, `2*N`, `N`, `6*N`, `4*N` respectively for `N` declared steps, shared across attempts. Existing task records without a ledger may retain observation/history access, but cannot start diagnostic progression until an explicitly reviewed compatible plan establishes a native ledger; unknown prior usage is not zero or unlimited. Ordinary resume/retry/goal changes cannot initialize/reset it. A newly user-reviewed plan can establish its own ledger under ordinary admission; no automatic task-ID churn is allowed to bypass a stop.

The loop is read/propose → native validate →, if needed, bounded diagnose/correction draft → revalidate **after a separately approved correction**. The draft itself changes nothing. A changed constraint, parameter or run always needs its normal current card. Native validation is required before dependent effects and engineering claims; when no applicable validator exists, say not evaluated and block the unsupported dependent claim/action. There is no automatic effect retry after timeout/unknown/partial outcome, no repair of a corrupt record by the model, and no re-running an original solve to obtain proof. Stream retry may replay a provider request only under the existing pre-output transient policy; it is not permission to repeat a handler. On stagnation/cap/refusal, preserve receipts and present partial/To review with the blocker and one next action; never set completed, goal success or verified publication because a limit ended the loop. Provider usage being unknown cannot make call/round counters zero or lift any limit.

LOOP-01–LOOP-06 specify these invariants, including persistence, native validation before progression, lower-cap precedence and truthful termination. This is a proposed incremental guard over the existing loop, not a claim that Claude Code has these exact limits or that its private harness was inspected.

## 7. HS-06: journey evidence and diagnostics

**Owner:** existing inventory/control-matrix, owner journeys, eventlog/replay/metrics and CI. **Finding:** H06.

Add versioned evidence links to the existing generated matrix: journey/test IDs, exact source commit/schema digest, tested provider mode, command/result artifact and verified operations. A fixture is not “passing” merely because its file exists, was collected, or a neighboring case passed. Regeneration is deterministic; stale or missing evidence remains pending. Current metadata reports 276 tools/17 tabs/20 panels, 170 controls and 14 pending family links at this baseline; these are inventory counts, not coverage.

E2E-01–E2E-14 below define the required family journeys. Each uses synthetic owned projects and independent native assertions; failures/denials must verify zero unwanted effects as well as a truthful message. UI-changing slices require both Guided/Expert browser evidence and existing schema/semantic tests. Real provider selection tests remain separately labeled and opt-in; fake results cannot establish live language reliability.

Extend existing turn metadata and metric readers with bounded diagnostics: task/phase/tool kind, outcome/reason, source disposition, context byte counts/omissions, latency and provider-reported input/output/cache tokens. No raw source-file content, credentials or sensitive constraint text belongs in aggregate diagnostics. Keep native IDs in owner-authorized records; the ordinary metrics surface must not reveal another user's project IDs. No dollar-cost calculation, new budget endpoint, second dashboard or fabricated cache savings.

Replay an effectful trace only under isolated synthetic project roots/DB/storage with an injected fake provider and an execution allowlist. Default production replay must refuse effect tools. Forking a transcript never restores approvals. A source/schema change invalidates affected coverage links; do not silently bless all descendants of a green commit.

### Completion condition

All 14 E2E cases have exact native evidence, applicable CI succeeds on the implementation head, and coverage records reflect only those tested scopes. The finite checked gate still runs independently. Production/domain approval and population error estimates remain separate.

## 8. HS-07: trusted domain procedures

**Owner:** existing skill/workflow registry and W9/H1/H2. **Finding:** H07.

Add or refine procedures in the existing packaged skills/workflows for native readiness triage, supported critical-service decision studies and commercial evidence completeness. A procedure names only catalogue tools available at its declared schema version and points to the relevant native evidence/limitations. Static procedure text may guide sequencing but cannot change tool tiers, checkpoints, bounds or checked admission.

Before publishing a procedure, registry validation and a synthetic end-to-end fixture must verify its named tools, source queries, stop conditions and expected native outcome. User/project uploads named SKILL.md or containing hooks remain data; install-owned procedures keep existing precedence/refusal behavior. Read-only helper evaluation is not a grant to install arbitrary MCP commands or run scripts.

### Completion condition

SKL-01–SKL-03 pass. On-demand loading retains stable prefix behavior, one card entry and ordinary gates. General subagent spawning, recursive delegation and arbitrary code workers remain excluded from this implementation plan.

## 9. Lifecycle, compatibility and release

The [contract details](09-HARNESS-DATA-AND-STATE-CONTRACTS.md) and [implementation/test plan](10-HARNESS-DELIVERY-AND-TESTS.md) are normative. Implement in existing owners in their queued order, not as seven new product modules. Exact schema changes must update catalogue/wrappers/handlers, typed client models and the append-only error-kind manifest together.

Older task files stay readable; legacy unverified effect completion cannot become verified proof. Old discovery callers keep defaults. New optional profile adapters remain opt-in. Context packing does not rewrite raw history or mutate native inputs. Rollback must preserve task/proof/event records. An older binary cannot be assumed to enforce new fields: quiesce and disable assistant effect-serving, including MCP, before any incompatible downgrade, and retain a compatible reader/reconciliation route. Never downgrade records into unguarded version-1 calls or delete unresolved receipts.

A unit is complete only when its code, mapped tests, compatibility cases, source/approval/effect evidence, docs/release fragment and required CI/premerge pass. This PR delivers the specifications, not those runtime implementations or approvals.
