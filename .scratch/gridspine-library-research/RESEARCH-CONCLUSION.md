# Library research conclusion and recommended first integrations

Status: source-discovery, family-level mapping and prioritization complete. See SOURCE-REVIEW.md for the separate cross-source AI review and its exact scope. This is research readiness for staged source-specific pilots, not a populated database, verified importers, blanket redistribution permission or engineering approval.

## Coverage

The inventory covers **80 parameter families** across the five modular cards. The completed [family-to-source map](SOURCE-COVERAGE.md) and [structured join](source-coverage.json) give every family an explicit disposition:

| Disposition | Families | Meaning |
| --- | --- | --- |
| Partial candidates | 28 | At least a useful candidate subset or reference fixture exists; it does not fill every field or establish source/asset applicability. |
| Benchmark only | 23 | Public class, historical or scenario values can inform labelled assumptions, not replace customer facts. |
| Method/reference only | 14 | Rules, models, methods or source context are available; they are not a ready parameter dataset. |
| Public-data gap | 15 | No adequate reusable public parameter set was identified for the actual target scope; retain the missing value or use authorized customer/vendor evidence. |

The catalog has **114 source assessments**: the original109 plus five explicit interruption-value, reliability-method and tax/incentive references. They are not114 distinct freely reusable databases. Raw source terms and precision differ by dataset; observed website access and a public API do not confer commercial redistribution rights.

Every join retains the inventoried parameter names, units, native source paths/support and site-specific exclusions. It also distinguishes candidate data, benchmarks, synthetic fixtures, weather context, model structures and restricted/unverified references. Exact source-table/cell-to-supported-native-field binding remains an implementation task for each admitted connector; this research does not fabricate a mapping to an unread spreadsheet cell.

## Priority list

| Order | First integration | Useful sources and scope | Admission before use |
| --- | --- | --- | --- |
| 1 | Regional CapEx/OpEx, efficiency and lifetime assumption packs | Existing technology-data/default packs; DEA technology chapters; US ATB/EIA; UK DESNZ; Canada CER EF2026 | Exact selected file/rights, original money/projection year, overnight vs installed scope, fixed vs variable O&M, storage power/energy and replacement boundaries |
| 2 | Equipment performance and nameplate subsets | Native standard types, selected CEC/SAM PV/inverter entries, ENERGY STAR UPS, DOE/NRCan product lists | Frequency/voltage/test bases, catalog model identity, applicable rights, actual supported fields; no inference of fault/transient behavior from efficiency |
| 3 | Official tariff library pilots | Existing URDB importer plus selected US utility books, European DSO/TSO schedules, Ontario/Quebec/BC schedules, CFE | Operator/customer/voltage/effective year, final vs provisional, levies/netting/demand terms, complete-versus-supported bill coverage |
| 4 | Historical market-price series | SMARD/Energinet; eligible ENTSO-E/Elexon datasets; selected permitted North American ISO/provincial/CENACE feeds | Exact data rights, node/zone/currency, market regime, timezone/interval and revision handling. Restricted PJM/AESO records remain references until rights permit use |
| 5 | Separate supply-quality and fleet-availability references | EIA861, OEB and Enedis supply indices; curated exact ERAA/NERC tables and definitions | Never convert SAIDI/SAIFI into component FOR/MTTR. Preserve FOR/EFORd/exposure, cohort, uncertainty and outage-inclusive profile guard |
| 6 | Lifecycle calibration references | Selected BatteryArchive study files and scoped storage planning-life references | Study rights, chemistry/cell/pack, temperature/SOC/duty, physical calendar time and replacement reset; planning cycle-life benchmark is not a calibrated ageing law |
| 7 | Economic normalization and regional benchmark context | ECB, BLS, Statistics Canada, Bank of Canada, INEGI/Banxico; EPA/EEA factors | FX/index selection and raw/derived rights; no automatic local-cost calibration, project WACC, marginal emissions or green certification |
| 8 | Process and operating references | DEA renewable fuels/industrial heat, DOE H2A, exact grid-code/model references | Product/feedstock/auxiliary boundary and source-specific support; actual mine/DC/OEM control/process data stays a named gap where unavailable |

The first useful outcome is a small, source-backed, versioned parameter set that the native model can consume correctly, not a large scraped warehouse or another modeling engine.

## Regional coverage and limits

- **Europe:** relatively strong public planning costs and official market/tariff references. EU, UK, Nordic and Swiss jurisdictions remain separate. Some sources are PDF/manual extraction, tariffs may be provisional, and generic network/device types are often50Hz. Day-ahead series must preserve the15-minute market transition rather than assume all prices hourly.
- **United States:** strong ATB/EIA/DOE/PNNL and product/utility data, with useful public supply benchmarks. ISO data terms vary; individual tariffs still need class/date/term validation. Published PPA sample prices are historical and selective.
- **Canada:** CER provides Canadian cost/performance assumptions; provincial sources provide tariffs/market reports and Ontario reliability context. Provincial market regimes and rights differ. An Ontario dataset is not a national parameter set.
- **Mexico:** CFE tariffs, CENACE market reports and INEGI/Banxico economic references are identifiable. A generally reusable machine-readable local equipment-cost, component-failure, maintenance or delivered-product-price library is not established. Do not fill those gaps using US defaults labelled Mexican.

## Important exclusions

The research did not establish open universal datasets for campus UPS/BESS/process fail-on-demand and repair, actual site fuel logistics, approved controller/protection/black-start/reconnection settings, mine recovery/throughput, real DC workload/cooling operation, or current negotiated H2/NH3/offtake prices. Customer/organization-owned data and explicit applicability review are needed. A warranty/MTBF claim is not a calibrated availability law.

Public PPA archetypes, auction strikes, support premiums and Bonneville power rates are different products. None is a current corporate PPA quote. Policy/depreciation references are versioned legal context, not inferred tax/incentive eligibility or a complete cross-region tax pack.

## Implementation boundary

Follow [INTEGRATION-PLAN.md](INTEGRATION-PLAN.md): one existing library/store lineage, source-backed draft → review → use → save, pinned versions, scoped permissions and retained provenance. Source updates never silently mutate projects. Native supported schemas, private-data isolation, strict bounded source access and checked-answer evidence remain in force.

Source-specific rights confirmation, selected-release/content validation, exact native field binding, actual ingestion/numerical tests and customer/domain calibration are still required before availability. These are implementation and admission gates, not undisclosed unfinished source research. No production dataset or new adapter was ingested/executed by this completion work.
