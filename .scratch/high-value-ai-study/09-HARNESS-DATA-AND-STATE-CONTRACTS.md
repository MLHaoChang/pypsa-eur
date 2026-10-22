<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harness data and state contracts

Status: proposed implementation contracts; examples are not currently callable APIs. All additions extend existing modules/records. Adopt master LC-23 exactly for brief/tasks.

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
| Next phase changes card | checkpoint | `resume_task` via explicit Continue clears only this boundary |
| Inputs changed outside verified plan chain | brief_stale flag / To review | Revised brief review required for next effect |
| User cancel_task | cancelled | Stop future steps; running jobs retain their real state |

At `checkpoint`, neither a model call to an effect tool nor a delayed continuation can clear the boundary. Add optional `task_checkpoint_ref={task_id, revision, view_digest}` to the existing authenticated StreamRequest/API client, not a new endpoint. The existing plan-card Continue button supplies the current native reference with its ordinary resume user message. The server revalidates owner/project/phase/revision/digest and binds a one-use checkpoint intent in the existing session for exactly that resume_task dispatch. The model cannot mint this stream field through tool args or replayed history. Keep resume_task(task_id) unchanged for legacy calls; at a checkpoint it requires that native intent. Under the task lock, consume it and advance the checkpoint revision atomically. Stale/duplicate Continue cannot skip a later boundary; direct MCP/model resume without the current user click remains blocked. The reference/digest is not a secret or effect permission. This acknowledgment only clears the boundary, never the next write/run card. Failed/uncertain outcomes do not become completed merely because they are terminal.

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

All new refusal kinds use the existing typed error path and append-only manifest. Proposed additions: capability_schema_changed, context_required_unavailable, task_resolution_unverifiable, task_checkpoint, brief_cases_not_runnable, invalid_brief. Reuse an existing equivalent kind if current master already has it; do not create synonyms. Errors carry a safe message and existing native target when available, not hidden resource identity.

Version-1 files stay readable. The new reader must reject versions it cannot interpret. An older deployed reader might not enforce this rule: do not downgrade active write-bearing traffic to it. Quiesce/disable assistant effect-serving across chat and MCP before incompatible rollback, preserve unresolved receipts, and keep a compatible interpreter/reconciliation route. Never flatten new records into version 1. No migration rewrites historical user acknowledgments into verified results. No arbitrary `eval`, downloaded hooks, new provider SDK dependency or external model reviewer is required by these contracts.
