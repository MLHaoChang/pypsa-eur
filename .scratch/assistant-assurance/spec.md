<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant answer assurance

Status: original design independently reviewed; reliability additions proposed
after source audit and pending independent review. No assurance gate is implemented.
The original review does not approve these additions or implementation/release.
Baseline: master `d43809aed8728c844cc4d5119785b68801c91a8e` (dictation merged).
AI-generated specification; review findings and acceptance evidence are separate.

Read the [implementation plan](../../docs/superpowers/plans/2026-10-10-assistant-answer-assurance.md),
[test contract](test-plan.md), [handoff](session-handoff.md), and
`pypsa-gui/backend/harness/README.md` before implementation.
The [reliability contract](reliability-contract.md) adds required request-scope,
metric/calculation, raw-validity and mandatory action-guard requirements. Its
contracts must be reviewed and frozen before new acceptance fixtures are written.

## Goal and assurance boundary

Every assistant output must accurately communicate what the application actually
observed or did, its evidence and limitations. All existing authorized harness
functions are in scope; load flow, charts and sensitivity are examples, not a
restricted feature list. Preserve Anthropic/OpenAI/compatible provider parity,
normal authorization, confirmations, project isolation and response budgets.

Authentication identifies the actor/source; authorization permits access;
integrity checks detect changed artifacts; evidence matching establishes that a
claim matches selected recorded data. None establishes that user inputs, external
data, a solver formulation or an interpretation are scientifically correct.
Never label an answer "guaranteed correct" or a hash as scientific validation.

Public labels: **Matches recorded evidence**, **Computed from recorded evidence**,
**Reviewed interpretation**, **Proposed action**, **Unavailable**, **Out of date**,
and **Legacy answer: not validated**. Each label has a precise policy and scope.
Input charts say "configured input", not "measured result". Model review remains
fallible; explicitly disclose solver warnings, mode, coverage and assumptions.

## Current gaps to close

- `harness/loop.py:500–505` sends text/thinking deltas before any factual check.
  The router and ChatPanel forward/display them; `turn_done` triggers speech.
- Assistant blocks enter provider/session history before completion. Redaction
  on disk is not validation; reload, summaries and future voice need the gate too.
- `tool_result` means the handler returned, not the requested effect completed.
  The frontend's generic checkmark and model-facing `ui_navigated: true` can
  overstate queued/failed/partial outcomes or unacknowledged navigation.
- `get_study_evidence` implements bounded/fresh source retrieval in some domains,
  not immutable historical evidence across all families or scientific certification.
- `create_chart` copies numeric application data under a lock, bounds rows and
  checks freshness/fingerprint; it lacks an immutable exact plotted dataset
  manifest covering full indices, transformations and render/data association.
- `build_delivery` checks file bytes but an independent JSON sidecar is not an
  authenticated dataset-to-render chain. Reports' numeric audit flags rather than
  blocks, matches values across all facts, and cannot validate semantic attribution.

## Required public contracts (version 1)

The minimum observable protocol is frozen in [protocol-v1](protocol-v1.md) before
acceptance tests. Subsequent changes require a reviewed contract/version change,
not weakening assertions to match unchecked legacy behavior.

### Evidence record

Server-created `EvidenceRecord`: schema/policy version; opaque `evidence_id`;
actor/org access scope; project identity; run/job/revision; source kind and adapter
version; captured time and immutable input/result/content hashes; data scope
(section/entity/metric/window/period/filter/page); value/unit/type; solver mode,
terminal outcome, convergence/validity/coverage/warnings; complete/partial/missing
markers; safe typed UI target and artifact references; retention state.

Capture typed full handler data BEFORE model-facing truncation/omission. Capture
raw validity/missing masks before UI serialization or zero-filling. Legacy
sanitized numbers alone cannot establish a checked metric. Apply metric-specific
validity, coverage and numerical invariant checks; a completed solve and a
matching response do not establish numerical correctness. A full
dataset may remain in the authorized artifact store; send only bounded selected
claims to the model/reviewer. Distinguish complete-source aggregates computed by
software from page-local extrema/counts. Null/missing is not zero; do not match a
value found elsewhere as proof of its entity/metric. Bind evidence IDs to scope;
model-supplied IDs, URLs, paths, hashes, "verified" flags and annotations are
untrusted. User uploads/assertions retain that origin and cannot certify a solve.

Reuse [live-voice issue 06](../live-voice/issues/06-run-evidence-and-context.md)
for immutable run storage and historical APIs; do not create a competing run
store. Completed historical evidence is valid *as of its recorded run*, even
when current inputs differ. "Current/latest" resolves once to an explicit run
and revision; never silently substitute newer results. Legacy results without
required provenance are unavailable for a verified claim until recomputed or
explicitly admitted as a labelled legacy source by a documented policy.

