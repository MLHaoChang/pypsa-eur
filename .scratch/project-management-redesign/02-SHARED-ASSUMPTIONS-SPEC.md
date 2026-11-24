# Shared assumptions across variants — proposed feature specification

Status: proposal based on the master audit, not implemented or domain-approved. Baseline `a68db6cfcf254df17a5b0d96da7a4c995f26423f`.

## User need and terminology

A Scenario and a Variant are the same model concept here. Use **Variants** consistently in the proposed user-facing management view; preserve Scenario in the API/database terminology. A variant is today's independent project copy, not a live configuration overlay.

Three distinct tasks:

1. **Edit one common assumption across existing variants.** Example: revise the common import-price assumption or battery cost without opening eight projects.
2. **Generate sensitivity variants.** Example: battery cost at three different values; reuse the bounded case-generation path and S5, not the common-value operation disguised as a sweep.
3. **Update a base and reapply variant-specific changes.** An explicit, conflict-checked operation. Never make all children inherit every future edit silently.

This proposal starts with task 1. Continuous shared-assumption inheritance is deferred. It would need linked versions, override rules and stale-result propagation, not merely a checkbox in a dialog.

## Placement

Canonical owner: **section 8, Scenarios & optimisation → Variants**. Use a full-height drawer or expandable workspace, not a tiny transient dialog for a large matrix.

The project's proposed Variants management view embeds/links the same feature and preserves source/context. An owning asset or commercial field can offer Apply to selected variants as a shortcut to the same drawer, with that field preselected. It does not become a second independently implemented parameter editor.

One canonical variant table should expose name, parent/base, explicit differences, state, active/last relevant run and supported actions. The scenario tree remains a lineage mode. No hidden extra scenario inventory with separate IDs.

## Interaction

1. Select explicit variants within one authorized project family. Root/base inclusion is opt-in, never inferred from All. For the initial bounded operation, propose at most eight target variants, matching the existing comparison plan; do not silently raise the current sweep's four-case execution cap.
2. Choose one or a few supported parameters from the existing field registry/editor. Start with typed scalar network inputs whose writer, units, validation, staleness and read-back contracts already exist. Additional tariff/finance/profile/process/horizon families are admitted separately.
3. Show a matrix of current effective values, proposed values, units and applicability. Missing/different assets are not guessed or created. Existing profile overrides are shown: changing a scalar load field may not change an effective time series.
4. Choose a clear application policy:
   - **Update matching/common values**: change only rows whose current value matches the explicit source assumption; preserve detected variant-specific differences, listed as exceptions. This is a guarded comparison, not a claim of proven inheritance where no override metadata exists.
   - **Set all selected values**: explicitly replace differing values too, with old→new preview and separate confirmation for overwritten variant differences.
5. Preview names/IDs, old→new values, preserved exceptions, conflicts, files/fields affected and which results will become stale. No write or solve occurs during preview.
6. Apply once. Display per-target read-back and a durable operation receipt. Locked, busy, inaccessible or changed targets are not silently skipped. Preflight refusals happen before any target write.
7. Offer **Run updated variants** separately. It previews exact target IDs, requested supported run kinds, queue/resource scope and ordinary approvals. Existing Run subtree can be reused only for the exact intended target set; do not run unselected descendants.
8. Show per-variant results/staleness. One failure does not invent results for that variant or prevent viewing successful completed cases.

Illustrative matrix, not actual project values:

| Assumption | Base | Variant A | Variant B | Action |
| --- | --- | --- | --- | --- |
| Common import-price scalar | 90 → 100 | 90 → 100 | 90 → 100 | Apply common revision |
| Battery cost | 200 → 220 | 200 → 220 | 150 (override) | Preserve B unless explicitly selected for replacement |

Units/price year/capital-cost basis must come from actual field contracts. These numbers do not prescribe annualized CAPEX, overnight cost or a tariff model. A price series is a pinned series reference/version, not a scalar to overwrite silently.

## Backend contract

### Preview request and result

An owner/project-scoped operation contains explicit target project UUIDs, expected revision/input digests, owning parameter descriptors, target entity mapping, typed proposed values, application policy and an idempotency/request ID. Organization/family scope is checked on every target. Local-mode identifier compatibility must be explicit.

