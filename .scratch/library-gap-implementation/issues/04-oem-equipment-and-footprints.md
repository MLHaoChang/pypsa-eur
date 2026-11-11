<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-04: Add a small model-identified equipment and footprint reference subset

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R1
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Optional manual envelopes in #712 do not supply OEM dimensions. Some library proxies are assumptions and cannot be scaled universally by MW.

## Research assignment

Which actual template battery/inverter/genset/transformer models have reusable dimensional/nameplate evidence for EU/NAM applications?

Candidate starting points, not import approval:

- https://www.energy.ca.gov/programs-and-topics/programs/solar-equipment-lists
- https://www.regulations.doe.gov/certification-data/
- Exact OEM model/revision dimensional drawings and installation manuals selected from actual template needs; landing pages alone are insufficient.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `gridspine/templates/data/campus_assets.yaml`
- `gridspine/templates/campus_assets.py`
- `pypsa-gui/frontend/src/site3d/assetLibrary.ts`
- `pypsa-gui/frontend/src/site3d/library/README.md`
- `pypsa-gui/frontend/src/site3d/assetLibrary.test.ts`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Rank classes/ratings by shipped example demand; select a bounded first subset with model/revision, units, rated conditions and explicit manufacturer-reported label. Research body dimensions, installed envelope, service clearance and land separately; record unavailable dimensions.
2. After rights/consumer review, add permitted reference fields to the existing catalogue/proxy seam. Read #712 at its current state: consume its schema only if merged, otherwise keep the collection reference-only and coordinate the dependency. Do not recreate its editor.
3. Use counted units or a documented configuration for sizing; explicit user envelopes retain priority. Reuse reference compare/apply and current Guided/Expert editors; fallback assumptions stay labelled. No full 3D generation from #711.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-04-01 | Convert a stated mm envelope and an independently specified unit count. | Expected metre dimensions/total area follow hand arithmetic; no inferred service clearance. |
| LG-04-02 | Scale 10 MW to 50 MW for an OEM unit. | Use documented count/configuration or refuse; never stretch all dimensions by five. |
| LG-04-03 | User has explicit dimensions and library edition changes. | Existing project dimensions stay unchanged until selected in an approved diff. |
| LG-04-04 | Dataset identifies an ESS model but has no dimensions or invalid ratings. | No fabricated footprint; missing or incompatible fields stay unavailable. |
| LG-04-05 | Preview/apply/reload a compatible record in Guided and Expert. | Shared native identity and source persist; preview leaves solve intact; denial changes nothing. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_references.py`
- `test_library_reference_apply.py`
- `test_library_defaults_pack_campus_reference.py`
- `Frontend site3d/assetLibrary.test.ts; footprint tests from merged #712`

## Stop conditions and handoff

No redistribution without exact rights, no product suitability/compliance guarantee from CEC/DOE inclusion, no #712 branch rewrite. A merged #712 is an apply dependency.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
