<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# HH-04 — Immutable references over existing domain services

Status: needs-triage
Type: task
Baseline: `60d8332d64b2edef1b204fb5980311e6102229a8`
Baseline provenance: first drafted at `002a3fe212b0f11e07a726c72a44177ff919130f`; current plan rebased by source-delta inspection to `60d8332d64b2edef1b204fb5980311e6102229a8`. Native evidence/workbook/turn-check changes are recorded in baseline.md; implementation must recheck fresh source.
Depends on: HH-01 shared input identity; HH-05 characterization fixtures.
Owner: Harness/reference-resolution; project/evidence owners retain native authority.

## Outcome and exact scope

Introduce internal frozen value references, not an object-execution runtime or another project database. A reference identifies what the user meant and the native observation it came from; it grants no permission. The pilot must reduce duplicated identity checks or reference errors before more paths adopt it.

Three integration points only:

1. Saved-project `services.chat_tools.get_component` for `Generator` reads: create and validate `ComponentRef` around the existing native row read. Other classes and unsaved projects keep the characterized existing path, without claiming pilot coverage.
2. `services.reference_resolution._run` for an **explicit completed recorded** `run_id`: use `RunRef` internally after existing native evidence resolution. Current, latest, displayed, discussed, running, failed or unavailable cases retain their existing ladder/status behavior; no new fallback or checked certification.
3. `harness.edit_card._update_component_preview` for HH-01's supported scalar Generator updates: attach a server-only ComponentRef observation to the private preview record; use the ordinary card/worker admission and independent native read-back. A reference is not a replacement edit digest or approval.

No new tool, HTTP route, public tool argument, provider keyword, catalogue description, SDK dependency or user-facing mode. Existing success payloads and alias/monkeypatch identities stay byte/structure-compatible except a versioned private record extension. Do not add `ref` to `get_component`'s public schema in this slice.

## Current code to preserve

- `services.project_registry.resolve_project/bind_context` own saved project ACL and native binding.
- `services.reference_resolution.resolve_reference`, `_component`, `_run` own the explicit → displayed → discussed → unique → Choice ladder; UI values remain untrusted hints.
- `services.dirty_state.revision` is an edit counter, not physical asset identity or evidence correctness.
- `services.workflow_tools._network_input_fingerprint(ctx, exact=True)` is the existing exact network-input identity, excluding solver outputs.
- `services.run_evidence.get_run_evidence` and `checked_run_manifest` own record/section integrity. The latter's name does not confer a checked-answer badge.
- `harness.edit_card.preview/require_current/receipt` and its `approved` ContextVar own preview/card/read-back, with explicit edit cards in both Guided and Expert.
- `harness.assurance_scope.RequestScope` is a stricter independent publication contract; it is not replaced or widened.

## Data contracts (new, private v1)

Use standard frozen dataclasses, `extra='forbid'`/strict parsing only at the serialized private boundary, finite canonical JSON and bounded strings. Prefer composition over model-callable instance methods.

`ProjectRef`: `version=1`, `owner_id`, `project_id` (canonical saved UUID), `binding_generation` (server session/parent generation), `input_version` (HH-01 shared project/residency/edit-counter/exact-input digest). The owner is captured by software from the current actor, never trusted from a model-supplied object.

`ComponentRef`: `version=1`, `project: ProjectRef`, `component_class='Generator'`, `name` (exact native index label, 1–256 characters), `observed_row_digest` (canonical digest of the finite native row view used by the read/preview). The name denotes a **named model row**, not a manufacturer's physical equipment UUID. Creating a different physical object with the same name cannot be detected from a row label alone; the input observation becomes stale across any intervening revision/residency change, including undo to equal values. No fuzzy rebinding, silent rename substitution or cross-project import activation.

`RunRef`: `version=1`, software-captured `owner_id/project_id`, `run_id` (native validated format), `source='recorded'`, `status='completed'`, `manifest_digest` (SHA256 of canonical entire native completed manifest), `recorded_input_digest` with its original fingerprint version, and `observation_generation` for the active binding. Preserve the manifest's native source/fingerprint scheme; do not relabel an older fingerprint as HH-01's exact digest. No live InputVersion is required to read immutable historical evidence; later live edits do not invalidate intact historical evidence. ACL/binding/generation and manifest integrity are rechecked. Window/metric/unit/basis selectors are absent: this pilot carries identity only and cannot establish numerical compatibility between two runs.

