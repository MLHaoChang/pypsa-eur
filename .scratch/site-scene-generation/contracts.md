<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Contracts that implementation must preserve

Status: proposed normative contracts; no new endpoint or storage behavior exists yet
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## 1. Geometry before an electrical model

A saved, authorized Project is necessary for durable scene storage. Its network can be empty. Entering coordinates and confirming a boundary creates a Site envelope with `buses: []` through validated site persistence, not a dummy electrical model. Generation can then produce native buildings without any network write. The branch/Bus checklist remains relevant only to the existing model-driven generation action.

The new scene render path must work without yard/bus objects. Later explicit associations add native model references; a scene alone does not satisfy any solve preflight requirement or fabricate energy inputs.

## 2. Single authority and atomic revision

Unadopted Sites retain the legacy sites.json authority. A scene-enabled Site has immutable generations, each containing its complete Site envelope and scene state, and an atomic `sites/<site_id>/scene/current.json` pointer. The pointer contains revision, generation manifest hash and last applied proposal/receipt reference. It is the sole commit point for that Site. All transforms, including component gizmo positions, are authoritative in the pointed generation; legacy placements are never a second writer.

Explicit adoption locks the project, verifies the current sites.json digest, snapshots the exact legacy envelope/placements into a first generation, fsyncs the complete generation and atomically commits its pointer. Opening/GET does not adopt. No rewrite of sites.json is needed for that commit. For a newly saved empty Site, a crash before the first pointer leaves a valid empty legacy plot, not a claimed generated campus.

GET sites projects enabled Site envelopes from current generations and other Sites from legacy storage. Legacy whole-document PUT must inspect the backend's enabled Site set, even if the client omits those IDs. A request that changes/deletes an enabled Site or lacks the required current revision is refused with a refresh/upgrade conflict. It cannot remove the directory/pointer or reset a placement. Never trust a client-supplied scene_enabled flag. Provide a versioned native write path for modern clients; legacy clients may inspect enabled Sites but cannot silently edit them.

Updated sitesStore performs an explicit handover at adoption: cancel pending legacy debounces, reconcile or reject unsaved old edits, install the canonical revision, and route gizmo/Arrange/reset/boundary/membership/flush/unload operations for enabled Sites through the same module. beforeunload cannot bypass expected_revision. A stale localStorage fallback remains an unsaved local draft, not a recovery authority.

Apply locks the project, validates ownership/current authorization, expected pointer revision, source envelope digest and target/artifact identities; writes/fsyncs a complete new generation with its receipt; atomically replaces the pointer; then releases the lock. Readers see the old complete generation or new complete generation. Cleanup can remove unreferenced failed generations only after checking live readers/jobs/receipts. The receipt is committed with the generation, so a disconnect after commit is read back before retry. Repeated proposal application returns that receipt; the same proposal ID with different inputs is refused.

Snapshot/export takes one immutable pointed generation under the project lock. Concurrent apply cannot mix new geometry with old bindings or old envelopes. Existing rename/delete/model-target hooks must enter this module for enabled bindings and placements; they may not mutate legacy JSON behind it. Network transactions remain separately admitted native operations, with unknown cross-operation outcomes disclosed rather than replayed.

Required race fixtures: gizmo save versus proposal Apply; old debounce finishing after adoption; browser-unload whole-document PUT versus new scene generation; old localStorage fallback after restore; snapshot/export versus Apply; disconnect after pointer commit; failed write before pointer commit. At most one expected revision succeeds and the previous valid generation is always recoverable.

## 3. Original-media privacy and lifecycle

Store original photos and original layout documents outside the project root in an application-private store keyed by native organization UUID, Project UUID and opaque media UUID. Resolve identities from project registry/ACL, never display names or a path from uploaded metadata. Flat-project mode needs a generated opaque ownership identity before accepting original media; do not fall back to a globally shared directory.

In particular, do not store originals under uploads/, sites/, studies/, reports/ or history: current project routines recursively export/copy those directories. An opaque source record in scene state stores no absolute filename, external URL credential or EXIF. Derived assets have separate ownership, source terms and sanitized metadata; they are not automatic substitutes for an original-media download.

| Operation | Required source behavior |
|---|---|
| Same Project reload | Reauthorize opaque source handle; originals remain private and available only while retained. |
| Same Project snapshot/restore | Snapshot holds scene/provenance and optional opaque handle; restore rechecks current existence/ACL/retention. A missing source is unavailable, not recovered from another Project. |
| Scenario / Save-As | New Project identity; copy semantic scene and selected sanitized derived assets only. No original bytes or live cross-project source access. Preserve non-sensitive provenance and mark original unavailable unless separately reimported. |
| Default project bundle | No original bytes, EXIF, private path or working cross-project source handles. Only expressly exportable derived content and scene records. |
| Bundle import | Bind scene to destination Project; old media handles cannot resolve to the source Project. |
| Project/source deletion | Revoke download immediately; mark private originals for bounded cleanup after running workers release their references. |
| Retention expiry | Show Original unavailable with reason; retain useful derived scene where its policy permits. Never fabricate a replacement original. |

