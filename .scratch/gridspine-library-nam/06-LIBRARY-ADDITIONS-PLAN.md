# Implementation plan: six additions to the existing Library

Status: proposed implementation backlog, published for implementation handoff. This PR contains the plans and research only. No new feed, tariff, contract, cost row or calculation is activated by it.

This document makes the six recommended collections explicit. Read it alongside [native requirements](01-DATA-REQUIREMENTS.md), [source assessments](02-SOURCE-CATALOGUE.md), [the detailed validation matrix](05-INTEGRATION-VALIDATION.md) and [master reconciliation](MASTER-RECONCILIATION.md). The machine-readable backlog is [library-additions-plan.json](library-additions-plan.json).

## Shared prerequisite: NAM-0

Before numerical admission, audit the current master Library implementation, including subsequent common/reference/apply work. Reuse its item/series stores, reference reader, regional pack versions, source pins, approvals and exports. Add fields or migrations only after proving a concrete gap. The source register is research evidence, not a runtime whitelist.

Every collection requires exact source/release identity, applicability, permitted use, currency/unit, economic basis, time convention, immutable numerical and semantic references, and a supported path to apply or refuse it. Verify rights for hosting, caching, customer display, derived results and export. A metadata-only link can remain useful where data admission is not cleared. Keep private customer quotes and contracts tenant-scoped.

Acceptance: validation rows A01–A04 and S01–S03 in the detailed matrix; independent arithmetic oracles wherever a collection changes monetary results. Source parsing alone does not establish correct application calculations.

## Proposed collections

| ID / priority | New content and sources | Existing integration seam | First bounded deliverable |
| --- | --- | --- | --- |
| LIB-01 / P1 | Verified US commercial/industrial tariff records; `us_urdb`, authoritative `us_pge`/`us_dominion` schedules | Existing URDB importer, native tariff/item storage and commercial configuration | One current eligible customer-class schedule whose complete chosen bill is supported and independently reproducible. |
| LIB-02 / P1 | Historical wholesale prices; `us_caiso` first, then `us_nyiso`, `us_miso`, `ca_ieso` | Existing series import/store, source metadata and `PriceSeriesRef` | One rights-cleared CAISO DAM product/node/day, with product/components/calendar/revision identity pinned. |
| LIB-03 / P1 | Historical PPA benchmark references; `us_lbl_solar`, later `us_lbl_wind` | Current reference reader/listing/compare pattern; supported contract apply path where explicitly valid | A small attributable selection of executed solar benchmark evidence, clearly separated from a user's contract strike. |
| LIB-04 / P2 | Reported wholesale contract/transaction observations; `us_ferc_eqr` | Library reference/series infrastructure after current EQR schema reconciliation | One current quarter, one selected product/unit and reproducible contract-to-transaction joins, including revised filings. |
| LIB-05 / P2 | Financial reference series: FX, inflation and rate benchmarks; `ca_boc_fx`, `ca_statcan`, `us_macro`, later verified Mexican sources | Existing series/reference storage, finance input preview and explicit transformation lineage | The observed CAD-per-USD series as a dated reference; numerical use only after the complete currency path is certified. |
| LIB-06 / P2 | Additional technical/O&M/performance references; `us_lbl_solar`, `us_lbl_wind`, `us_h2a`, later validated projection releases | Existing regional cost overlays and reference reader; separately versioned rows only when basis matches | One scoped empirical cost/O&M reference with inclusion/exclusion notes and an explicit statement of missing equipment-level coverage. |

### LIB-01: verified tariff records

Preserve utility/territory, class, voltage, effective sheets, eligibility, energy/TOU/tier structures, billing-demand window, ratchets, fixed fees, riders and all unsupported clauses. The recent SDG&E URDB page is useful rejection evidence: its reactive charge and apparent voltage inconsistency prevent assuming a complete supported tariff. Select the positive pilot only after its utility schedule has been reconciled.

Deliver a source-backed Library preview and explicit application to the active project. Do not silently discard minimum bills, reactive/kVA charges, coincident peaks, standby or export terms. A positive end-to-end pilot requires the full chosen bill; if it cannot be represented, the result is a clear unsupported status, not a truncated “verified tariff.”

Acceptance: T01–T07, E01–E02. Independent component-level bill oracle with source rounding; interval demand must use adequate measured/modelled resolution. Next-country work is NAM-4 for Ontario, then NAM-6 for Mexico, each with its own bill rules and source access gates.

### LIB-02: historical market prices

Preserve operator, node/hub/zone, DA/RT product, total versus components, source currency/unit, interval start/end, source clock, coverage and revision/finality. Start with the native-supported CAISO region after intended-use clearance. Its `PRC_LMP` CSV uses a value column named `MW`; parse from query/schema context, not that label alone.

