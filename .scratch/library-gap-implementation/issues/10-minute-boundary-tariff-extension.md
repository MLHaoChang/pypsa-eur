<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-10: Conditionally support minute-aligned tariff bands

Status: needs-triage
Type: research-and-implementation
Priority: P2
Group: R3
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

TariffPeriod stores integer hours although settlement may be 15 or 30 minutes. Fractional-hour TOU cannot be represented faithfully.

## Research assignment

Does an admitted high-priority tariff require a 00:30/quarter-hour boundary, and what backward-compatible schema/hash/engine change is justified?

Candidate starting points, not import approval:

- Exact selected official tariff from ticket 08/09 plus native TariffPeriod validation, matching and LP bindings.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/models/commercial.py`
- `pypsa-gui/backend/services/commercial/tariff_engine.py`
- `pypsa-gui/backend/services/library/items.py`
- `pypsa-gui/frontend/src/pages/results/investment/TariffBuilder.tsx`
- `pypsa-gui/frontend/src/pages/results/investment/tariffModel.ts`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. First document a concrete admitted schedule blocked by a fractional boundary. Submit a schema/engine/UX plan choosing one canonical minute representation and precedence/migration rules. If no supported high-value case needs it, defer and keep refusal tests.
2. Implement only approved minute-band semantics end to end: model, validation, matching, bill and LP consumer, editor, item/bundle schema compatibility. Distinguish demand integration, settlement and TOU boundaries.
3. Keep old integer-hour saved items/hashes valid; mint a new item version for new value fields using existing compatibility policy. Define local-time DST and half-open midnight/overnight behavior explicitly.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-10-01 | Evaluate a band boundary at 00:29, 00:30 and 00:31. | Only the approved half-open minute band matches; no integer truncation. |
| LG-10-02 | Load/export an old integer-hour tariff. | Its values, old content identity and previous bills remain unchanged. |
| LG-10-03 | Use 30-minute settlement with hourly TOU and vice versa. | Each independent time concept is respected; none supplies the missing one. |
| LG-10-04 | Cross midnight and both DST transitions in local time. | No gaps/double matching outside the reviewed policy; expected UTC interval table matches. |
| LG-10-05 | Compare bill evaluation and LP-selected energy under the same tariff. | They use identical minute-band semantics and independent expected costs. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_tariff_engine_core.py`
- `test_tariff_engine_urdb.py`
- `test_lp_tariff_capacity.py`
- `test_library_items.py`
- `Frontend TariffBuilder.test.tsx and tariffModel.test.ts`

## Stop conditions and handoff

Conditional scope only, requires a demonstrated tariff consumer, schema/finance plan and UX review. Does not implement TenneT weighted peaks or unrelated ratchets.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
