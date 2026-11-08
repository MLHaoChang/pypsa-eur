<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-07 — integrated AI guidance and project-flow continuity

Status: ready-for-agent
Model: Opus
Type: task
Dependencies: accepted SC-00 N contracts; develop native adapters with SC-01–SC-03, optional adapters only when their respective engine gates pass
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Implement [ai-guided-project-flow.md](../ai-guided-project-flow.md) through the existing companion and project flow. This is part of the native release scope, not a separate assistant/mode or a dependency on Blender/photo availability. Freeze context/tool/request/disposition contracts in SC-00 N; write failing owned-context, dispatch/approval and visible guided-journey tests before adapters or UI code.

Reuse get_project_flow, native model schemas/edit cards, workflow flow_section mapping, event log, existing start_task/study-brief execution, navigation acknowledgements and owned job/receipt services. Implement the bounded scene-context and typed prepare/apply adapters only after applicable harness/UX plan review; every route has an explicit chat disposition. Extend the existing assistant workflow content rather than building a parallel executor. Do not copy any stale workflow text that suggests Expert writes bypass current edit-card policy.

Provide Explain this, help with the current blocker, prepare a change and resume assistance at the existing section/selection. Server flow verdicts own availability/completion. Map the whole native journey into project/carriers/connection/loads/assets/profiles/commercial/scenarios/readiness/results without altering their order or adding duplicate input editors. No inferred energy demand/cost/efficiency from geometry. Preserve selected study scope and planning periods.

Show exact native receipts, distinguish partial model-create/scene-placement outcomes, reconcile unknown effects before retry, and resume by reading owned current state. An old approval, chat summary, finished-worker observation or emitted navigation frame is never evidence of current authority/success. Keep manual completion usable with no provider. Freeze a finite context budget in the prototype; no original-media transfer or unbounded model/mesh injection.

Done for native release: V61–V72 pass at the integrated seams with real native data/services and scripted-provider control; other applicable domain/device/CI gates pass; a separate authorized real-provider evaluation supports conversational-quality claims. Report absent optional-engine adapters as unavailable, never implemented. Independent GK/UX review and all ordinary repository gates still apply.

## Scene routes held for this issue (route parity)

Live-voice issue 08 (R2, owner option 1) declares these GUI routes `held-by-spec` with this file as
their `issue` (`pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/`). They get their chat
tools here, through the frozen SC-07 tool names in `../ai-guided-project-flow.md`; until then each
is a limitation and this issue is one `route-parity:held:<this file>` full-coverage blocker.
`assistant_route_parity.py --check` fails once this issue's Status is done or wontfix while a route
is still held, or when a held route is no longer named below.

- GET /api/projects/{name}/sites/{site_id}/scene
- GET /api/projects/{name}/sites/{site_id}/scene/results (SC-03 overlay; its chat read is the SC-07 results unit)
- POST /api/projects/{name}/sites/{site_id}/scene/adopt
- POST /api/projects/{name}/sites/{site_id}/scene/proposals
- POST /api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/apply
- POST /api/projects/{name}/sites/{site_id}/scene/proposals/{proposal_id}/cancel

The site anchor (`GET/PUT /api/projects/{name}/site-anchor`) is not held: `get_site_anchor` and the
carded `set_site_anchor` serve it.

## Likely touch points and verification

Rebaseline before code: harness compose/workflows/catalogue/dispatch, typed chat handlers and owned scene/job services; server flow projection; frontend Site selection, existing companion/context and flow-navigation return anchors. This is a touch-point map, not authorization to overwrite other briefs. Keep proposals/applies in the common native scene path rather than implementing state changes inside prompt text.

Run relevant flow/workflow/navigation, edit-card/access/restrictions, provider offer-cap, tool-route/schema/error, event-log, replay/recovery and scene integration tests. Regenerate capability inventory/control artifacts only through their scripts. Capture Guided/Expert same-task screenshots and a native geometry-first full journey, including a failed/denied action and manual fallback. Record exact commands, head, evidence and exclusions in the PR and handoff; proposed tests are not completed evidence.

## Optional coordinate-first workflow clarification

Follow optional-site-workflow.md through optional coordinate-gated creation, campus review, buildings-only pause, native editor return/target mapping, logical/physical energy layers and actual-cadence results. Include V73–V90 with relevant stage-aware refusals and resumption; no extra wizard, diagram-only substitute, automatic solve or missing-data invention. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).

## Interactive configuration acceptance

