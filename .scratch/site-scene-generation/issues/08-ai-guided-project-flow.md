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
