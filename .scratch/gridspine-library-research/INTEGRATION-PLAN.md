# Parameter-library integration sequence

Status: proposed synthesis of saved research; final cross-source review and exact importer contracts remain pending. This document does not authorize mirroring restricted data or changing native model schemas without their normal review.

## Architecture

Extend the existing `services/library/items.py`, bundle pins, defaults packs and series store. Native scalar item kinds currently cover tariffs, contracts and connection agreements; equipment, reliability and other proposed kinds need explicit schema/validation work. Keep one logical library with existing common/org/local scope semantics, not a second database per card.

Separate three kinds of evidence:

1. **Reusable parameter/model records:** technology-class costs, lifetimes, equipment properties, calibrated curves or supported control presets, each with applicability and uncertainty.
2. **Time series and events:** price/weather/availability observations and outage revisions, whose interval/state/coverage are preserved. A derived parameter is a separate reviewed item, not a relabelled event archive.
3. **Dated rules and benchmarks:** tariff schedules, grid-code clauses, official awards, macro indices, supply-quality benchmarks and regulatory references. A public benchmark is not the site's actual contract, component failure law or eligibility verdict.

Project facts remain user/org-owned: actual topology/asset condition/fault level, meter and CMMS records, negotiated prices, actual vendor quotes, health, loan terms, risk criteria and operating authorizations. Credentials and computed optimization results are not library defaults.

## Import and use contract

Each proposed item retains original and normalized values, units, currency/money year and projection year, real/nominal/LHV/HHV basis, geography/market/operator/voltage/frequency/size/class, effective/publication/retrieval dates, source URL/row/clause/hash where lawful, transformation version, uncertainty and rights/access status. Distinguish measured, synthetic, fitted, assumed and illustrative.

Use the existing find → review → use → save lifecycle. Import into a draft/quarantine state, validate against the native schema and supported scope, then show a source-backed old→new proposal. Applying pins a version; source updates are “update available,” not silent project mutations. Keep verification/provenance metadata semantics from the existing library design; normalization fields that affect values/meaning belong in the versioned payload, not unhashed display metadata.

Access and redistribution are separate gates. No common seed pack is created from a restricted source merely because it is publicly reachable or a user has a key. Tenant-scoped acquisition is also conditional on actual permitted use. Dataset rights, original-document storage and raw/derived redistribution need separate records. Retain attribution/notices. Do not alter the general GET-only confined fetch rule to accommodate one API: a read-only POST or authenticated provider needs its own fixed, bounded, reviewed connector.

Connectors use administrator-controlled official hosts/endpoints, typed query parameters, response/row/time caps, revision-aware caching, rate limits and audit. Secrets remain server-side. Documents are untrusted data; no macros, formula execution, executable model imports or instructions from fetched pages. Imported values cannot widen permissions or source access.

## High-priority slices

| Slice | Deliverable | Acceptance |
| --- | --- | --- |
| L0 | Reconcile current master/library work with all80 parameter groups | Actual native fields vs proposed schema explicit; no duplicate store or conflicting implementation |
| L1 | Source/applicability/rights registry and draft item lifecycle | Units, effective dates, region, source evidence and permission gates validated; assumed data remains labelled |
| L2 | Regional techno-economic alternatives | Reuse technology-data/default packs; evaluate DEA, ATB/EIA, UK DESNZ and Canada CER. Separate overnight/installed, storage power/energy, FOM/VOM, connection cost, replacement and money basis; exact input-data rights checked |
| L3 | Source-backed equipment catalog | Start with native standard types plus selected PV/inverter/UPS datasets; enforce50/60Hz and equipment/test bases, standard type OR explicit parameters, no manufacturer/model certification inferred |
| L4 | Official tariffs and historical price series | Reuse URDB/native rating; EU DSO/TSO schedules and selected licensed NA market data. Match utility/customer/voltage/year/final-vs-provisional. Preserve15-minute/other native intervals, DST and negative-price clauses; unsupported terms refused |
| L5 | Reliability benchmark and class-parameter pilot | EIA861/OEB/Enedis supply-quality data separate from ERAA/NERC dated class assumptions. FOR/EFORd/exposure/MTTR semantics explicit; missing data not zero; no event-to-rate inference without denominators |
| L6 | Outage-event and private maintenance support | Revision/dedup/censoring and planned/forced state preserved; native planned-calendar support implemented before claiming it exists. CMMS data tenant scoped, not public defaults |
| L7 | Lifecycle, performance and control-depth records | Exact validated chemistry/model/temperature/duty and physical calendar basis; curated thermal/process/control model admission. Public cell data or generic dynamic specs cannot become pack/site guarantees |
| L8 | Coverage expansion and verification | Complete source-to-parameter joins, current scoped rights and actual file/API parsing evidence; source-specific numerical fixtures and downstream evidence invalidation |

Europe and North America are the only target deployment regions here. Start with sources whose actual scope and terms support the intended record. European tariff rollout can begin DE/NL/FR/ES/IT and expand to UK/Nordics/Switzerland. North America includes separate US utility/ISO, Canadian provincial and Mexican CFE/CENACE/CER or other applicable official contexts; do not convert a US assumption into local evidence by FX alone.

## Important gaps

- Public supply reliability and event notices do not provide a complete cross-sector UPS/BESS/process failure, repair and maintenance database. Actual duty/age/vendor/customer evidence remains necessary.
- Real negotiated PPA, forward commodity and delivered H2/NH3 quotes are commonly private. Auction strikes/premiums and historical observations are labelled references, not live bilateral offers or forecasts.
- Vendor controller, protection, black-start and process safety settings are not generic reusable certified defaults.
- Some seemingly public catalogs/markets have explicit restrictions (examples include AHRI, PJM and AESO in the research). Do not seed them without the required rights.

## Validation before availability

Test real small imports for each admitted source/version, not just webpage access. Required cases include units/sign/currency/tax and input/output cost bases;50/60Hz/per-unit; provisional/final tariff dates; DST/leap/interval-energy settlement; missing values and source revisions; stale library pins; outage-in-profile double-count; FOR/EFORd and per-km/per-device exposure; maintenance vs forced outage; raw/derived rights; source injection/SSRF/caps; cross-tenant access; exact parsing traceability and unsupported native fields.

Release only the actually supported tested subset. A source catalog entry does not grant a checked chatbot result; claims still require the native assurance profile and retained evidence. Independent human domain review, real-use calibration and any tax/contract applicability decisions remain separate.
