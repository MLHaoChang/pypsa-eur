<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-17: Prove bounded manual and AI library use across the existing workflow

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: Shared
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

A collection is not useful until users can understand applicability, preview it and apply it safely in Guided/Expert/chat without duplicate pages or false verification.

## Research assignment

For each admitted collection, which existing editor/step/companion tools support find, review, explicit use, pin and explain today, and what minimal consumer gap remains?

Candidate starting points, not import approval:

- Native ReferenceApplyDrawer, CommercialLibraryPage, profile upload and harness contracts; actual admitted records from each collection ticket.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/frontend/src/pages/results/investment/ReferenceApplyDrawer.tsx`
- `pypsa-gui/frontend/src/pages/economics/CommercialLibraryPage.tsx`
- `pypsa-gui/frontend/src/layout/properties/AssetFields.tsx`
- `pypsa-gui/backend/harness/README.md`
- `pypsa-gui/backend/services/library/reference_apply.py`
- `pypsa-gui/backend/tests/test_chat_tools_library.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Create one coverage row per admitted collection: editor/workflow home, native read/apply/approval tool, version/source labels, completion warning and unavailable-field behavior. Implement incrementally after that collection is admitted; do not wait for all collections or build a new global dashboard.
2. Reuse current preview/apply paths and approvals; show source/money/technical applicability plus old/new fields. Explicitly preserve local edits, stale-project refusal, org isolation, denial/cancellation, and solve-safe reads. Only add native consumer/tool changes where the audit proves a gap.
3. Use offline scripted providers and actual HTTP/API/UI interactions to check explanations and evidence. New tool/route changes must pass bounded-tool discovery/ranking/journey, schema/argshape/handler, inventory and matrix checks; no model call is required.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-17-01 | Preview a reference while a solve is retained. | No input mutation or solve invalidation; source/applicability labels are visible. |
| LG-17-02 | Approve/deny/cancel a compatible edit in Guided, Expert and scripted chat. | Only approval mutates once; native read-back, receipt and version pin agree across modes. |
| LG-17-03 | Switch owner/project/component or edit locally after preview. | Stale/cross-scope apply refuses; no local edit is silently overwritten. |
| LG-17-04 | Ask for a numeric conclusion with partial tariff/unknown cost/no compatible source. | Assistant states exact missing evidence and does not invent a value or full total. |
| LG-17-05 | A new library tool description changes provider tool selection. | Discovery and results-walk regressions stay green; inventory is generated, never hand-edited. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_reference_apply.py`
- `test_chat_tools_library.py`
- `test_vs2_results_walk_journey.py`
- `test_chat_tools_handler_dependencies.py`
- `test_chat_tools_argshape.py`
- `test_write_surface_lock_policy.py`
- `Frontend ReferenceApplyDrawer/LibraryReferences/CommercialLibraryPage coverage`

## Stop conditions and handoff

UX and harness/schema review apply to their changed paths. An offline scripted-provider journey is not a live paid-provider, full browser/device or engineering calibration claim.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
