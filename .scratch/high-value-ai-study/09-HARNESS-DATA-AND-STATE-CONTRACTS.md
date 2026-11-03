<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harness data and state contracts

Status: residual implementation contracts over the delivered foundations reconciled in [11](11-CURRENT-MASTER-AND-REVIEW.md). Illustrative fields are not a claim that new APIs already ship. All additions extend existing modules/records. Preserve master LC-23's brief/tasks and Continue path.

## A. Discovery response additions

Existing `find_capabilities` fields stay. Proposed additive response shape:

```json
{
  "items": [{
    "name": "update_component",
    "safety_tier": "write",
    "required": ["component_class", "name", "attrs"],
    "eligible_for_project": true,
    "in_current_toolset": true,
    "support": {
      "disposition": "needs_inputs",
      "reason_kind": "target_required",
      "target": {"section": 5, "component": "StorageUnit", "field": "capital_cost"}
    },
    "schema_ref": {"kind": "component", "id": "StorageUnit", "sha256": "native-schema-digest"},
    "result_view_ids": [],
    "job_family": null,
    "evidence_kind": "edit_readback"
  }],
  "returned": 1,
  "has_more": false,
  "next_offset": null
}
```

Digest strings above are placeholders, not credentials. `support.disposition` is exactly `available|needs_inputs|manual_only|not_implemented|prohibited|not_applicable|unknown`; native missing data yields unknown with a reason. Available means observationally eligible, never authorized execution. `target` comes from existing native flow/field metadata, not guessed model links. No user identity or hidden record is exposed to resolve an unauthorized target.

`schema_ref` identifies existing native metadata. Domain-service schema pages include only actual request fields with native type/constraints/unit/basis if defined, plus editable/derived state. Unknown basis stays null with a reason. Canonical hashing uses complete sorted JSON, finite values and a stable schema version. Returning a page never truncates a required field constraint. For details=True, offset is a field offset in the uniquely resolved native tool schema; pass the returned schema hash as the optional schema_sha256 argument on every nonzero page. Search mode offset instead pages ranked capabilities. A changed version returns typed `capability_schema_changed` before using the next page. Use the existing result budget and typed error path; no new SSE frame.

## B. Brief and task contract inherited from LC-23

`start_task(title, steps, request_id, brief=None)`:

```json
{
  "objective": "Compare supported critical-service designs",
  "cards": ["capacity", "reliability", "hand-off"],
  "cases": [{"name": "Battery option", "experiment": "$exp.experiment_id"}],
  "phases": [
    {"card": "capacity", "steps": ["edit", "run"]},
    {"card": "reliability", "steps": ["read"]},
    {"card": "hand-off", "steps": ["export"]}
  ]
}
```

This illustrates phase partitioning, not an executable plan: actual steps must name admitted tools, and the experiment reference must resolve to an earlier real step. Objective ≤240 chars; ≤6 unique cards; ≤8 case labels of ≤60 chars; phases partition every step exactly once in order and use declared cards. A brief does not activate off-scope cards. Native run/case support may refuse even a structurally valid brief.

Plain plan: ≤12 steps and 16,000 chars of serialized steps. Brief plan: ≤24 steps and 32,000 chars; args ≤1,800 chars/step. Native experiment execution remains ≤2 network cases in the admitted first slice. `request_id` remains owner/project scoped and idempotent; changed content conflicts. Under a brief, no nested task controls, interactive Choice, project rebinding or `wait_for_job` step.

Proposed state behavior extends current names rather than creating a second state machine:

| Current state / event | Next observable state | Effect admission |
|---|---|---|
| Brief reviewed / start_task persisted | ready | Each next write/run still needs a card click |
| Matching call admitted | running | Exact args and fresh native gates only |
| Native asynchronous submission with receipt | waiting_job; step waiting | No dependent effect until verified settlement |
| Terminal native success with proof | next step ready, or completed | Valid proof dependencies only |
| Success status without proof / partial effect | needs_review; step uncertain | Block dependent effects; no replay |
| Native failed/aborted/unreachable/exhausted | failed/needs_review as native adapter defines | Correction draft only, no auto-retry |
| Next phase changes card | paused_at_card | Existing stream `continue_task_id` clears only this exact boundary; `resume_task` stays blocked |
| Inputs changed outside verified plan chain | brief_stale flag / To review | Revised brief review required for next effect |
| User cancel_task | cancelled | Stop future steps; running jobs retain their real state |