Preview resolves target-specific field/asset identity, current effective values, units/basis, capability support, locks/busy state, pending unsaved contexts and dependency hashes. A matching component name does not prove identity across independently edited variants. Missing or ambiguous mapping refuses the affected parameter and prevents Apply until resolved.

Return an immutable preview digest binding the exact targets, old/new values, exceptions and dependencies. Revalidate ACLs, locks, revisions and old values at Apply; an earlier preview is not continuing authority. Shared parameter edits go through canonical validators and writers, not direct JSON/path assignment.

### Apply, failures and recovery

- Resolve or explicitly save/capture unsaved target work first. Never use a stale on-disk copy while a dirty resident context silently wins later through autosave.
- Acquire target locks in a deterministic order and recheck authority. Existing locks are project-node/user keyed; do not invent one root-tree lock or rely solely on frontend disabled state.
- Checkpoint each target with a versioned capture manifest and explicit included/excluded state, after fixing the audit's restore omissions. Snapshot quota/pruning must not delete operation rollback points.
- Stage and validate target changes before publishing; maintain a durable operation journal with target/source/pre/post digests, progress, receipts and error states. Multi-project, multi-file updates are not made atomic by a SQL transaction or Promise.all.
- Do not call an operation fully applied until all intended writes and read-backs are confirmed. If a mid-operation error occurs, perform only verified safe compensation, otherwise show Partial/Recovery needed and the exact completed/failed/uncertain targets. Do not mask a partial application as success or automatically replay an uncertain write.
- Mark affected results stale using the underlying input/dependency contracts, not modification time alone. Preserve historical evidence and unaffected results where the contract proves independence.
- A retry with the same request ID resumes/returns the same receipt; changed payload conflicts. Do not create extra variants or duplicate jobs through double-click/retry.
- Chat may prepare the same preview and request ordinary confirmation; it uses the identical service, constraints and evidence. The feature remains fully usable without a model API key.

### Revert this change

Support a reverse patch for the specific operation. Revert only if target values/dependencies still match the operation's recorded post-state. If later work conflicts, show the conflict and leave it unchanged pending an explicit resolution. A wholesale snapshot restore can discard unrelated later edits and is a separate action, with its own protection and scope.

History distinguishes parameter edit, checkpoint creation, job submission and result completion. Reverting an assumption does not cancel a running job, erase a failed run or make historical results current. No silent rerun on revert.

## Run and revert UX

Recommended actions are explicit:

- **Run selected variants**: run the current reviewed inputs using existing jobs and approved run kinds.
- **View this run**: open exact retained evidence; never restore inputs just to view results.
- **Restore this saved version**: capture current work, preview scope/differences, apply a verified restore; no implied solve.
- **Create variant from this version and run**: preserve the working design and execute an isolated source-bound copy after ordinary review. Requires an exact-version copy path; current restore-then-clone is not an acceptable hidden implementation.
- **Revert shared change**: reverse only that operation where safe, with conflicts shown.

A future one-action convenience may compose these steps after showing the full concrete plan. It must not hide saving, target changes, case creation or solve submission.

## Companion integration

The [chatbot integration specification](03-CHATBOT-INTEGRATION-SPEC.md) defines the same target-bound matrix/preview/effect/revert service in chat, with one visible project/variant/source context, native approval cards and durable read-back. PM-AI0–PM-AI5 map into the complete [delivery plan](04-IMPLEMENTATION-PLAN.md); no chat-only bulk writer or automatic solve is introduced.

## Existing code to reuse, and limits

- `ScenariosPanel.tsx`: variant forest, target selection, guards and queue behavior.
- `services/workflow_tools.py`: isolated case preflight, typed component allowlist, fingerprints, read-back/reuse principles; existing sweep is new-case creation, not this multi-target mutation API.
- Canonical owning forms/writers for assets, connection and commercial parameters; one field registry for Guided, Expert and bulk preview.
- `project_registry`, `project_acl`, `project_locks`, request-bound contexts: identity and access.
- `dirty_state`, existing per-kind staleness/source evidence, solve queue and terminal receipts.
- Snapshot infrastructure only after safe capture/restore-domain fixes; ordinary in-memory Undo is insufficient for a multi-project operation.
- S5/S6 and LC-17/18: case definitions and comparison expansion. Reconcile rather than create a second roadmap.