Limits: serialized reference <= 8 KiB, nesting depth <= 4, schema version exactly 1, no paths, opaque Python objects, pickle or arbitrary method names. Unknown fields/versions/non-finite data refuse; no lossy coercion of bool to int or UUID aliases. Dataclass immutability is a coding aid, not a security guarantee.

## Proposed module API

New `harness/domain_refs.py` is the sole reference definition/factory/validation module. HH-01 owns `harness/admission_identity.py` and the residency identity; HH-04 imports it rather than calculating another hash.

Proposed internal functions (names are new, not claims about existing code):

- `component_ref(actor, session, ctx, klass, name) -> ComponentRef`: resolve actor access, require an eligible saved context, acquire the bounded native read/mutation lock, capture InputVersion and finite row together. No network/model call.
- `validate_component_ref(ref, actor, session, ctx)`: repeat ACL/binding/generation/version/current-row checks under the native lock. Return the current bounded native row or a typed refusal. Only the HH-01 worker can consume it for mutation.
- `recorded_run_ref(actor, session, ctx, run_id, manifest) -> RunRef`: native service supplied the completed manifest; validate ownership/source/format and canonical identity. Never recursively call `resolve_reference`.
- `validate_run_ref(ref, actor, session, ctx)`: native manifest re-read verifies exact digest and completed/integrity status. No fallback current, no automatic record recreation, no solve or side effect.

Factories return an explicit `not_eligible` result for the characterized legacy paths. A caller must distinguish it from invalid/stale/foreign eligible references: only not-eligible may use the pre-existing path. Invalid typed refs never fall back.

## Admission and lock semantics

1. Apply existing offered-tool, actor, project, restriction and checked-turn guards first. The pilot does not add a new checked tool or variant.
2. Resolve the native target using the current ladder. Ambiguity produces the existing Choice and no reference.
3. Capture refs under the same bounded lock as the native observation. Use explicit bound context; do not switch the active project to read another reference.
4. A component preview stores the reference privately beside the exact approved args/digest. No later model field can replace it.
5. HH-01 rechecks and consumes the private preview/ref at the native mutation boundary; require `requirements_revision/records_digest` if HH-02 is active. A successful facade precheck outside that lock is not atomic authority.
6. No reference lock is held while waiting for a user, model, SDK stream, filesystem remote fetch or queued job. Native run-evidence store reads retain their own established transaction order.

On restart/reload/eviction: old live ComponentRef/ProjectRef observations expire due to the HH-01 residency identity; re-resolve and re-preview. Historical RunRef can be rebuilt after fresh ACL/integrity checks. A serialized ref never auto-restores a card or native job authorization. Parent requirements persist separately from the short-lived observation.

## Refusals and user experience

Add only needed new kinds via `tool-error-kinds.d/claude-hh04-domain-refs.json`: `domain_reference_stale`, `domain_reference_invalid`, `domain_reference_unavailable`; reuse native ownership/ambiguity/read refusals where possible. Native failure detail must not leak a foreign project's existence, paths, owner IDs or internal record text. Status 409 for stale/missing eligible observation, 422 for malformed private data; existing ACL codes stay as established.

Plain examples: “The project changed since that preview. Review the updated values before applying them.” / “That saved result is unavailable. Select another recorded run.” No ticket IDs, class paths or hash strings in ordinary UI copy. Existing card/Choice/Activity surfaces render this; no new buttons. Explicit edit previews remain required in both modes. Other write tools follow their current mode policy.

## Compatibility, rollout and rollback

Proposed server flag `PYPSA_GUI_HARNESS_REFERENCE_PILOT=off|saved_network` defaults off until the deterministic pilot gates pass and normal review approves activation. Neither value weakens current guards. `saved_network` enables only the three paths above; configured-but-unsupported classes stay on their characterized legacy path with no typed-reference guarantee. A caller that has already received an invalid eligible ref cannot bypass refusal by toggling/retrying a legacy path.

Private edit records gain `domain_ref_version=1` and a bounded ref field. Legacy records are not assigned a fabricated observation; existing require-current behavior remains, and new unattended HH-01 admission requires a freshly created supported record. Rollback disables creation of new refs, leaves existing native card/current checks in place, and expires pending pilot observations instead of converting them into authority. No persisted evidence, requirements, uncertain effects or cost records are deleted.

## Allowed files and ownership

