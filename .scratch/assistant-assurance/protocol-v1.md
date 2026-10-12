<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Frozen minimum assurance protocol, version 1

Status: observable design contract for reviewed acceptance tests; UNIMPLEMENTED.
Applies to the [spec](spec.md). Refinements require review and a documented
compatible addition or version change. Do not freeze internal Python classes.

The [reliability revision](reliability-contract.md) proposes stronger admission
semantics and additional request-slot/metric/formula/check metadata. Those fields
and the computed-block shape are **not yet frozen or implemented**. P0 must review
exact schemas, compatibility and whether a version increment is required before
fixtures or code rely on them. Existing v1 fixtures cannot certify the added
requirements. Never use an unknown-field allowance or legacy client fallback to
bypass them; strict release requires the reviewed negotiated contract.

## Negotiation and admission

`POST /api/chat/stream` accepts `assurance: {"version": 1, "mode": "strict"}`.
The existing session/profile/message/input_mode fields keep their meaning.
Unknown assurance versions/modes/malformed options produce HTTP 422 with
`detail.error_kind: unsupported_assurance_version|invalid_assurance_request` and
no model/tool call. Strict mode unavailable on a disabled server produces HTTP
503 `detail.error_kind: assurance_unavailable`; it must never silently emit raw
model text. A server configured strict never permits a client to bypass the gate.
Acceptance invocation sets `PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict` before app import.
Configuration accepts disabled/shadow/pilot/strict; environment name is a deployment
selector, not a provider change or authentication bypass.

Existing HTTP 401/403, session ownership, CSRF and body-error behavior stays.
`session_init.assurance` identifies `{version:1, mode:"strict", state:"checking"}`
or an explicitly legacy/unvalidated state where rollout allows legacy behavior.
Client-supplied verdict/source ownership/check badges are never authoritative.

## Private model draft

The last no-tool assistant message is a bounded JSON object:

```json
{
  "schema_version": 1,
  "blocks": [
    {
      "id": "load-input",
      "kind": "observed",
      "source": {"tool_use_id": "read-load", "path": "/p_set"},
      "entity_id": "L1",
      "metric": "p_set",
      "value": 5,
      "unit": "MW"
    }
  ]
}
```

`source` is either `{evidence_id, path}` or `{tool_use_id, path}` referring to a
server-captured result in this turn. The latter is a convenience resolving to a
server-issued evidence ID, not certification by an arbitrary tool-use name.
`path` is an exact JSON pointer within captured data. The source adapter supplies
the actual project/run/entity/metric/unit/scope; forged entity/unit or claimed
value mismatch blocks that claim. Client/model IDs cannot cross actor/project scope.
Examples use existing `get_component` returning static fields, not dynamic output.

Block kinds: observed, computed, action, interpretation, proposal, limitation,
clarification. Computed blocks contain a registered formula ID and source refs;
action blocks refer to actual receipts. Arbitrary formula code and unknown kinds
are refused. Optional narrative/caption is NOT independently admitted by a valid
claim ledger. The first executable tranche exercises observed blocks/invalid
drafts/action status and API surfaces; later computed/reviewer shapes need a
reviewed additive schema before fixtures, and stay pending in the coverage ledger.
Duplicate IDs, malformed fields, more than 20 blocks and >64 KiB serialized draft
are rejected. Strict fallback is server-generated and clearly limited/unavailable.

## Public event ordering and approved record

Existing real tool lifecycle frames still occur with typed status projection.
Private provider text/thinking never becomes public `token`/`thinking` before
approval. Thinking deltas are not exposed even after approval. SOFTWARE-authored
`answer_progress` may precede final approval:

```json
{"version":1,"turn_id":"opaque-turn","stage":"checking_claims","status":"running"}
```

Stages: retrieving_evidence, waiting_approval, waiting_job, building_artifact,
checking_claims, reviewing_interpretation, ready, blocked. Statuses:
pending/running/completed/partial/failed/cancelled. Optional source_action_id,
started_at/completed_at exist only for actual transitions, no chain-of-thought.

Durably commit owned answer text/blocks+receipt BEFORE any approved public output.
Then emit exactly one `answer_approved` for this answer, followed by approved
`token` deltas and `turn_done` (or an interrupted delivery recovered by GET):