At `paused_at_card`, neither a model call to an effect tool nor a delayed continuation can clear the boundary. Preserve the existing authenticated `StreamRequest.continue_task_id` → `continue_at_card` path and server-written message. Add optional `task_continue_ref={boundary_generation, phase_index, revision, view_digest}` beside that field, not a new endpoint, tool or `resume_task` intent. A reference without `continue_task_id`, a message/attachment combination or conflicting continuation mode refuses before provider work. For newly revisioned tasks, a missing reference also refuses with a refresh action; legacy records must be rendered/refreshed into the current boundary snapshot before Continue, never silently bypass the new guard.

The current plan-card button sends the native reference it rendered. `boundary_generation` is a persisted monotonic counter identifying the exact paused phase, not a turn counter. Under the existing task lock the server rechecks current owner/project, scope/permissions, uncancelled status, source freshness, paused phase and the rendered semantic revision/digest, then consumes that exact boundary and saves atomically. Polls/diagnostics alone must not rotate the semantic revision; a changed plan, source, phase or authorization invalidates the view and requires refresh/review. Duplicate or cross-session delayed requests for boundary A cannot clear boundary B, even if its visible label matches. A consumed request can return a safe already-consumed/refused observation but never clear another boundary or trigger a provider/effect again. Model/MCP `resume_task` remains unable to clear a pause. The reference/digest is not a secret or effect permission. This acknowledgment clears only the boundary; every next write/run still needs its own card. Failed/uncertain outcomes do not become completed merely because they are terminal.

## C. Completion proof

Native-only additive field on an existing step:

```json
{
  "completion_proof": {
    "version": 1,
    "kind": "foreground_run",
    "verifier_version": 1,
    "attempt_id": "native-attempt",
    "job_id": "native-job",
    "run_id": "owned-run",
    "input_fingerprint": "native-fingerprint",
    "source_revision": "exact-native-revision",
    "verified_at": "native-timestamp"
  }
}
```

Allowed kinds: edit_readback, foreground_run, queue_run, experiment_case, report_artifact. The actual family owns required fields; missing IDs are not filled from latest live state. Access/account checks precede reads; native state is pinned during proof construction or checked before and after immutable evidence publication. JSON finite/type validation and exact ownership/origin apply. A proof's hash detects drift, not malicious compromise of the native evidence store.

Proof/status publication, diagnostic reservation and boundary consumption require a bounded **cross-process** owner/task transaction, using the existing `harness.durable.transaction` with fixed lock ordering and expected task revision/attempt. The current process-local `_LOCK` alone is insufficient when two workers serve the same task. Busy/unavailable storage refuses without starting work; do not hold the storage transaction across provider calls, solver work or user approval. Save the reservation first, execute through ordinary admission, then compare the native revision/attempt before publishing the result. A crash retains the reservation as unresolved, never replenishes it. Multi-process barrier tests must prove one boundary claim and no budget overdraw, as well as the existing multi-session cases.

`resolve_task_step(completed, result=...)` ignores caller result for verification and refuses without a native verifier. A read step is not silently rerun as part of resolution. `retry` keeps existing explicit confirmation and quiescence checks. Neither outcome resets a native experiment reservation or waives its no-replay ledger.

## D. Working-context packet

```json
{
  "version": 1,
  "project_id": "current-owned-project",
  "thread_id": "current-owned-thread",
  "generation": 3,
  "goal_refs": [],
  "constraint_refs": [],
  "units": {},
  "scope_cards": [],
  "current_refs": [],
  "discussed_refs": [],
  "task": {"task_id": "owned-task", "phase": 1, "next_step_id": "read"},
  "unresolved": [],
  "omitted": {"optional_records": 0},
  "packet_sha256": "native-computed-digest"
}
```