Implement the example intent-to-effect contract in ai-guided-project-flow.md and V91–V98: guided creation, building/placement adjustments, exact multi-load/facility associations, PV/battery placement, supported native process/connection and parameter edits, and evidence-grounded channel mapping. Separate scene, model and display effects, ask about ambiguous output/connection wording, reuse normal native edit cards and exact scene proposals, and show truthful partial receipts. Unsupported tools/fields retain named refusal/editor fallback. This is a required guided adjustment path, not a read-only explainer.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage — owner-required specification; not implemented`. The owner required this specification, so it needs no further owner call. Build the native path only; optional-engine adapters report unavailable. Files: the assistant tools and flow in `pypsa-gui/backend/harness/` and `services/chat_tools.py`, the Site flow in `pypsa-gui/frontend/src/`, tests V61-V98. Blockers: 02 and 03 (SC-01, SC-02; in progress; SC-00 N contracts are accepted). Reviewers: GK B and UX. Large: the implementer proposes slices in its plan.

### 2026-11-07: plan GO (GK B, UX) and the slices

Session `session_01G7qCun9vXpfVgFsSgwQegs`. Seven PRs (PR-SIZE-1): S1 tools (GK B), S2 companion entry points (UX), S3 task step and recovery (GK B), S4 bindings through chat (GK B), S5 binding receipts (UX), S6 results read and integrated journey (GK B), S7 journey evidence and Status done (UX).

GK B answers: D11's names `site_scene_read`, `site_scene_prepare`, `site_scene_apply` (with `site_scene_cancel`); the spec's names are aliases in the docs only. Prepare has no card. Adopt stays `not_via_chat`. Cancel has no card.

GK B conditions:
- **G1.** Apply requires `proposal_id`, `input_hash` and `expected_revision`, refused at the tool. The card binds them plus `site_id` and the owner.
- **G2.** Chat prepares never evict others' previews: per-owner cap, and refuse at the global cap.
- **G3.** Owner, project and site come from the server; a foreign or deleted `site_id` reads `not_found`.
- **G4.** Cancel reaches only the caller's own preview, never relabels an applied one, and is idempotent.
- **G5.** Read is capped in bytes and items, user strings are capped and fenced, and the `[site 3d]` block comes from a server re-read only.
- **G6.** A chat apply lands the same receipt, revision and bindings as the route, with the D06 lock order.
- **G7.** Apply is in no run envelope in S1; a task step joins `BOUND_CONTEXT_TOOLS` in S3.
- **G8.** An uncertain apply reads the chain receipt before any retry and never retries silently (S3).
- **G9.** Model edit and scene mapping are two cards; resume re-checks the model revision; ambiguity returns clarify (S4).
- **G10.** Results reads are project-scoped, every channel carries unit, run and freshness, and stale results are labelled (S6).
- **G11.** Kinds and route parity go in fragments; the listed checks run before each review.
- **G12.** Hand-mutated guards are listed with the test that kills each.

UX conditions:
- **U1.** S2, S5 and S7 are each their own PR under PR-SIZE-1.
- **U2.** Help jumps go through `withReturn` and the mode-aware opener; nothing jumps on its own.
- **U3.** "Help with the current blocker" shows only when `/flow` reports one, named in the section's words.
- **U4.** Receipt wording: no em dash or semicolon, and no ids or field keys in visible text.
- **U5.** Prepare uses the existing ConfirmationCard unchanged; a malformed preview disables Approve with a reason.
- **U6.** Resume says what it found. Success shows only on the browser ACK, with a pending word before it.
- **U7.** Shots of each state in Guided and Expert, with real API and route mocks.

### 2026-11-07: S3 plan GO with changes N1–N8 (GK B)

GK B (32), `session_01G7qCujH35CpYUUDWwdZg7v`, to SC-07 (2) and resent to SC-07 (3). Plan: an apply step in `start_task` takes ref-only args from one prepare step; `site_scene_apply` alone joins `BOUND_CONTEXT_TOOLS`; a `_native_evidence` branch keyed on `proposals._committed` and an input_hash match; `resolve_task` retry refused after `proposal_expired`, `proposal_cancelled` or `revision_conflict`; the plain-chat unknown outcome is the new kind `scene_apply_outcome_unknown`.

- **N1, the card is never skipped.** The task layer gives no approval. Every apply step, plain or under a brief, reaches `proposals.apply` only through `site_scene_apply`'s own edit card for that exact proposal_id and input_hash. An approved brief, a Continue at a stage boundary and a `resolve_task` retry each count as no card. Tests: a plain prepare→apply task with the card unapproved never calls `proposals.apply` (spy); the same under a brief after Continue.
- **N2, one source for the refs.** site_id, proposal_id, input_hash and expected_revision are all whole-value refs to the same earlier `site_scene_prepare` step. A literal, or refs split across two prepare steps, is refused at `start_task` (kind named in the test).
- **N3, retry is not re-prepare.** When the apply step failed with `proposal_expired`, `proposal_cancelled` or `revision_conflict`, `resolve_task("retry")` is refused with a message to `cancel_task` and start a new plan with a fresh prepare. Each of the three tested.
- **N4, the reconcile key.** The `site_scene_apply` evidence branch resolves its args from the task's own steps, calls `proposals._committed`, and counts the receipt only when its input_hash equals the resolved one. It comes before the generic `edit_card.reread` branch, which must not also yield found for this tool. An error inside `_committed` gives `(False, None)`. Tests: matching hash completes with the receipt; other hash unverifiable; `_committed` raising unverifiable.
- **N5, a partial model write.** Inject a fault between `_record_bindings` and `store.apply`; show the retry leaves exactly one set of recorded bindings, or refuses. If neither holds, it is an S1 defect for GK B, not an S3 workaround.
- **N6, the plain-chat unknown outcome.** Catch only non-SceneError exceptions (a SceneError keeps its typed refusal). After the receipt read, a proposal with no receipt shows no card that can be approved again: the entry is marked unusable in `chat.apply`'s path, or a second apply on that pid is refused with `scene_apply_outcome_unknown`. The route's behaviour is unchanged. A failing receipt read returns the same kind. Tested: `proposals.apply` called once, `read_back` present.
- **N7, BOUND_CONTEXT_TOOLS.** `site_scene_apply` only, with its sweep case in `test_harness_run_skeleton`. No stage allowlist, `L1_TOOLS` or `RUNNER_ONLY` set changes (cited in the PR).
- **N8, mutants (G12).** By hand, each with its killing test: drop the literal-arg refusal; drop the input_hash match in the reconcile; drop the N3 retry refusal; let the unknown outcome answer applied with no receipt; drop the card requirement for a task-dispatched apply.

Kinds go in the fragment, with a catalogue row only if the guard asks for one (G11). Review goes to GK B (33).

### 2026-11-07: S4 plan GO with changes H1–H8 (GK B)

GK B (33), `session_01G7qCukLe9Nbb6fsN1EwMTV`, on SC-07 (3)'s S4 plan: chat prepare admits bind_targets, place_existing, rebind, detach and undo_detach; ambiguous, unknown or unsupported targets return clarify; the scene card maps targets only and a model edit keeps its own native card; the card re-checks each target's fingerprint; W2 cancels an unknown-outcome preview.

- **H1, the choice is the user's, on the card.** Catalogue text is not enforcement. After a clarify, the scene card row names the exact target (component name and class, bus, carrier, unit, not only its label) and says it was picked from N candidates. A target outside the candidates is allowed and shown the same way. Tested.
- **H2, clarify bounds.** Over the cap the result says "and N more" and lists no partial pick. Zero candidates is unknown_target, never clarify. Labels pass the G5 fence; tested with an instruction-shaped load name.
- **H3, clarify in a plan.** A task step whose prepare returns clarify is not completed: it waits for the user, and the apply step's refs never resolve against a clarify result. Test: a plan with a clarify prepare stops before apply, and the apply is never dispatched.
- **H4, no model value changes.** A scene apply of each of the five model actions leaves `_network_input_fingerprint` and the network revision unchanged, one test per action. If D06 bindings feed any LP input, stop and tell GK B before building.
- **H5.** The card-time fingerprint check adds to apply's check and never replaces it. Mutate each alone and both together; each alone-mutant has its own killing test (target moved after the card, before apply).
- **H6, W2 never hides a landed receipt.** Cancel only after a successful receipt read found none; a failed read marks unknown but does not cancel. Status and reconcile read the chain first, so a landed commit always reports applied. Tests: landed, first read fails, a later read finds it → applied; no receipt → cancelled, and evicting the marker does not re-enable a card.
- **H7, two cards.** A denied scene card leaves the native edit and no binding; a denied native card means the prepare never runs (the N2 refs fail closed). Spy on `proposals.create`.
- **H8, mutants.** The plan's three, plus: clarify returning the first candidate; H6's cancel on a failed read; H4's guard if one is added. Then `mutants.py` on chat.py's changed lines, survivors named in the PR body.

One PR, GK B's area. Send the head to GK B.

### 2026-11-07: S5 plan GO (UX (13); GK B (34) with changes J1–J6)

SC-07 (5), `session_01G7qCungUYyBRNqihmHTZqW`. Plan: R1 the receipt reads back each binding, R2 words not scene ids, R3 the partial outcome on the task card, R4 the unknown outcome, R5 Resume in Site 3D, R6 pending then Opened on the browser ACK.

UX (13), `session_01G7qCuib7jBi7bz9ToMaouW`: Resume is one line in the S2 companion row, shown only with something to resume. Split (U1): **S5a** holds R1, R2 and R4 (led by GK B, UX checks the words); **S5b** holds R3, R5 and R6 (UX). Conditions:
1. buildingLabel built once in the backend with a parity test, KIND_WORDS, curly quotes for user names; no scene id or class key in any receipt line or Resume "Found:" line.
2. R3: "Done: <model edit words>. Not done: the 3D site mapping (<reason>)." with the reason in plain words; "Prepare the mapping again" is a button with no "›".
3. R4: no Approve again; "Check the 3D site" shows "Checking…" until the answer arrives, never a success word before it.
4. R5: the result in `role="status"`; "Review it in chat ›" is a jump, "Prepare it again" a button; "revision N" only if Site 3D shows the revision; the line clears after a fresh read once its condition ends.
5. R6: pending, then "Opened" only on the ACK, else "Could not confirm the 3D site opened"; withReturn and the mode-aware opener; Back to Site 3D restores the selection.
6. Receipt wording: colon or full stop only, "›" only on real links; "Not found on the site" uses the existing uncertain wording.
7. Shots per PR in Guided and Expert with the real API and route mocks. S5a: a receipt with all four row kinds, an uncertain receipt, the unknown-outcome card. S5b: the partial task card, each Resume "Found" state, Resume absent, the pending and "Opened" states, the Back pill.

GK B (34), `session_01G7qCumPpRCKKA8Rskye7ef`, B3 = option (a), an owner-scoped `my_previews` on the scene GET and on `site_scene_read`:

- J1 (B1, read-back). A receipt row reads back what the preview planned, not only that something is present. For each target:
  - the binding or placement on disk after commit must equal the planned one (class, name, bus, carrier, the feature; for a placement x/y/heading/on);
  - for detach / undo_detach, the item must be off the site (or back on it) with its attachments as planned.
  A missing or different target makes the outcome uncertain, never verified. Read from disk under PyPSAService.get_lock, never from apply's return value. Each row names the fields it did not compare (#1170 revision). A "done" count counts only rows this apply changed: a `keep` step is not done (#1175 E2).
- J2 (B1, tests). Per action: found → verified; target gone after commit → uncertain; target present but different (another load bound, a moved placement) → uncertain. Hand mutants beyond yours: compare presence only; count keep rows as done.
- J3 (B2, labels). A label is user-authored: G5 fence and cap, and it never reaches a model value. Two features with the same visible label must stay distinguishable on the card and the receipt (add the id or an index when labels collide), with a test. Stored keys and fingerprints are unchanged, as you say.
- J4 (B3, my_previews). Owner-scoped:
  - only the caller's previews, and never another owner's, even with the same id;
  - capped at MAX_LIVE_PER_OWNER;
  - status reads the chain first (H6), so a landed commit is applied even when the preview was cancelled or its marker evicted;
  - `cancelled` with no receipt is not listed, and an expired one neither.
  Tests: two owners; unknown marker → outcome_unknown, then applied once the receipt reads back; a cancelled preview whose commit landed → applied.
- J5 (B3, read surfaces). The scene GET has no server-side cache today (I checked: no ETag or lru_cache on the scene routes), so keep it that way. The client's query key for the scene must not be shared across sign-ins, so a my_previews list can never show to another user. site_scene_read gains the same list. Run inventory, route parity, control matrix and the error-kind manifest at a trial merge, because a read's shape changed.
- J6 (B4). No backend change, agreed. The UI's status read goes through _proposal_status only.

J3 and U4 meet as: two features with the same label get a number in scene order ("Proposed building 1", "Proposed building 2"), never the id. Review of S5a goes to GK B (35), `session_01G7qCunD24gwxQbJ8Fd9cVN`.

### 2026-11-08: S5b plan GO (UX (14) with U-a–U-g; GK B (36) with changes V1–V5)

SC-07 (7), `session_01G7qCumNJ98m4khm55HSi2L`. Plan: R3 the partial outcome line on the task card (frontend only, from the task view's `changes`, steps and next_step error); R5 the Resume line in the S2 companion row, read from the scene GET's `my_previews`; R6 a browser ACK for a chat navigate into Site 3D (`harness/navigation.py`), with pending, then "Opened" only on the ACK. Folded in: the S5a mutant survivor at `chat.py` roof words, and GK W1 (an early stop in `my_previews`).

UX (14), `session_01G7qCujt312zP7jy3ZddHnV`:
- U-a. "›" (U+203A) marks only "Review it in chat ›". "Prepare the mapping again", "Prepare it again" and "Check the 3D site" are buttons with no mark.
- U-b. "Found: your <action words> was applied." shows only as the answer to a "Check the 3D site" that finds the change landed, and clears on the next fresh scene read. An applied row with nothing to act on never shows on page load (#1183).
- U-c. "Prepare it again" shows only after a check finds the change did not land. An open row has one action, "Review it in chat ›".
- U-d. Several rows: one line. The one to act on first (unknown before open, then newest), the rest as "(N more)" before the full stop. Review it in chat opens the dock on that row.
- U-e. One ellipsis character in "Opening the 3D site…" and "Checking…". R3 reasons stay in brackets, with no ids or kind keys; a denied card and a cancelled one stay distinct.
- U-f. scene_apply_outcome_unknown never reads "Not done", on the card or in Resume; it keeps S5a's "Check the 3D site" path.
- U-g. Shots as listed in condition 7, plus "(N more)" and the check-resolved applied and not-landed states, in both modes. S2's U1–U7 still apply.

GK B (36), `session_01G7qCuinrvGikhfDa18qDRb` (GO stands with GK B (37), `session_01G7qCuiY6AnM2CjmQw3AqmV`); R6 stays in this PR:
- V1, exact admission. `canvas_view` only with `panel_id: "map"` and the value "site"; another value, another panel or any extra key returns None. The existing key set and the results/loadflow branches stay byte-identical. Tests: canvas_view on another panel, canvas_view "foo", an extra key, each None; a pin that an existing results or loadflow payload issues the same record shape.
- V2, what "Opened" attests. A navigate that names a Site carries site_id in the target and acknowledge() matches it, so "Opened" means that Site. With no Site named the words claim only that Site 3D opened. input_sha256 is the network fingerprint: the ACK attests no scene revision and no displayed content (said in the README's navigation section). Tests: another site_id refused; another panel refused.
- V3, no network. issue() returns None when no network is loaded, and the UI then says "Could not confirm the 3D site opened". Backend test for the None.
- V4, W1. An output-identical early stop. A fixture compares the old and new code on the same entries (8 open plus applied, mixed with markers) and on fewer than 8 non-applied rows (every row read). Hand mutant: a break at the 7th row is killed.
- V5, R3 and N3. No change to assistant_tasks.py. "Prepare the mapping again" sends cancel_task and a fresh prepare, never resolve_task retry; the old proposal_id refuses to apply after the cancel (tested).
- Route parity, control matrix and inventory at the trial merge; the error-kind manifest test if any kind changes.

### 2026-11-08: S5b amendments 1a and 2a (GK B (37))

GK B (37), `session_01G7qCuiY6AnM2CjmQw3AqmV`, relaxes V5's "assistant_tasks.py unchanged" for exactly these two changes:

- **1a, next_step.error_kind.** Emitted only when the step stores one, read-only, never derived from the message text. It is the kind finish_step stores (N3) from the tool's own typed refusal (`HTTPException.detail["error_kind"]`, `harness/loop.py` dispatch except branch), so a manifest kind, never model or user text. Tests: proposal_expired shows in get_task; a step with no kind has no key; the frontend's fallback for an absent kind has its own test.
- **2a, cancel_task cancels its own open previews.** Only the proposal_ids in this task's own completed site_scene_prepare step results, never another task's or one the user prepared outside it. Through the owner-scoped G4 cancel only (idempotent, an applied proposal never relabelled). A failing preview cancel never blocks the task cancel and is reported in the cancel's result (`previews_not_cancelled`). Apply claims the proposal (`ready` → `applying`) and cancel writes its status under the same `proposals._lock`. Tests: prepare, denied card, cancel_task, apply on the old pid refuses proposal_cancelled with the apply spy never called; a second task's preview survives; an applied one stays applied; a raising G4 cancel still cancels the task and reports it. Mutants: drop the loop, drop the own-steps filter, drop the completed-only filter, swallow the failure.

Found while building: a denied card never reaches the task step (the confirmation gate refuses before `begin_step`), so the apply stays pending and R3 claims no "Not done" for it; and a refused apply is stored `uncertain`, so R3 reads "Not done" only for a typed refusal that proves nothing landed (expired, cancelled, revision conflict, unknown target).
