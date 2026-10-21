# European and North American parameter-library research

Research date: 2026-10-20 (Europe/Berlin). Publication prepared 2026-10-21. Status: source-discovery snapshot, not an implemented or approved data library.

The inventory covers **80 parameter families** across Energy (26), Load flow/grid (13), Reliability (14), Commercial (19) and Controls (8). Six source catalogs contain **109 source assessments**; assessments can cover different products from the same publisher and are not 109 independently tested or freely redistributable databases.

## Files

- [Parameter inventory](research/PARAMETER-INVENTORY.md), [structured fields/native mappings](research/parameter-inventory.json).
- [Technical components](research/TECHNICAL-COMPONENTS.md), [18 source assessments](research/technical-sources.json).
- [Costs and lifecycle](research/COSTS-LIFECYCLE.md), [18 source assessments](research/cost-sources.json).
- [Outages and maintenance](research/OUTAGE-MAINTENANCE.md), [18 source assessments](research/reliability-sources.json).
- [European commercial index](research/EUROPE-COMMERCIAL.md), [22 source assessments](research/europe-commercial-sources.json).
- [North American commercial research](research/NORTH-AMERICA-COMMERCIAL.md), [19 source assessments](research/northamerica-commercial-sources.json).
- [Profiles, macroeconomics and emissions index](research/PROFILES-MACRO-EMISSIONS.md), [14 source assessments](research/profile-macro-sources.json).
- [Combined machine-readable source catalog](source-catalog.json).
- [Proposed implementation sequence and validation](INTEGRATION-PLAN.md).

Europe includes relevant EU, UK, Nordic and Swiss sources, with dataset-specific coverage. North America distinguishes US, Canada and Mexico; a US value is not silently a Canadian or Mexican default. PPA benchmarks and Bonneville Power Administration (BPA) rate sources are both covered.

## What the status labels mean

`open_verified` means the research found an explicit applicable open/reuse notice for the described scope; it is not an importer, legal or engineering approval. `public_terms_review`, `mixed`, `restricted` and `unknown` must retain their stated limitations. Restrictions may apply to derived data as well as raw data. An API key does not grant redistribution rights.

The completed sector-plan review in the sibling directory does not cover this newer data-source catalog. Final independent cross-source review, complete source-to-field joins, selected release/rights confirmation and actual ingestion tests remain pending. Some topic notes were saved before their research sessions ended; publication-generated European/profile indexes summarize their saved JSON and do not claim a second completed review.

No bulk public dataset, customer record, API credential or paid database is included. No new connector or populated project library was implemented. Small public API observations reported in topic notes establish only those endpoint/schema observations, not general integration validation.