### Draft and claims

Build a server-owned request scope and required result slots. Check that approved
blocks answer the requested metric/project/run/window and include required warnings.
Domain key-results profiles supply documented defaults and metric definitions;
unresolved ambiguity yields a clarification, unavailable required slots a limited
answer. Correct facts about a substituted quantity do not satisfy the request.

`AnswerDraft` contains bounded typed blocks: observed fact, computed fact,
action outcome, interpretation, proposal, limitation, clarification and evidence
navigation. Factual blocks reference exact `evidence_id` plus field/entity/window,
or an authorized action receipt. Computed facts name a registered deterministic
formula and dependencies; no model-authored arbitrary expressions or code.
Comparison blocks require compatible modes, units, periods and denominators.
Computed blocks use versioned metric definitions and bounded acyclic dependencies;
the server computes from raw evidence and renders the value. Freeze their schema
before implementation. Energy weighting, objective-versus-cost, signed deltas,
denominators, uncertainty and missingness follow the reliability contract.

The server renders factual numbers, units, names, statuses, captions and source
links from validated blocks. A correct claim ledger next to contradictory free
prose is invalid. Never approve arbitrary narrative just because its numbers
appear somewhere in data. Non-template interpretation is separately reviewed and
labelled; reviewer success is not a fact guarantee. General explanations use
explicitly scoped, versioned reference material or are labelled general guidance.
Clarifications and courtesy text cannot serve as a bypass for factual claims.

Schemas are vendor-neutral Pydantic/JSON schema at the harness seam. Models can
return JSON text through the existing stream contract; provider-native structured
output is an optimization, never a required OpenAI-only capability. Invalid JSON,
duplicate claim IDs, too many blocks, unresolved references and unsupported claim
types do not publish as trusted text. Existing report number audit remains a
diagnostic, not the admission authority.

### Validation and publication

`ValidationDecision`: `passed|limited|blocked`, exact checked block IDs,
evidence references, limitations, checker/policy versions, reviewer status,
timings and generation. `passed` means specified mechanical checks passed;
it must not be presented as universal correctness. Interpretations expose their
different assurance scope even when accompanying checked factual blocks.

1. Admission keeps all existing tool permissions/confirmations/locks/limits.
   Required effect guards validate offered schemas, request scope, domain bounds
   and exact confirmed arguments. Guard absence/error blocks the handler; existing
   courtesy pre-dispatch validation remains distinct. Recheck under commit locks.
2. Capture full results and action receipts; canonical adapters classify
   requested/approved/submitted/running/completed/partial/denied/cancel_requested/
   cancelled/failed/uncertain. A successful HTTP/tool return is not completion.
   Read back requested effects from actual saved/foreground state and bind the
   receipt to operation/arguments/before-after revisions. Resolve ambiguous
   effects before any retry; model-supplied reconciliation is insufficient.
3. Buffer ALL model prose, including pre-tool comments and thinking deltas.
   Immediately stream bounded SOFTWARE-generated progress from real events.
   Pre-approval tool lifecycle projections omit unchecked prose and unapproved
   artifact links. Raw tool cards cannot bypass answer/artifact admission.
4. Validate draft schema, scope/ACL, source and metric attribution, units,
   signed values/precision, freshness/as-of status, solver validity and actions.
5. For configured interpretation risk, invoke a bounded read-only reviewer via
   the provider seam with only necessary authorized evidence. No tools, mutations,
   raw secrets or hidden planner reasoning. Reviewers can disagree; disclose it.
6. Permit at most one generation repair after an invalid draft. Repair and review
   consume ordinary request/time/token/spend caps; never replay executed tools.
   Reviewer timeout/failure produces validated factual fallback plus the explicit
   unreviewed/withheld interpretation status. No evidence yields honest unavailable.
7. Revalidate ACL, binding generation, evidence revision and artifact retention
   just before publication. Revocation or mismatched current context blocks output.
   An explicit authorized historical reference retains its as-of scope.
8. Durably commit an `ApprovedAnswer` and its receipt atomically BEFORE emitting
   any approved tokens, artifact links, public history entry or speech event.
   Its content and receipt share
   `answer_id`, `turn_id`, generation, content hash, policy and evidence IDs.
   Only its public projection reaches UI, reload, summaries, exports and TTS.

