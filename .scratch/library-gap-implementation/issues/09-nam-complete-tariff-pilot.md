<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-09: Add one complete US C&I tariff and stage Canadian/Mexican coverage

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R3
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

#524 describes six collections; URDB alone does not prove a complete current utility tariff or broad NAM coverage.

## Research assignment

Which exact US utility schedule has a representable C&I tariff and independent published evidence; what province-specific Canadian/Mexican requirements remain?

Candidate starting points, not import approval:

- Existing URDB adapter and #524 LIB01; exact official utility/regulator schedule matching the selected URDB revision.
- Province/utility-specific Canadian and verified Mexican official sources are follow-up candidates, not first-pilot deliverables.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/urdb.py`
- `pypsa-gui/backend/services/library/items.py`
- `pypsa-gui/backend/services/commercial/tariff_engine.py`
- `pypsa-gui/backend/tests/test_tariff_engine_urdb.py`
- `pypsa-gui/backend/tests/fixtures/investment_case/us_tariff_demand_charge.json`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Reconcile LIB01/NAM-0 with ticket 01, then select one exact eligible schedule, URDB version and official-source revision. Enumerate tiering, TOU, demand integration/ratchets, taxes, riders, export and minimum-bill clauses.
2. Normalize with existing URDB/tariff paths using source spans and explicit USD/timezone/effective dates. Unsupported clauses remain partial/refused; do not replace them by zero. Source rights govern common versus user-scoped publication.
3. Validate independent monthly and annual bills, including a peak that differs between 15-minute and hourly demand integration. Record a precise next-province requirement list without claiming Canada/Mexico are integrated.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-09-01 | An official schedule and URDB record disagree. | Exact revision discrepancy blocks admission until reconciled; no preferred-number guess. |
| LG-09-02 | A short spike has different hourly and 15-minute billing peaks. | Independent demand-interval oracle matches the chosen tariff semantics. |
| LG-09-03 | Ratchet/minimum-bill/rider is required but unsupported. | Partial/refusal is explicit; total is not sold as complete. |
| LG-09-04 | Compare an eligible USD tariff and a CAD/EUR reference. | No silent currency fallback or unchecked conversion. |
| LG-09-05 | Save/reload/export the admitted tariff in its permitted org. | Identity, source edition, eligibility and all native billing terms persist; other org access is refused. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_tariff_engine_urdb.py`
- `test_tariff_engine_demand.py`
- `test_library_items.py`
- `test_library_router.py`
- `test_settlement_references.py`

## Stop conditions and handoff

One complete US schedule is the bounded first deliverable. No broad NAM coverage claim and no live API key or paid tariff service required for CI.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