- New `pypsa-gui/backend/harness/domain_refs.py`.
- HH-04 integration hunks only in `services/chat_tools.py:get_component`, `services/reference_resolution.py:_run`, `harness/edit_card.py:_update_component_preview` plus private record serialization in `harness/edit_card_records.py` if required by the inspected format.
- New `tests/test_harness_domain_refs.py`; additive tests in `test_reference_ladder.py`, `test_edit_card.py`, `test_workflow_provider_parity.py`; existing semantic/layout/checked tests remain unchanged unless an exact amendment is reviewed.
- New error-kind fragment, one release fragment, bounded README coverage note.
- No catalogue/tool schema, generated inventory/matrix, assurance module, solver, public route or frontend availability changes. HH-01 owns its identity/context changes; coordinate rather than duplicate.

Before widening this list, stop with the exact missing seam and proposed amendment. Imported aliases and handler identities must retain the existing parity tests.

## Test-first acceptance matrix

Each numbered case becomes a named test or explicit parametrized case. Preserve expected outputs for positive controls; all refusals require a handler/native-effect spy and independent state read-back where applicable.

| ID | Fixture / perturbation | Required result |
|---|---|---|
| R01 | Factory on saved Generator; same actor/context | Native values identical to legacy read; finite immutable ref. |
| R02 | Serialize/decode valid reference | Exact identity preserved; no bound method/network object/path. |
| R03 | Mutate copied/ref JSON owner/project | ACL failure before native read; no foreign values leaked. |
| R04 | User loses access after factory | Recheck refuses despite unchanged digest. |
| R05 | Edit then undo to identical values | Old live ref stale due to revision, even if exact digest matches. |
| R06 | Reload/restart same inputs/counter | Old residency rejected; newly resolved ref works. |
| R07 | Rename/delete named row; duplicate labels across classes/projects | No fuzzy fallback; existing Choice/not-found semantics retained. |
| R08 | Preview then change selected UI element | Exact private args/target retained or stale refusal; never edit new selection. |
| R09 | Native mutation between preview and worker | HH-01 barrier refuses with no dirty/undo/effect side effects. |
| R10 | Completed retained result then live project edit | Intact exact historical run remains readable, clearly historical. |
| R11 | Manifest or section tamper; retention deletion | Ref unavailable; no live/latest fallback or fabricated completion. |
| R12 | Current/discussed/latest/running/failed run resolution | Characterized legacy ladder/status unchanged; no false completed RunRef. |
| R13 | Unsupported schema/version/NaN/path/method payload | Typed refusal; cannot invoke arbitrary code or file IO. |
| R14 | Same task on each supported provider transport | Each provider's sent-offer allowlist respected; same identity/authority invariants. |
| R15 | Guided and Expert scalar edit | Both retain current explicit edit-card rule; approve/deny outcomes match each declared policy. |
| R16 | Strict checked turn unsupported variant | Existing guard refuses before facade/handler; no assurance expansion. |
| R17 | Two saved projects; cross-turn rebind/generation bump | Old live ref invalid; no parent/ref attachment inference. |
| R18 | Flag off, unsaved network, unsupported component class | Characterized legacy behavior; no pilot coverage claim. |
| R19 | Factory/ref checks fail after card but before effect | No receipt claims success; ordinary no-replay rules preserved. |
| R20 | Pilot vs legacy representative read/preview | No changed tool count/ranking/public payload; record duplicated-check and latency/context observations without an invented benefit threshold. |

## Build and completion sequence

1. Characterize existing three paths and write R01/R10/R12/R18 compatibility tests on fresh master. Existing behavior must pass before implementation.
2. Write factory/identity tests R02–R07/R11/R13 red; add only new module/types using HH-01 identity. Show green.
3. Integrate Generator read then explicit recorded-run resolution, retaining payload/alias contracts; test R03/R04/R10–R14.
4. Integrate private preview observation through HH-01 worker; test R08/R09/R15–R19 with lock barriers, not sleeps.
5. Run slice suite, reference/edit/provider regressions, required semantic/checked/layout/tool-kind checks and HH-05 conformance pack. Record exact commands/counts/head; no paid calls.
6. Report R20 findings. If the facade adds only indirection without consolidating a real invariant or reducing mistakes, keep the characterization/tests and defer facade rollout rather than force adoption.

Done means all mapped cases executed with expected positive/refusal outcomes, required checks green on exact head, explicit pilot coverage/rollback documented, no open review blocker, and the normal official reviewers accept the bounded implementation. It does not mean every tool or physical asset has an immutable identity.
