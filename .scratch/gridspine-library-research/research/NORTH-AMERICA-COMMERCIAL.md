# North America: prices, tariffs and PPA reference data

Research date: 2026-10-20 (Europe/Berlin). Scope: United States, Canada and Mexico. Status: source/access/terms research, not data ingestion, price quotation, legal clearance or an implemented connector. No accounts registered, authenticated data APIs exercised, large datasets downloaded or paid calls made.

The machine-readable catalog is [northamerica-commercial-sources.json](northamerica-commercial-sources.json): 19 primary-source candidates, with geographic coverage, parameters, formats/access, reuse status, limitations, priority and proposed integration mode. Priority identifies research/product value; it does not override access or rights gates.

## Main recommendation

Reuse GridSpine's native tariff/contract/connection library and time-series store. The best initial North American library is a combination of (a) a small verified rate library, (b) explicitly labelled statistical benchmark ranges, and (c) optional approved, user-scoped market-data connections. It should not begin as a mirrored database of every ISO's public data.

**Public access is not the same as open redistribution.** The strongest concrete issue is PJM: [Data Miner](https://dataminer2.pjm.com/) says internal use and prohibits redistribution of both original and derived data without its redistribution license. [AESO's legal page](https://www.aeso.ca/legal/) permits non-commercial/personal/educational copying under conditions and requires permission for other uses. These sources cannot be promoted into a commercial common seed library by simply transforming their data or using an open-source wrapper.

## Parameter families worth reusable records

| Library record | Parameters / required applicability | Native destination / separation |
| --- | --- | --- |
| Retail supply tariff | Energy/TOU/tier charges; monthly demand, billing window, ratchet; fixed/rider/minimum/standby/reactive/contract-demand conditions where supported; utility, class, voltage, geography, effective dates | Existing `tariff` item; explicit supported/unsupported terms. Actual customer eligibility is not inferred from an industry label. |
| Grid service / connection terms | Import/export/transmission capacity charges, one-time connection obligations, service class and contractual limits | Existing `connection_agreement` and tariff items as appropriate; no duplicate charge on both. Actual POI limit remains a site input. |
| Historical wholesale series | Market, node/hub/zone, DA/RT, interval, components, currency, time basis, preliminary/final/revised state | Existing series store and references; never a retail all-in tariff or forecast. |
| Fuel and average-price benchmark | State/sector retail averages, gas/diesel prices, source vintage, delivery/tax/heating-value basis | Clearly labelled scenario/benchmark series; average price cannot reconstruct a demand-charge tariff. |
| Contract archetype / PPA benchmark | Public observed price ranges, vintage, term, shape, escalation, REC/tax/credit basis; known auction/award terms only where published | Existing `contract` item for an illustrative archetype. A current bilateral quote and confidential clauses remain user-provided. |

API credentials are not library parameters. Store only credential references under the existing secure tenant connection mechanisms, never keys in item JSON, exports, prompts or logs.

## Source shortlist

| Source | Useful data and access | Admission / caveat |
| --- | --- | --- |
| [OpenEI URDB](https://apps.openei.org/services/doc/rest/util_rates/) | US machine-readable rate structures; free API key; manual/full-download options documented | P0 candidate. Reuse existing importer. Primary landing/catalog returned429 during direct research; exact record-specific reuse terms still need verification. Do not adopt contradictory third-party CC0/CC-BY claims as proof. |
| [PG&E tariff book](https://www.pge.com/tariffs/en/rate-information/electric-rates.html) | Actual industrial/commercial rate sheets such as [B-20](https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_B-20.pdf) | P1 official-document cross-check; source/valid-date/class/service rules and copying terms matter. A regional sample, not nationwide coverage. |
| [EIA API v2](https://www.eia.gov/opendata/documentation.php) | Electricity/fuel historical benchmarks, downloadable and API series | P0. [EIA states public-domain reuse](https://www.eia.gov/about/copyrights_reuse.php), with stated exceptions/notices; API terms still apply. Exclude identified third-party material from blanket seeding. |
| [PJM Data Miner](https://dataminer2.pjm.com/) | DA/RT LMP, load and ancillary market data; account/API | Defer shared seeding: explicit redistribution license required, including derived data. |
| [CAISO OASIS](https://www.caiso.com/systems-applications/portals-applications/open-access-same-time-information-system-oasis) | Public market report downloads, plus [historical pricing portal](https://oasis-bulk.caiso.com/) | P1 connection candidate. [Terms](https://www.caiso.com/privacy-terms-of-use) distinguish public-use website material, API/data rights and third-party material. Review exact product grant. |
| [ERCOT public-data portal](https://www.ercot.com/services/mdt/data-portal) | Public-report APIs and archives | P1 connection candidate; [registration/authentication](https://developer.ercot.com/applications/pubapi/user-guide/registration-and-authentication/) uses account/token/subscription key. API [terms](https://www.ercot.com/help/terms/data-portal) were not fully retrievable; no open distribution claim. |
| [MISO reports](https://www.misoenergy.org/markets-and-operations/real-time--market-data/market-reports/) / [Data Exchange](https://data-exchange.misoenergy.org/apis) | Pricing/reserve CSV reports, JSON/API products | P2. Official report-to-API transition is not a reason to guess current endpoint semantics; pin chosen product/version and rights. |
| [NYISO market data](https://www.nyiso.com/energy-market-operational-data) | LBMP and market reports | P2 candidate; browser verification prevented full direct inspection. No bypass or live-connector claim; official endpoint/terms pilot required. |
| [ISO-NE Web Services](https://www.iso-ne.com/participate/support/web-services-data) | Registered REST XML/JSON, DA/RT prices and reserves, final/preliminary distinctions | P2; some internal nodes withheld. [Legal terms](https://www.iso-ne.com/legal-privacy) do not establish an open redistribution grant. |
| [SPP public marketplace](https://portal.spp.org/groups/day-ahead-market) | DA/RTBM location prices and related reports | P2; report/market/location/timezone and terms review required; Western services distinct from SPP East. |
| [Bonneville power rates](https://www.bpa.gov/energy-and-services/rate-and-tariff-proceedings/power-rates) / [transmission rates](https://www.bpa.gov/energy-and-services/rate-and-tariff-proceedings/transmission-rates) | Public wholesale/service schedules and adjustments | P1 for eligible Northwest customers. BPA is Bonneville, not a synonym for PPA. Base rate-case schedules require subsequent adjustment/effective-date checks. |
| [Berkeley Lab utility-scale solar data](https://eta.lbl.gov/publications/us-utility-scale-solar-2025-data) | Annual public nonconfidential XLSX; historical levelized PPA, project cost/performance and value evidence | P1 reference ranges. Selected historical/levelized PPAs are not current quotes or full contracts. Exact workbook/data rights and confidentiality exclusions remain attached. |
| [IESO data directory](https://www.ieso.ca/en/Power-Data/Data-Directory) | Ontario public CSV/XML market reports | P1. [OEMP replaced HOEP from May1,2025](https://ieso.ca/power-data/Price-Overview/Ontario-Market-Prices); do not splice regimes. [Terms](https://www.ieso.ca/Terms-of-Use) grant limited use/reproduction, not an open redistribution license. |
| [AESO API/reporting](https://www.aeso.ca/market/market-and-system-reporting/aeso-application-programming-interface-api/) | Alberta pool prices and related reports | Defer commercial shared use until [rights](https://www.aeso.ca/legal/) resolved; API access does not supply permission. |
| [OEB distribution-rate databases](https://www.oeb.ca/open-data/electricity-distribution-rates-databases) | All-class historical XLSX since2006; selected current-class XML | P0 Ontario source. [Open-data inventory](https://www.oeb.ca/ontarios-energy-sector/open-data) links [OGL Ontario](https://www.ontario.ca/page/open-government-licence-ontario); verify each asset inclusion. Distribution-only data is not the total bill. |
| [Hydro-Québec business tariffs](https://www.hydroquebec.com/business/customer-space/rates/) | Official electricity-rate PDFs and L/M/LG eligibility, demand and options | P1 Québec. Large data center does not automatically qualify for industrial Rate L; check LG and actual use. Public does not establish redistributable dataset rights. |
| [BC Hydro Electric Tariff](https://www.bchydro.com/toolbar/about/strategies-plans-regulatory/tariffs-terms-conditions/electric-tariff.html) | Approved distribution/transmission customer schedules and connection terms | P1 BC. BCUC-approved text prevails; OATT/retail schedules differ. Example structural change: RS1830 consolidated energy charges from2026-04-01. |
| [CENACE public SIM](https://www.cenace.gob.mx/Paginas/SIM/Reportes/PreEnerServConMDA.aspx) | Mexico PML/PND/ancillary reports and links to documented web services | P1 Mexico. [Designated open-data reuse statement](https://www.gob.mx/cenace/articulos/conoce-los-datos-abiertos-del-cenace) exists, but specific reports/manuals apply; original/recalculated traceability subset has demonstrative-only wording. |
| [CFE GDMTH official schedule](https://app.cfe.mx/Aplicaciones/CCFE/Tarifas/TarifasCRENegocio/Tarifas/GranDemandaMTH.aspx) / [tariff-scheme publication](https://www.cfe.gob.mx/hogar/infcliente/Documents/esquematarifariovigente.pdf) | Industrial tariffs by class/division/month and component | P1 curated source. Direct schedule returned502; exact monthly record acquisition and rights pending. US rates are not a substitute for Mexico. |

## Native integration implications

The native `services/library/urdb.py` already supports a meaningful subset of URDB: energy and TOU schedules, tiers, TOU/facility demand, fixed charges and supported ratchets. It explicitly names unsupported fields and can refuse an import. Its partial mode records unsupported terms and the billing engine returns no complete total. Preserve this behavior; a broad source catalog must not weaken it.

For example, minimum/annual minimum charges, coincident-rate fields, demand units other than kW, unsupported sale tiers and differing tier structures can fail mapping. Billing demand is often measured over15/30/60 minutes; hourly planning output cannot prove the actual monthly demand charge without suitable data. Preserve importer disclosures when a missing demand window is assumed. Customer acceptance is not scientific proof that a missing tariff term is zero.

Use one acquisition pipeline: source registry → jurisdiction/utility/location/service selector → bounded fetch or authorised upload → raw hash and retrieval/finality metadata → pure offline parser → per-value provenance and unmappable list → reviewed old/new item → existing content version/pin. Do not mirror copyrighted documents or restricted raw/derived market data into `common` merely because an API returns200.

Keep rights as separate dimensions: may retrieve, use commercially, cache internally, display in a customer's project, export with a bundle, and redistribute in common defaults. A licence may allow one and forbid another. Every downstream derived item retains source obligations. User/org retention cannot bypass a source's commercial-use restriction.

Market-series keys should include operator/product, node/zone/system, DA/RT, currency and price unit, interval duration, interval start/end convention, local timezone and UTC conversion, DST fold, settlement version, revision/finality and retrieval date. Mexico's regional DST rules and historic changes, Ontario's renewed market, and ISO-specific hour-ending/time-standard conventions make a single naive timestamp column unsafe.

## Practical rollout

1. **US pilot:** one eligible URDB industrial/commercial rate checked against its current official utility sheet and a known bill; EIA-owned benchmark series as clearly labelled context. Obtain exact dataset rights before shipping common records.
2. **Ontario pilot:** OGL-covered distribution-rate records paired with the appropriate commodity/Global Adjustment/transmission/tax basis. Keep a renewed-market series separate from legacy HOEP. A <50kW general-service XML feed cannot stand in for a hyperscale industrial tariff.
3. **Québec/BC pilot:** one approved large-customer schedule each, after eligibility/document terms review, with demand/contract/tax basis and dated source spans.
4. **Mexico pilot:** CFE verified monthly/division schedule and one CENACE designated open report, with exact PML node/system/currency/time/revision semantics. Start with authorised upload if automated access is not proved.
5. **Selected US ISO connection:** choose the actual customer market; do not implement seven ISO adapters before a case needs them. PJM/AESO remain rights-gated; any other feed requires its own scoped terms/access proof.

## Validation and honest gaps

- Rate fixtures must cover class/voltage/date applicability, time-window/DST, demand maxima, tier/ratchet history, riders/taxes/standby terms, unit conversion, unsupported partial import, known-bill reconciliation and expired/superseded schedule refusal.
- Price fixtures must cover missing/duplicate/repeated hours, revised/final prices, incorrect node or market, negative prices, currency, units and energy-weighted settlement. Historical observations are not forecasts or forward curves.
- Licensing tests should prevent a restricted source or descendant from `common` promotion, cross-tenant export or cache sharing. Reproducing official rate coefficients and redistributing the source PDF can have different rights.
- No comprehensive free current bilateral PPA quote library was established for US/Canada/Mexico. Public benchmark ranges and selected awards can seed illustrative assumptions only; tenor, shape, credit, technology, vintage, RECs, taxes and negative-price clauses matter. Do not silently backfill confidential prices or claim a negotiated contract from a benchmark.
- Public industrial H2/NH3 forward/contract prices and bespoke large-load riders remain major gaps. Fuel benchmark averages do not solve them. Preserve user-provided quotes and source-specific scenarios rather than invent precise values.

This catalog is research-ready. No source connector, seeded tariff pack or numerical billing validation was implemented or executed in this session.