Begin with one day and explicit missing/duplicate/quality reporting before extending history. NYISO requires its own timestamp/sign rules; MISO explicitly uses fixed EST; IESO distinguishes creation from delivery and renewed-market products from legacy HOEP. New raw series are not proof of market-engine support.

Acceptance: M01–M08, A01–A04, E01–E02. Independently reproduce `price × energy` for the selected product, including negative prices and actual interval duration. PJM remains conditional on exact entitlement/rights; a wrapper cannot clear it. No current forward curve or ancillary/capacity revenue engine is part of this deliverable.

### LIB-03: PPA benchmarks

Store execution vintage, technology, region, tenor, observed profile, currency, money year, first-year/levelized, nominal/real, discount/indexation basis, sample limits and provenance. Solar executed prices in the inspected sheet are levelized real 2023 dollars at 4% real; the offered vendor indices are first-year nominal observations and require separate rights treatment. Wind's inspected header uses real 2025 dollars and still needs exact workbook/sheet clearance.

Present references for comparison and deliberate assumptions. Do not auto-populate a signed PPA, reverse levelization without full assumptions, fill confidential/`#N/A` cells with zeros, or extend native financial contract forms silently. A benchmark can remain read-only until a defensible supported apply path exists.

Acceptance: P01–P06, P08, A01–A04, E01–E02. Retain independent reference-versus-contract labels through chat answers and exports. Future vendor offers, SEC exhibits, swaps and forward curves are separate admission scopes.

### LIB-04: regulatory contract observations

Retrieve and validate one current EQR schema/quarter. Preserve filer, quarter, filing/revision, contract and transaction keys, product, unit, delivery point, quantity and economic classification. Reconcile Order 917 changes rather than trusting only the legacy dictionary; do not assume an announced future submission format is operational.

Expose selected reported observations with coverage/selection limitations. They are not a complete corporate PPA price tape. Avoid double-counting corrected records or mixing energy prices with capacity, transmission and ancillary products.

Acceptance: P07–P08, A01–A04, S01–S03. Independently reproduce one selected record and aggregate from the current source with correct product/unit joins. Requires NAM-0; does not block the first tariff/market/benchmark release.

### LIB-05: regional finance references

Start with the observed Bank of Canada CAD-per-USD series, preserving direction, observation dates, source policy and missing-date rules. Add CPI and rate series only after actual numerical samples and exact series identities have been validated. Preserve real/nominal basis and money year; a risk-free rate is not WACC or a lending offer.

Offer a visible old→new assumption/derived-value preview with pinned FX/index inputs and method. No implicit weekend fill, silent rebasing, relabelled USD/CAD/MXN values or common currency fallback. Banxico/INEGI extraction remains a later verified addition, not an assumed completed feed. Tax/incentive references require separate dated eligibility treatment.

Acceptance: F04–F06, A01–A04, E01–E02. Independent FX direction/inversion and rebasing oracles, with stale-result/evidence behavior tested when assumptions change.

### LIB-06: equipment and operating-cost coverage

Reuse existing `us_federal` and `ca_federal` technology-cost overlays from PR #499. Add empirical O&M/cost/performance references first, keeping source inclusion boundaries and plant size/age/region visible. Only add regional monetary rows where the source's technology, AC/DC or input/output capacity basis and cost boundary match a native key.

Prioritise gaps in battery power versus energy components, hydrogen equipment and lifecycle replacements after suitable evidence is obtained. Current evidence does not justify inventing a battery cost split, treating H2A's legacy model economics as current defaults, or turning performance aging into an equipment failure distribution. Missing regional money rows must continue to refuse inappropriate EUR fallback. Asset-level maintenance/CMMS and private OEM/off-take quotes remain explicit gaps.

Acceptance: F01–F06, A01–A04 and relevant source safety checks. Use independent unit/cost-conversion oracles and preserve existing regional-pack pins; add a new version rather than editing a shipped one.

## Delivery sequence and handoff

1. Complete NAM-0. Each implementing session rechecks current master, relevant parallel PRs and repository review requirements.
2. First release: separate bounded PRs for LIB-01, LIB-02 and LIB-03. Each source can ship independently only when its applicable gates pass; no requirement to ingest all discovered sources.
3. Follow with LIB-04, LIB-05 and LIB-06, and separately scoped Ontario/Mexico pilots. Finance references can progress earlier if their small verified subset is useful and safe to apply.
4. For every implementation PR, name its source/product subset, changed files, independent oracle, rejection cases and applicable matrix IDs. Run required local/remote checks and ordinary approval/review controls before release.

Independent domain review and representative real-use calibration remain pending. This document does not authorize paid data subscriptions, reviewer contact, production activation or a full-coverage claim. The next implementing agent should use [HANDOFF.md](HANDOFF.md); raw third-party files are intentionally outside Git.