Descriptor entries carry native source ID/revision plus origin (`user_decision|native_observation|model_suggestion|imported_data`) and current disposition. A model suggestion/imported quote is never automatically promoted to a user decision or native observation. Packet ownership, generation and scope are server-derived. Do not interpret the packet hash as approval or merge it into a system prompt. Compute packet_sha256 over canonical finite JSON excluding the hash field (sorted keys, compact separators, UTF8); include the final hash/envelope in the size check. Packet ≤6,144 UTF-8 bytes; the entire mutable conversational segment including the packet ≤65,536 UTF-8 bytes and ≤400 messages, with complete tool pairs. These are proposed context constants, not token/cost guarantees. Required content that cannot fit blocks the dependent operation rather than being silently dropped.

Packet construction is pure read/format work except existing eventlog metadata persistence. It cannot change budgets, reservations, approval records, source snapshots or native model inputs. Existing source readers provide details on demand with fresh auth. Rebuilding after resume must invalidate old approval tokens and re-read access/freshness.

## E. Result-coverage and plausibility records

Extend the existing ResultView projection, without a new tool:

`coverage = [{metric_id, profile_id|null, source_mode, required_scope, disposition, reason_kind}]`.

Native requested-slot dispositions remain authoritative; this descriptive field cannot set answer_approved. Exact source/window/entity/basis is required before admitting a checked metric. An unavailable slot is not zero. At most 12 admitted facts enter result explanation; unmatched numbers or stronger conclusions are withheld.

LC-20 native rule records require `{rule_id, rule_version, evidence_ref, threshold, threshold_basis, verdict, unavailable_reason}`. Missing rule/threshold means not evaluated. Screening/fidelity limitations stay inline in existing result explanations, never a new confidence map or badge. Native formula definitions and independent oracles belong to their existing checked-results/native engine slices.

## F. Coverage links and diagnostics

Extend existing matrix fixture entries with a native-generated record:

`{journey_id, test_node_ids, source_commit, schema_digests, execution_mode, artifact_sha256, outcome, scope}`.

Allowed execution modes distinguish deterministic_fake, scripted_wire_native_solver, browser_synthetic and authorized_live. A source/schema change makes the applicable link pending until revalidated; never infer a pass by filename or inherit it from old CI. Keep failure/denial checks and declared exclusions beside each link. Full-coverage remains false while required links or gates are missing.

Diagnostics use existing owner-scoped turn records and aggregate counters only: timestamps/durations, tool/family/outcome, source disposition, context bytes/omissions and provider-reported token/cache fields. No source contents, credentials, cross-owner identifiers, fabricated dollar cost or inferred cache lifetime. Replay never executes production effects or restores approval records.

## G. Error and migration rules

All refusal kinds use the existing typed error path and append-only manifest. Proposed additions include capability_schema_changed and context_required_unavailable. Reuse shipped task_resolution_unverifiable, task_pause_needs_user, plan_continue_unavailable, brief_cases_not_runnable and invalid_brief where their existing meanings apply; do not introduce task_checkpoint as a synonym or a new state. Errors carry a safe message and existing native target when available, not hidden resource identity.

Version-1 files stay readable. The new reader must reject versions it cannot interpret. An older deployed reader might not enforce this rule: do not downgrade active write-bearing traffic to it. Quiesce/disable assistant effect-serving across chat and MCP before incompatible rollback, preserve unresolved receipts, and keep a compatible interpreter/reconciliation route. Never flatten new records into version 1. No migration rewrites historical user acknowledgments into verified results. No arbitrary `eval`, downloaded hooks, new provider SDK dependency or external model reviewer is required by these contracts.

Version-2 task records already ship. Add a native `harness_contract_version: 1` guard marker for the new proof/counter/boundary contract rather than treating every version-2 file as protected. Records without it remain inspectable but cannot automatically initialize unknown counters to zero or acquire verified completion. Require an explicit reviewed compatible plan or one persisted native migration disposition, carrying forward every receipt/uncertain origin; no repeated migration on read. Unknown future guard versions refuse progression. Before deployment cutover, quiesce old effect-serving workers and sessions across chat/MCP, drain or fence in-flight native effects and route all resumed guarded records through the compatible reader. Mixed old/new workers that ignore guards cannot serve writes. This cutover rule applies to rollout as well as downgrade; a model cannot repair/migrate the store.
