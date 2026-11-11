<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LG-12: Refresh renewable-cost/PPA benchmark editions and comparability

Status: needs-info
Type: research-and-implementation
Priority: P2
Group: R4
Blocked by: LG-01, LG-03
Baseline: master `aba252d2384f55116bbd15516a5b025951ba265e`; recheck before claiming.

## Observed gap

#524 inspects a 2025 Berkeley Lab solar workbook; a 2026 release is now published. Exact new cells/rights have not yet been inspected.

## Research assignment

Which 2026 solar/PPA and European onshore-wind/PV cost cells are comparable after AC/DC, hybrid, money-year, vintage and price-basis normalization?

Candidate starting points, not import approval:

- https://emp.lbl.gov/publications/us-utility-scale-solar-2026-edition
- https://ens.dk/technologydata
- Existing #524 LIB03/LIB04 source records; official European CfD auction results for a separately labelled benchmark only.

Apply [the source protocol](../research-protocol.md). Deliver a field/source map, exact resource/rights evidence, bounded permitted fixture or extraction recipe, independent numerical/time/identity oracle, and a GO/NEEDS INFO/NO GO record for a specified consumer. Do not mark a candidate verified from a landing page. Desk research can prepare before dependencies; implementation completion needs all listed dependencies and any ticket-specific gates.

## Native anchors and bounded scope

- `pypsa-gui/backend/services/library/reference_catalogue.py`
- `pypsa-gui/backend/services/library/europe_reference.py`
- `pypsa-gui/backend/services/library/reference_data/README.md`
- `pypsa-gui/frontend/src/pages/results/investment/LibraryReferences.tsx`

These are inspected anchors, not permission to rewrite whole directories. Before code, record an exact file allowlist including relevant tests, permitted new reference/fixture files and one release fragment. If a new route, item kind, engine, migration or out-of-scope path is needed, return a scope amendment for review. Check the outsourced register and open PR overlaps first.

## Implementation steps after admission and required plan gates

1. Fetch a permitted bounded official subset and verify exact workbook version, cells, rights and third-party sheet exclusions. Prior OEDI 429 was not a verified resource; record access failure rather than bypass it. Diff against #524 old source fields without overwriting them.
2. Create a versioned read-only benchmark subset with execution/COD vintage, region, PV/wind/hybrid, AC/DC, CapEx/O&M, original money year, first-year versus levelized price, escalation and discount basis.
3. Use the existing reference presentation after compatibility review. Historical PPA benchmarks, FERC-reported contracts, auction CfD strikes and customer offers stay different categories. No automatic contract-price application or invented PPA/swap forward curve.

## Planned acceptance tests — write first

| ID | Trigger | Required observation |
|---|---|---|
| LG-12-01 | Compare identical numbers labelled first-year versus levelized. | They are not treated as interchangeable contract strikes. |
| LG-12-02 | An AC plant cost is mapped to DC capacity. | A sourced ratio/explicit conversion is required; otherwise comparison refuses. |
| LG-12-03 | A workbook sheet embeds third-party price-index data. | Uncleared cells are excluded even if other sheets are reusable. |
| LG-12-04 | A 2026 release is admitted while a project pins the older reference. | Old reference bytes/basis remain available and unchanged. |
| LG-12-05 | A chatbot is asked for a current executable PPA quote from historical data. | It labels the benchmark and states the missing offer, without manufacturing a price. |

Regression anchors (locate renamed/equivalent tests during the current-master audit; do not invent execution):

- `test_library_references.py`
- `test_library_europe_reference.py`
- `test_finance_contracts_p4.py`
- `Frontend LibraryReferences.test.tsx`

## Stop conditions and handoff

No new release values/rights are asserted until cells are inspected. Financial model/basis changes need plan review; future market/contract price disclosure can remain a public-data gap.

Follow [spec.md](../spec.md) and [HANDOFF.md](../HANDOFF.md). Keep source, numerical values, semantics, applicability and approval evidence distinct. Record unresolved fields and safe fallbacks; software test success does not establish independent domain calibration. All five acceptance IDs remain planned until executed evidence is attached.