Separate private provider protocol trace (including tool-use/result pairs) from
public approved history. Private draft text never becomes an accepted fact in
conversation summaries or public history. Keep the minimum scoped trace needed
for ongoing tool continuation; subsequent turns use approved content and valid
portable protocol state. Bounded retention, redaction and owner-only access apply;
raw thinking is not persisted or exposed as the user confidence trail.
Cancelled/stale turns never publish late answers. Network/SSE delivery is not
atomic. Interrupted delivery allows authenticated idempotent GET of the already
committed answer; never regenerate or replay tools merely to recover delivery.
Uncertain commit blocks publication until reconciled; answer IDs deduplicate replay.
Basic approved-only live UI, history, summary, export and current TTS enforcement
is a P2 requirement. P6 extends it to durable restart/compaction/reviewer policy.
Until P3 outcome adapters exist, P2 withholds unsupported completion claims.
Evidence and approved-answer retrieval revalidate referenced sources/permissions;
revoked, corrupted or expired references cannot retain a current checked label.

### Artifacts and navigation

Charts, tables, reports, exports and deliveries use a captured authorized dataset
and a server-owned render specification: exact entities/indices, units, axes,
aggregation/filter/normalization, precision, missing-value policy and provenance.
Store canonical data hash, render-spec hash, artifact-byte hash and evidence scope
in a manifest published atomically with the artifact. Changed/swapped data,
sidecars, labels, captions or bytes invalidate the association. Exact deterministic
data validation is primary; models review chart choice/interpretation, not certify
numbers by inspection. Audit misleading aggregation/axis/units and clipped scope.
Release artifacts only after checks; quarantine failures outside normal downloads.
Recheck authorization at every evidence/artifact read. Links are server-issued
same-application opaque references; checksums are not externally signed trust.

Evidence cards show source/run/time/entity/window/unit, checked facts, limits and
"Open evidence"/"Download source data". Navigation uses a typed allowlisted
target and `action_id`/binding generation, no model-generated browser URL.
Only an authenticated acknowledgement matching the expected target/revision
counts as application view-state acknowledgement. It cannot prove the user read
or visually understood the view. Navigation sent/acknowledged/unavailable are
distinct; timeout, absent historical UI, closed browser and superseding user
navigation yield an honest source link/status, never fake "opened".

### Visible process

Show brief verifiable steps: request understood, retrieving selected evidence,
checking scope/units, waiting for approval/job, building artifact, checking claims,
reviewing interpretation, ready/partial/blocked. Each has real state, timestamps,
source action IDs and expandable tool/check explanations. Do not expose chain of
thought or fabricate a reviewer, reasoning trace, percentage or elapsed stage.
Long jobs and monitoring reuse the live-voice job contracts. Stop answer, stop
speech and cancel job remain distinct. A review cannot approve an engineering edit.

### Scope, performance and release

Map ALL tools/families and output channels using the existing
[whole-application coverage contract](../live-voice/application-coverage.md).
Every adapter identifies effects, source/validity rules, artifact requirements and
unsupported limits. Unknown/unmapped factual results cannot acquire a checked label;
they may be displayed only as explicitly unvalidated raw details without success
badges while rollout is limited. Strict release removes that bypass for factual
assistant publication; the app's ordinary authorized result UI remains available.

Cache immutable evidence/validation by actor/org scope, source/run/content revision,
claim/render spec and policy/checker version; recheck ACL every hit. Share only
public schema/reference caches. Cached reviewer output is never a permission grant.
No model polling. Bound draft/evidence/block counts, queue/workers and storage.
Use one authoritative application worker initially; multi-worker guarantees depend
on the existing durable owner/lease design. Separate acknowledgement latency from
grounded-answer latency. Initial targets (measurement gates, not current claims):
software acknowledgement p95 <=300 ms; warm deterministic validation overhead
p95 <=200 ms for <=20 claims over <=64 KiB bounded evidence; added model review
p95 <=3 s with a hard 5 s timeout/factual fallback, measured per deployment/model.
Existing end-to-end voice targets still include STT/tools/TTS and may need revision
if measured reviewer latency conflicts. Never skip checks to meet a latency target.

Rollout: disabled -> isolated shadow (no user assurance label) -> explicit pilot
with limited supported adapters -> strict supported subset -> full mapped release.
Shadow records only bounded sanitized diagnostics; it is not pre-display protection.
Rollback labels legacy output unvalidated and disables approved-answer voice,
without bypassing authorization or resurrecting private drafts. Do not advertise
whole-application assurance until all required mapped families pass their gates.

## Delivery and review

Use the numbered issues under `issues/`, the [test matrix](test-plan.md), and
vertical red-green slices. The transferred plan calls for acceptance tests for all
phases before implementation; run an explicit opt-in red suite after publication,
then refine contracts without weakening behavior as implementation proceeds.
Independent review must resolve blockers before this plan is marked approved.
