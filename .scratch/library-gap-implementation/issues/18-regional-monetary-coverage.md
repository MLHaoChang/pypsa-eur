<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-18: Expand local-currency cost/O&M coverage without EUR substitution

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R0/R2
Blocked by: LG-01, LG-02
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

CA/UK/US regional overlays each expose only 10 cost-value rows and explicitly list 90 unpriced monetary keys; the old EU base needs coordinated correction.

## Research assignment

Which high-use missing keys have exact regional technical/cost sources and consistent money/scale boundaries, rather than an unjustified FX conversion?

Candidate starting points, not import approval:

- Existing regional source_cells.csv and parameter-gap records; official selected US ATB/DOE, Canadian federal/provincial and UK primary technology references.
- DEA supports EU references but does not by itself establish a local installed USD/CAD/GBP cost.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/defaults_pack`
- `pypsa-gui/backend/services/library/defaults_pack/pricing.py`
- `pypsa-gui/backend/services/finance/packs/us_federal.py`
- `pypsa-gui/backend/services/finance/packs/ca_federal.py`
- `pypsa-gui/backend/tests/test_library_defaults_pack_regional.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Rank unpriced keys by actual template/library use and cost sensitivity, grouping technical nonmonetary rows separately. Select a small first subset of local investment/fixed/variable O&M rows with exact scope, scale, original money year and projection year.
2. After ticket 02, emit new immutable regional versions and source-cell records using the existing overlay builder/validator. Preserve old versions and unpriced lists for all remaining gaps. Explicit FX conversion, if ever justified, is a separate declared assumption, not a local sourced quote.
3. Check selected source values and currency/year/refusal behavior independently. Do not import tax/incentive rules or overwrite finance packs merely because technology cost is updated.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-18-01 | A high-use local monetary row has an admitted source. | Exact source value/unit/conversion appears in a new regional version only. |
| LG-18-02 | A monetary key is still missing locally. | Existing explicit refusal stays effective; no EUR row or zero is substituted. |
| LG-18-03 | A technical row is reusable across regions. | It retains technical applicability and does not acquire a fabricated local price. |
| LG-18-04 | New overlay is rebuilt on the corrected EU base. | Its inherited source/year labels and manifest are consistent without rewriting old overlays. |
| LG-18-05 | Compare a future-year scenario and a base-year cost. | Projection year is distinct from money year and any conversion has an independent oracle. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_defaults_pack_regional.py`
- `test_library_defaults_pack_pricing.py`
- `test_library_defaults_pack_money_year.py`
- `test_finance_packs.py`

## Stop conditions and handoff

No promise to fill all 90 keys; public evidence may remain absent. Finance-policy and money-year decisions stay separate from dataset enrichment.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
