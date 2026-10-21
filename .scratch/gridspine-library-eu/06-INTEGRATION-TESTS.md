# Integration sequence and test/validation matrix

## Integration order

| Slice | Concrete deliverable | Status in this package | Availability gate |
| --- | --- | --- | --- |
| EU-0 | Parameters, source research and reconciliation | 44 groups; 36 focused assessments; links to original family/source IDs | Structural checks, primary observations and explicit access/rights gaps |
| EU-1 | Optional versioned native technology-reference reader | Implemented: 24 explicit snapshots, five archetypes, native CostPart/PackValue and attribution wrapper | Exact data hashes, source-cell numeric oracles, unit/basis tests and no defaults mutation |
| EU-2 | Find/review/use for these references in existing Library UI/chat | Not implemented here | Preserve scope/money year and unsupported terms; approved old→new proposal, pinned evidence, no silent application |
| EU-3 | Danish DataHub supported energy-charge importer | Detailed field mapping, real API sample; not implemented | Confirm exact rights; currency conversion or explicit currency support, identity, null inheritance, validity and DST; known bill |
| EU-4 | DE/NL selected final network schedules | Exact documents/resources inspected; no numeric common seed | Rights, utility/category selection, utilisation/peak/contract basis, taxes/reactive exclusions and TSO/DSO no-duplication oracle |
| EU-5 | FR/ES/IT and AT/PT tariff subsets | Source-level mapping | Calendar/rule support, unsupported-term refusal, correction history and bill fixture; half-hour periods cannot be rounded |
| EU-6 | PPA/CfD archetypes and official benchmarks | Native fields reconciled; restricted documents linked only | Exact document/data rights, instrument/units/settlement compatibility, current user contract input |
| EU-7 | H2 component and process benchmark expansion | Actual Observatory/DEA cells assessed | Resolve component/unit/boundary, licences and projection vs historical assumptions; round-trip fixture |
| EU-8 | Maintenance/degradation calibration | Source references and explicit gaps | Cohort/exposure/operating-hour/calendar basis; OEM/customer applicability and independent domain validation |

## Validation matrix

| Area | Required test cases | Evidence now |
| --- | --- | --- |
| Exact source version | Changed workbook bytes, selected sheet/header/column, cached values only, licence notice, deterministic extraction | Extractor binds both primary workbook hashes; actual extraction and reproduction checked |
| Native values | Source-cell identity; million/kilo scaling; annual absolute FOM reconstruction; lifetime and efficiency domains; native JSON round-trip | Automated reference-reader tests |
| Applicability | PV AC vs DC, electrical input vs H2 output, LHV vs HHV, generic rooftop blend vs commercial-only, mixed money years | Automated selected-cell oracles and preserved wrapper |
| Missing/unadmitted input | Missing year, SOEC lifetime anomaly, unsupported technology, string/bool year, VOM/stack/water exclusions | Automated refusal/omission tests; anomaly documented |
| Reproducibility | Reference-byte corruption; fresh independent return values; default pack identity/hash unchanged | Automated tests plus existing pack suites |
| Tariff units | ct/kWh, EUR/MWh, kW/MW/kVA, monthly/annual/day, currency and tax basis | Planned per-connector bill fixtures; no passed importer claim |
| Tariff dates and calendars | Future/provisional revision, ValidFrom query vs overlap, end-date convention, DST, holidays, 15/30/60 min, half-hour band | Source findings mapped; tests required before connector admission |
| Complete bill | DSO-included TSO, meter/connection/levy scope, reactive and excess rules, utilisation threshold, class and non-firm eligibility | Known-bill oracle still required per selected tariff |
| Commercial instruments | Physical/financial legs, buy/sell signs, share/notional, interval vs capture reference, indexation, negative prices, certificates/merchant double count | Existing native scope identified; no new instrument implementation asserted |
| Product semantics | Electricity CfD vs H2 support premium; H2 CapEx EUR/kW vs LCOH contribution EUR/kg; cost vs offtake break-even | Explicit mapping/admission exclusions; next importer needs numerical fixtures |
| Degradation/maintenance | Stored fraction vs displayed percent, operating vs calendar hours, stack vs plant, planned vs forced, censoring/exposure, no-outage double count | Source inspection only; no physical ageing/maintenance model admitted |
| Rights/security | Dataset vs code licence, restrictive contract terms, third-party exceptions, private scope, fixed endpoints/caps, no source instructions/macros | Source register and offline-only reader; remote connector/tenant tests deferred until those connectors exist |
| Evidence and checked answers | Source pin, project change invalidation, preserved assumptions, appropriate native assurance profile | UI/apply/checked-answer wiring remains separate; source citation alone is not a checked result |

No engine, UI, solver, contract valuation or live-fetch permission is changed here. Independent human/domain review, customer calibration and statutory tariff/contract eligibility remain separate. See VALIDATION.json for exact executed commands/results; remote CI is reported separately from local tests.