## Implementation slices

1. **PM-R0, safe checkpoints/revert:** dirty-state protection, presence manifest, directory policy, protected rollback retention, truthful errors and exact version identity. Meaningful HTTP tests for F01–F03 must fail first, then pass under the clarified contract.
2. **PM-V0, variant/source summary:** one canonical table in section 8 with correct inputs/results/job identity and an explicit relation to project management; no new parameter mutation.
3. **PM-B0, read-only common-assumption matrix:** bounded targets, effective values and conflict/missing mapping; preview only.
4. **PM-B1, one supported scalar family:** preview/apply journal, locks, expected revisions, checkpoints, read-back, staleness and guarded reverse patch. No generic file-path edits, automatic inheritance or automatic solve.
5. **PM-B2, deliberate queue and comparison:** Run updated variants over the existing supported orchestrator, current limits, exact targets and per-case completion. Base+N output comes from S6, not model-generated arithmetic.
6. **PM-B3, additional parameter families:** each adds source/units/basis, writer/read-back, migration/portability and independent tests before being selectable.

## Acceptance matrix

| ID | Condition | Expected result |
| --- | --- | --- |
| BV01 | Same value across three selected variants | Preview shows all exact old/new values; one reviewed apply; source base changes only if selected. |
| BV02 | One variant intentionally differs | Common-value policy preserves/lists it; Set all requires explicit replacement preview. |
| BV03 | Missing, renamed or different asset | No guessed target, hidden creation or partial silent skip; mapping must be resolved. |
| BV04 | Scalar with existing time-series override | Effective value/scope shown; no claim scalar edit changed the profile. |
| BV05 | EUR/USD, price-year, cost basis or component-specific units differ | Explicit valid conversion with basis evidence or refusal; no bare-number copying. |
| BV06 | Dirty resident project or pending editor save | Capture/save resolution or refusal before any writes; later autosave cannot resurrect pre-batch data. |
| BV07 | Foreign org, revoked membership, locked/busy target | Server rechecks each target; refuse before writes or record true later recovery state. |
| BV08 | Target changes after preview | Digest/old-value conflict, no overwrite based on stale preview. |
| BV09 | Crash/cancel/disk failure between target commits | Durable completed/failed/uncertain ledger; verified compensation/recovery; never false all-success. |
| BV10 | Double-click/retry same request | Same receipt; no duplicate writes, copies or jobs. Changed payload conflicts. |
| BV11 | Reverse patch after unrelated/conflicting later edits | Preserve unrelated work; conflict when postconditions no longer match; no blind full-project restore. |
| BV12 | Snapshot quota and rollback retention | Operation checkpoints remain until safe resolution; retention behavior is explicit. |
| BV13 | Batch input edit with old results | Affected current results stale; immutable historical evidence retained; no automatic solve. |
| BV14 | Run exact three selected variants, one fails | Only selected targets/jobs; each real terminal state; successful results readable and failed metrics unavailable. |
| BV15 | More than current sweep/compare caps | Explicit supported limit or different admitted path; planned eight-case compare does not authorize unlimited execution. |
| BV16 | Export/import/fork after common edit | Declared changes, source/base and receipt references travel under the declared portability contract or show unavailable; no invented provenance. |
| BV17 | Guided/Expert/chat entry | Same preview/service/IDs and ordinary confirmations; user can perform all actions without an LLM. |
| BV18 | Base edited after an independent fork | Existing fork inputs/results do not change silently; show baseline relationship explicitly. |
| BV19 | Run a saved version | Isolated exact-source copy where supported; working branch unchanged; ordinary queue/job receipt. |
| BV20 | Restore fails partway or required evidence is corrupt | No success badge; prior protection/recovery and explicit invalid/uncertain state. |

Independent domain/design review is required when a new parameter family reaches engine, financial or adequacy semantics. Existing repository UI/engine/premerge gates are not waived by this proposal. No live equipment/production authority or paid calls are introduced.
