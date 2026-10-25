<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-01: Reconcile the source-to-consumer register and shipped state

Status: ready-for-agent
Type: research-and-implementation
Priority: P0
Group: R0
Blocked by: None
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Original EU documents and research PRs describe some already-shipped readers/apply paths as future work. Source counts overlap and do not state field-level implementation or rights.

## Research assignment

Which exact source editions supply each advertised native parameter, which records are still assumed, and which source-to-consumer links are missing?

Candidate starting points, not import approval:

- Existing .scratch/gridspine-library-eu/{source-register.json,parameter-map.json,IMPLEMENTATION.md,HANDOFF.md}
- Existing .scratch/gridspine-library-research/terms/clearances.json; research PRs #491 and #524

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `.scratch/gridspine-library-eu/IMPLEMENTATION.md`
- `.scratch/gridspine-library-research/terms/clearances.json`
- `pypsa-gui/backend/services/library/reference_catalogue.py`
- `pypsa-gui/backend/services/library/defaults_pack`
- `pypsa-gui/backend/services/library/common_items`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Generate a reproducible bounded inventory from native packs, EU references, common items, campus YAML and template/study pins; preserve source IDs with explicit aliases to unmerged research IDs.
2. Store a source/field/consumer/rights/status register and annotate historical research statements with current code/test anchors; keep historical evidence intact. Track missing, benchmark-only, partial, reference-available, approved-apply and calibrated separately.
3. Record refresh policy per source: inspect a new edition, diff fields/basis/rights, review and publish a new immutable version; never auto-repin saved projects. Update this queue only with observed evidence.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-01-01 | Inventory deduplicates aliases without summing #491/#524 entries. | Counts reconcile to their own source/variant/row units and duplicate IDs are rejected. |
| LG-01-02 | Current reference apply and both partial TenneT records are mapped to code. | Register reports shipped apply and partial tariffs, not future-only or complete billing. |
| LG-01-03 | A source version has no native consumer or no rights record. | It is research/unavailable for apply, never admitted from its URL alone. |
| LG-01-04 | A new edition is detected. | An update candidate is recorded; existing project hashes/pins and values remain unchanged. |
| LG-01-05 | Re-run the extractor on identical native files. | Stable sorted output and hashes; changed source edition is a visible diff. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_defaults_pack.py`
- `test_library_references.py`
- `test_library_common_tariff.py`
- `test_library_bundle_pins.py`

## Stop conditions and handoff

Do not build a second catalogue service or ingest new values. Conflicting ownership or current implementation requires a named reconciliation item.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