```json
{
  "version": 1,
  "answer_id": "opaque-answer",
  "turn_id": "opaque-turn",
  "verdict": "limited",
  "receipt": {
    "policy_version": "1",
    "content_sha256": "64-lowercase-hex",
    "evidence_ids": [],
    "checked_block_ids": [],
    "limitations": ["No recorded result supports that claim."],
    "reviewer_status": "not_required"
  }
}
```

Verdicts are passed/limited, never blocked for published text. A limited owned
fallback may have no evidence. `reviewer_status` is not_required/passed/failed/
unavailable; it cannot claim passed when no reviewer ran. Hash is SHA-256 of exact
UTF-8 concatenated public answer text. `turn_done.answer_id` matches approval.
Failed/aborted turns without public fallback may emit existing typed error/done
events instead; they must not mark unchecked model text approved.

`GET /api/chat/answers/{answer_id}` returns HTTP 200 with version, answer_id,
turn_id, text, blocks, verdict and the same receipt. Unknown/unowned/private draft
IDs return 404 without source existence leakage. A known owned record with lost,
corrupted, expired or revoked evidence returns 409 with `detail.error_kind:
answer_evidence_unavailable`; no stale checked text or metadata values leak.
Auth/ACL denial still follows 401/403 where identity/access itself is refused.
Repeated authorized GET is idempotent and executes no model/tools. Public history
and export retain answer_id/receipt alongside approved content in the existing
`assistant` text-block list; concatenating those text blocks gives the approved
answer text exactly. Legacy entries say unvalidated.

## Evidence, outcomes, navigation and artifacts

`GET /api/chat/evidence/{evidence_id}` returns 200 with version, evidence_id,
project_id, source_kind, scope, facts, validity and safe view/artifact references.
Facts have path/entity_id/metric/value/unit; validity names outcome/as_of/coverage
and warnings. Unknown/unowned IDs return 404; known expired/corrupt evidence 409
`detail.error_kind: evidence_unavailable`. Recheck ACL before response/cache reuse.

Tool-result metadata adds `_assurance: {version:1, outcome, evidence_ids}` where
outcome is submitted/running/completed/partial/denied/cancel_requested/cancelled/
failed/uncertain. Schema/source adapters, not the model, create this metadata.
An unknown status never maps to completed. UI tools initially return submitted.

`POST /api/chat/ui-ack` accepts version, action_id, turn_id, binding_generation
(integer), and target (the allowlisted panel/project/run/revision/window values
from the expected action). HTTP 200 returns action_id and status acknowledged;
unexpected/mismatched/stale actions return 409 `detail.error_kind: ui_ack_mismatch`;
malformed types/version return 422; cross-owner actions return 404. CSRF rejection
retains existing 403 error shape. Sending navigation is not an acknowledgement.
Target schema freezes with actual GUI mappings in P5, not guessed browser paths.

Chart/approved-artifact result has `_assurance`, `evidence_ids` and a `manifest`
containing schema_version, project_id, source_kind, exact dataset, render_spec,
dataset_sha256, render_spec_sha256, artifact_sha256 and validity. Exact dataset
contains column/row indices, units and numeric values; larger data may use an
authorized immutable dataset URL plus its hash. A new chart evidence record binds
data/spec/bytes; downloads must not release unapproved or inconsistent artifacts.
Input data has source_kind configured_input. Generic uploaded files without this
chain are explicitly unvalidated source files and are not assumed computed results.


### Minimum inline chart and delivery integrity contract

For bounded inline datasets, `dataset` contains `index` (ordered row IDs),
`columns` (ordered column IDs), `values` (row-major finite numbers), and `units`
(column ID to unit). A static G1/p_nom fixture has exactly:
`{"index":["G1"],"columns":["p_nom"],"values":[[20]],"units":{"p_nom":"MW"}}`.
`render_spec` describes the actual chart kind, title, axes and series/column mapping;
its shape may grow additively but must describe the rendered image faithfully.
Hash `dataset` and `render_spec` independently as UTF-8 JSON with lexicographically
sorted object keys, compact separators (`,` and `:`), unescaped Unicode, and no
NaN/Infinity. Preserve array order and normalize integral numeric data to integers
(e.g. 20 rather than 20.0); do not stringify numeric values. `artifact_sha256`
hashes exact delivered bytes. No check may merely assert that a hash is present.
Delivery `manifest.json.files[]` retains each chart's complete manifest under
`assurance`; each entry's existing `archive_path` identifies the bound bytes.
Local provenance sidecar layout is not a public protocol. Legacy-storage fault
injection may exercise that existing layout as a clearly labeled regression,
separate from future manifest-contract tests.
