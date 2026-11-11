<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-11: Admit exact EU and US historical wholesale-price products

Status: needs-info
Type: research-and-implementation
Priority: P1
Group: R3
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

Generic price CSVs lack a reliable product/node/currency/time-revision contract. German DA changed to 15-minute products in October 2025.

## Research assignment

Can one German day-ahead zone product and one CAISO DAM product be represented by existing monetary consumers with exact temporal/currency semantics?

Candidate starting points, not import approval:

- https://www.smard.de/en/15-minute-wholesale-prices-available-218078
- https://www.smard.de/en/all-about-our-data-download-section-210130
- Official CAISO OASIS DAM product documentation and exact bounded node/product resource from #524 LIB02.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/series_store.py`
- `pypsa-gui/backend/services/library/series_io.py`
- `pypsa-gui/backend/services/library/export_series.py`
- `pypsa-gui/backend/services/library/template_series.py`
- `pypsa-gui/backend/models/commercial.py`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Select exact zone/node, DA/RT, total/component, currency, interval labels/duration/timezone and revision/finality. Start with one day plus DST transition samples before a bounded full-year record. Preserve source granularity; do not assume a uniform hourly calendar.
2. Build pure fixture-driven parsers into existing series storage with ticket 03 semantic bindings. Refuse duplicates, gaps, incompatible units, missing currency/product and unsupported native consumers; negative prices remain valid.
3. Where a consumer requires resampling, specify an explicit duration/energy-preserving policy and an independent cashflow oracle. Historical prices are not a forecast or a present customer offer. No live refresh on project open.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-11-01 | Parse interval-ending versus interval-start data. | Expected UTC timestamps and durations match an explicit independent table. |
| LG-11-02 | Parse spring/autumn DST and negative values. | No lost/duplicate physical intervals; negative prices persist. |
| LG-11-03 | Mix CAISO nodes or DAM and RT components. | Product mismatch refuses; total is not accidentally summed with components. |
| LG-11-04 | Price a fixed 15-minute MWh trace. | Independent price-times-energy cost matches units and declared currency. |
| LG-11-05 | A final/revised edition changes semantics or values. | A new candidate/version is created; pinned project data remains unchanged. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_series_store.py`
- `test_library_export_series.py`
- `test_settlement_references.py`
- `test_template_price_series.py`
- `test_settlement_inputs.py`

## Stop conditions and handoff

No guessed interval labels, automatic USD/EUR conversion, unreviewed upsampling or rounding milliseconds. Native consumer compatibility is a gate before monetary apply.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
