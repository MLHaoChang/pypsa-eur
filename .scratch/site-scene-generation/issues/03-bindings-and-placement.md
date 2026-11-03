<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02 — multi-energy buildings, process complexes and equipment

Status: in-progress — SC-02a backend merged (#844, c86f572f); SC-02b UI merged (#859, 2d8779ca); SC-02d detach and group editing merged (#1029, [../sc-02d/record.md](../sc-02d/record.md)); remaining follow-ups in [../sc-02/record.md](../sc-02/record.md)
Type: task
Dependencies: SC-01; SC-00 target-lifecycle contract
Baseline: master 9988f9899a3bf8e80f51272529f18ea70599b90c

## Implementation assignment

Follow [execution-guide.md](../execution-guide.md) and the matching units in [execution-manifest.yaml](../execution-manifest.yaml). Freeze this assignment's actual-master file allowlist and public contracts; satisfy [decision gates](../decision-register.md) before dependent code, and record test-first evidence/unfinished work using [implementation-record-template.md](../implementation-record-template.md). Prototype N/B/P units close their own decisions through evidence; they do not require accepted experimental outcomes before starting. Primary V ownership does not remove cross-cutting obligations or authorize changes outside the frozen slice.

## Scope and acceptance

Before code, obtain the required engine/UX plan review for native lifecycle/mutation seams. First prove rename/delete/same-name recreation behavior; do not use loose display-name matching or infer a stable UUID the model does not provide.

Write binding and native-model seam tests before implementation. Associate existing Loads per carrier, Process-unit Links (eh_process) and Inventory Stores with a building/group. Support acyclic complexes covering several buildings and outside equipment. Associate multiple visible enclosures with one logical component without multiplying capacity. Do not create a duplicate fixed electricity Load for a process Link's demand. Display unsupported conversions/allocations as unavailable.

Integrate real building footprints, roof eligibility, functional terminals and service envelopes into placement. Preserve exact physical scale for source-verified catalogue equipment; keep generic fitted equipment visibly approximate. Place existing is scene-only; Add model asset uses the existing native editor/approval path and separate receipt. If placement fails after creation, do not retry creating the asset.

Keep optional geometry-derived length behavior behind the current setting and existing native routes, preserving user routes. Test access revocation and lifecycle failure before Apply. No new energy/process physics or globally overwritten equipment library.

Done: V11–21, domain fixture bindings, lifecycle correctness, native mutation regression and Guided/Expert review evidence.

## Validation

V11–21, V37–38, V45–47, V50–51; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.

## Cross-cutting AI guidance

Deliver this slice's in-place explanation, typed proposed action or truthful UI fallback, native read-back and resume context under [ai-guided-project-flow.md](../ai-guided-project-flow.md), alongside SC-07. Server flow state and current approvals remain authoritative. Include the relevant V61–V72 assertions; optional engine absence cannot block the native/manual flow.

## Optional coordinate-first workflow clarification

Map existing native2D/SLD targets only, with per-target prerequisites and in-place parameterization/return. Resolve PoC from native Bus.eh_poc and existing role/config import/export Links (no new PoC class); preserve multiple/ambiguous PoCs and separate Transformer terminal references and typed multi-carrier connection projections; distinguish known logical edge, known physical route and available flow. Unknown routes stay explicitly schematic, buried layers hidden until requested, and no geometry supplies missing electrical/pipe parameters. Cover V80–V86 and applicable process fixtures. Read [optional-site-workflow.md](../optional-site-workflow.md) and [workflow-assessment.md](../workflow-assessment.md).

## Comments

### SC-02d plan verdicts (2026-11-02)

Plan: detach (`detach`, `undo_detach` proposal actions) and group editing (several items in one `place_existing`), by session_01G7qCujZwKU3hhLgFqHRzVf. Record: [../sc-02d/record.md](../sc-02d/record.md).

**GK B (16) GO**, keeping the sites.json binding on detach. Conditions:

1. `undo_detach` is a delta on the current head, never a revert to the detach's parent generation: edits made after the detach to other keys, groups and associations survive the undo.
2. At Apply, undo re-derives what it restores from the chain and refuses (no write) if that differs from the preview. Undoing twice gives `already_placed`; re-applying the same detach id returns its committed receipt.
3. The proposal input is checked against the id format and looked up only in this site's own chain. A detach id from another site or project, a missing one and a proposal that is not a detach all give the same `not_found` text.
4. A new route `error_kind` goes in a `pypsa-gui/tool-error-kinds.d/<branch>.json` fragment.
5. A binding with no placement and an empty group become reachable: every reader handles them (no 500, no "unbound" finding for a detached-but-bound item).
6. Detach reuses `place_existing`'s key cap and validation; existing actions' previews and receipts are unchanged.
7. Mutants, each killed by a named test: drop the `already_placed` check; undo restores the parent generation wholesale; detach drops the binding.

**UX (6) GO** with conditions: display names everywhere (never `Class:name`) and each refusal as a plain sentence naming the item; move fields reuse the single-item axis words and units; "Edit N items" heading, with one item back to the absolute fields and no values carried between modes; Esc clears the selection only when the canvas or list has focus; the detach preview lists what goes by name and says when a group is left empty (kept); the receipt carries an Undo button, the undo card is titled "Undo detach: restore N items", and Undo stays visible but disabled with its reason when the detach can no longer be undone; one selection outline, distinct from hover and gizmo, in light and dark; screenshots in Guided and Expert, light and dark.
