<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-03: Bind interpretation-critical source semantics without rewriting numerical pins

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R0
Blocked by: LG-01
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Numeric series hashes do not certify currency, product, interval labels or source revision. SeriesMeta has only limited descriptive fields.

## Research assignment

Which immutable interpretation fields must accompany an admitted record, and how can the existing pins/bundles carry a separate manifest without breaking older projects?

Candidate starting points, not import approval:

- Native SeriesMeta/_canonical and Library items/bundle contracts; .scratch/project-workflow/library-and-live-lookup.md hashing rules.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/series_store.py`
- `pypsa-gui/backend/services/library/items.py`
- `pypsa-gui/backend/services/library/bundle_pins.py`
- `pypsa-gui/backend/models/commercial.py`
- `pypsa-gui/backend/routers/library.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Write a compatibility/schema decision first: separate versioned semantic manifest linked to the existing numerical ID/version/hash, containing currency/unit/basis/product/node/interval-label/timezone/revision/field-source identity. Store descriptive provenance, scope and verification outside value identity as current policy requires.
2. Extend existing store/bundle resolution with optional manifest binding. Old refs retain exact hashes and load normally but do not gain verified financial interpretation. Monetary use of newly admitted data requires complete compatible semantics.
3. A semantic change creates a new semantic version/binding even when numbers coincide; confirmation metadata alone does not. Enforce org ACL and exported scope; no generic new item kind without a demonstrated consumer.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-03-01 | An old numeric ref is loaded/exported/reloaded. | Original numeric bytes/hash and behavior are unchanged; unknown semantics remain visible. |
| LG-03-02 | Same timestamps/values, different EUR/USD or DA/RT product. | Numeric pin may match; semantic identity/binding differs and incompatible monetary use is refused. |
| LG-03-03 | Verification or promotion metadata changes. | No value hash rewrite; descriptive state cannot alter the bound financial interpretation. |
| LG-03-04 | Manifest is tampered with, missing or belongs to another org. | Resolve/apply fails safely; no numeric-only claim of verified currency. |
| LG-03-05 | A complete series and manifest are bundled twice. | Roundtrip preserves both identities and stable canonical semantics without importing restricted bytes. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_series_store.py`
- `test_library_items.py`
- `test_library_bundle_pins.py`
- `test_library_export_series.py`
- `test_settlement_references.py`

## Stop conditions and handoff

Do not add interpretation fields to old numeric hashes, auto-fill ambiguous metadata or silently migrate legacy pins. Resolve schema/hash policy before building.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