SC-00 N chooses and records an operator-visible retention period and measured per-project/media quotas before upload is enabled. Original download/delete uses current native authorization. Default exports test for the original fixture's exact bytes/hash, EXIF markers, original filename/private paths and resolvable foreign handles. Derived texture export requires source rights and sanitized metadata; selecting it explicitly is distinct from exporting original photos. No private originals are placed in Git or sent to an external worker without the separately configured authorized processing path.

## 4. Server-owned availability

Extend the existing project `/flow` projection additively with scene action verdicts: create_plot, generate_native, process_layout, reconstruct_photos, generate_blender, export_scene and apply_proposal. Each returns availability, a named blocker/fix where applicable, required inputs and a user-facing description of what will be computed. Electrical run_ready/verdicts retain their current meaning and value.

The UI displays this server verdict; it never derives enabled/blocked states from a browser/platform/source capability table. Model-free generate_native may be available with no electrical Bus/branch. Missing worker only blocks its action. Changing source, scope or scene revision refreshes the verdict. Submit and Apply both recheck the actual current conditions; a prior green /flow response is an observation, not admission authority.

Public input forms may use declared field constraints for inline syntax errors; they cannot certify source/worker/model availability. Adding new scene routes also requires explicit route/chat dispositions and standard policy/ACL tests. This plan creates no autonomous chat write tool.

## 5. Proposed external operations

These paths are concrete proposed names for review, not claims about the current OpenAPI. SC-00 N must compare them to current routing, pin DTO/error schemas and record any change before tests/code. No external engine is needed to freeze the Native subset.

| Method and proposed resource | Contract |
|---|---|
| GET /api/projects/{name}/flow | Additive scene action verdicts, no change to electrical run readiness. |
| GET /api/projects/{name}/sites | Compatibility projection; read never adopts/migrates. |
| PUT /api/projects/{name}/sites | Existing legacy contract with enabled-Site/revision protection; cannot replace canonical enabled state. |
| GET /api/projects/{name}/sites/{site_id}/scene | Canonical scene/revision, or explicit legacy/unadopted state. |
| POST .../scene/proposals | Validates action, expected revision and owned inputs; produces immediate native preview or a bounded job/proposal ID, no Apply. |
| GET .../scene/proposals/{id} | Authorized status, warnings, preview and base/hash metadata. |
| POST .../scene/proposals/{id}/apply | Revalidate current access/limits/revisions, exact proposal and allowed changes; atomic pointer commit and receipt. |
| POST .../scene/proposals/{id}/cancel | Idempotent cancellation; terminal applied result is not undone or relabelled cancelled. |
| POST .../scene/sources | Bounded upload into private original-media store; returns opaque source metadata, not a project-path URL. |
| GET/DELETE .../scene/sources/{id} | Current authorized metadata inspection or source deletion. |
| GET .../scene/sources/{id}/original | Explicit currently authorized original download; never a public bundle URL. |
| POST .../scene/exports | Owned immutable export snapshot proposal/job; selected format/media policy explicit. |
| GET .../scene/artifacts/{id} | Current authorized derived artifact/export download, owned manifest/path checked. |

The abbreviated paths share the full project/site/scene prefix above. Expected contract categories: not_found/forbidden as native policy requires, invalid_geometry/input, revision_conflict, scene_managed_site, source_unavailable, worker_unavailable, resource_limit, proposal_expired/cancelled and integrity_error. Map actual error kinds into the existing manifest and UI disposition contracts; do not introduce unclassified strings or waive existing guards. Revoked access between any two operations is rechecked.

## 6. Missing numerical data

The scene result DTO has a nullable value plus availability/reason on every unresolved numerical path. A representative unavailable quantity is `{value: null, available: false, reason: 'conversion_factor_missing', unit: 't/h'}`. A resolved zero is `{value: 0, available: true, reason: null, unit: 'MW'}`. Consumers preserve that distinction and show Unavailable. Actual unit/quantity keys are pinned in SC-03's native adapter contract, not inferred from the mesh.

Exact negative assertions cover missing process series, unknown native unit conversion and unsupported group quantities. A visual feature with no valid model binding has no numerical overlay. The current process summary endpoint does not authorize an hourly throughput number.

## 7. Independent milestones

SC-00 N: native model-free generation, flow projection, canonical authority, privacy, exact Native request/DTO/errors, numerical availability conventions and measured native limits. This milestone can be complete when both optional engines are unavailable.

SC-00 B: genuine Blender exchange and optional-worker readiness/resource evidence. SC-04 native artifact code can start after N; claiming Blender compatibility requires B. SC-05 cannot enable its worker before B.

SC-00 P: a supported genuine photo-reconstruction/registration experiment and measured limits. SC-06 layout/manual-tracing can start after N and its file policy; its photo action remains unavailable until P. Completion/failure of B and P cannot hold N's accepted status open. Each milestone records source SHA, fixture/engine versions, exact command/output and GO/NO-GO independently.
