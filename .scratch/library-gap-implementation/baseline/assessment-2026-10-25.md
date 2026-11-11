<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GridSpine library assessment and focused research plan

Assessment date: 2026-10-25. Actual fetched master: **aba252d2384f55116bbd15516a5b025951ba265e**. Open PRs inspected by exact head; unmerged code and documentation are not described as master features. No runtime library data or application code changed in this assessment.

## Conclusion

The useful next step is to close specific source-to-model gaps and complete small verified data collections. GridSpine already has substantial storage, provenance, cost-reference and commercial infrastructure. A larger source-link catalog alone would not fix its remaining gaps. The priority order is: reconcile money-year/pin and documentation drift; enrich equipment technical and dimensional records; improve battery/H2 lifecycle assumptions; complete selected tariff/market/PPA pilots; then targeted reliability/process/control datasets with honest applicability.

Separate three questions for every candidate: does a source exist, is the exact resource admissible and applicable, and does the native model have a correct consumer? A positive answer to one does not establish the others.

## 1. What exists on master

| Layer | Observed implementation | Main limits |
|---|---|---|
| Versioned defaults | Latest generic pack 2026-10-25 has 308 parameter rows and 96 technology/variant entries, including 81 campus electrical equipment variants. Hash-pinned versions, typed capacity basis, money year and connection-cost scope. | These are parameter rows/variants, not 96 independently calibrated technologies. 68 rows carry an illustrative flag. |
| Regional cost overlays | ca_federal/CAD, uk/GBP and us_federal/USD, latest overlays 2026-10-22 based on the 2026-10-22 EU pack. | Each exposes only 10 cost-value rows; each declares 90 unpriced monetary keys. Missing prices correctly refuse rather than silently borrowing EUR values. |
| European cost references | 24 explicit technology/year snapshots across onshore wind, utility PV, commercial rooftop PV, AEC and PEM. Native CostPart/PackValue, source cells and file hashes. | Danish representative benchmarks, not a complete local cost forecast or site quote; exact exclusions survive comparison. No interpolation. |
| Reference user workflow | Read-only listing/detail/preview, separate confirmed native apply, asset-form “Compare with references,” drawer, receipt and chat handler. | Some older EU research documents still say UI/chat apply is future work. Source/implemented-code comparison establishes that statement is stale. |
| Org Library | Content-versioned numeric series; typed tariff, contract and connection-agreement items; org ACLs, project pins and bundle handling. | Equipment, process recipes, lifecycle laws and control presets are not generic editable Library item kinds yet. Native metadata/standard types are not a vendor product database. |
| Commercial support | URDB import with supported-term mapping/refusal or explicit partial state; native PPA/CfD/DR/lease/EaaS/retail contract payloads. | Generic contract schemas are not populated market data. Live PPA offers, arbitrary swaps, forwards and all regional tariff clauses are not established as supported data/products. |
| Shipped tariffs and profiles | Two illustrative defaults-pack tariffs, two common TenneT 2026 HS/EHS tariff records, two synthetic office/industrial load shapes. | Both TenneT records omit weighted monthly peak charges; tests require an incomplete bill and total=None. The two generic load shapes are not measured site demand. This count does not include every template or weather-generated profile. |
| Electrical equipment | 81 variants: 29 transformers, 12 cables, 9 capacitors, 9 reactors, 9 STATCOMs and 13 switchgear entries. Cited cost subsets coexist with assumptions. | The detailed gaps below affect engineering inputs as well as economics. |
| Reliability/lifecycle | Native per-asset overrides and generation-class outage defaults; explicit FOR/EFORd/MTTR semantics; planning lifetimes/replacements. | Outage defaults include approximate class benchmarks, not campus equipment field distributions. Generic finance defaults explicitly say degradation is not used in that MVP. Do not infer absence of every storage analysis feature from that one finance setting. |

Evidence: native-inventory.json, the inspected source tree and library-tests.xml in this directory. The underlying modules are `services/library/{defaults_pack,reference_catalogue,europe_reference,reference_apply,items,series_store,common,urdb}`, `routers/{library,library_references,network_library_reference}`, and frontend `layout/properties/AssetFields.tsx` / `ReferenceApplyDrawer`.

## 2. Concrete gaps requiring attention first

### 2.1 Corrected pack versus actual project/template pins

The 2026-10-25 pack relabels technology-data v0.14.0 monetary values to their 2025 money year. It does not inflate or otherwise change the numbers. However, template and ammonia paths still pin 2026-10-22 with 2020 case-year labels; the guided-study ledger pins 2026-10-05. Regional overlays also inherit the older base. The existing `.scratch/money-year/issues/01-templates-case-year-and-repin.md` explicitly records the unfinished coordinated migration.

This is a basis/provenance consistency issue, not a newly demonstrated numerical-result regression. Bumping a version alone can trigger the existing mixed-money-year refusal. The fix must reconcile template/study case years, tariffs, CO2 assumptions and regional bases together, while preserving old project pins. Do not “repair” historical snapshots in place.

### 2.2 Equipment values with the clearest research payoff

From `gridspine/templates/data/campus_assets.yaml`:

| Category | Entries | Assumed CapEx | Other important assumptions |
|---|---:|---:|---|
| Transformers | 29 | 5 | All 29 short-circuit impedance values are marked assumed; losses and other technical values also retain assumptions. |
| Cables | 12 | 1 | All 12 resistance entries are marked assumed. Need exact installation/temperature/current and impedance basis. |
| Capacitor banks | 9 | 0 | All 9 lifetimes are assumed. |
| Shunt reactors | 9 | 9 | All 9 lifetimes are assumed. |
| STATCOMs | 9 | 9 | All 9 O&M and lifetime entries are also assumed. |
| Switchgear | 13 | 8 | Sourced monetary rows do not establish a complete manufacturer/type-test catalog. |

Thus **32 of 81 equipment entries have assumed CapEx**. A sourced price does not automatically validate technical parameters. The existing campus-library gap issue also identifies a missing 220/110 kV transformer option for the hydrogen-hub investment path; confirm scope and add a cited compatible unit or retain an explicit unresolved result.

### 2.3 Data-model and provenance gaps

- `SeriesMeta` currently permits source/provider/vintage/unit/settlement/notes, not a full typed market-product, source-revision, currency and interval-label manifest. Series canonical hashing includes timestamps, values and timezone; it does not establish all economic interpretation metadata. A numeric pin alone must not be advertised as verification of currency/product semantics.
- Tariff periods use integer start/end hours although settlement can be 15/30/60 minutes. These are different concepts: a tariff whose band begins at 00:30 cannot be faithfully represented just by selecting 30-minute settlement.
- Item storage has a closed kind/schema set. A document promising a universal equipment/process/control Library needs new reviewed typed adapters/kinds; collecting CSVs does not implement that promise.
- Source clearances are resource-specific and generally retain automatic_import_approved=false. Existing permissions do not automatically cover revised editions, third-party workbook sheets or commercial redistribution.
- Public outage indexes, historical PPA benchmarks, synthetic loads, OEM nameplate values and private project measurements require different applicability labels; a single undifferentiated “verified” badge would hide material differences.

## 3. Open PR/spec reconciliation

| PR / exact inspected head | What it contributes | Remaining gap |
|---|---|---|
| #491 / c35bfd0b7a218a343c9b5febc8f1b69aa26f0836 | Publication of the five-card integration/sector plan and 80-family/114-source research coverage. | Documentation/research only. Those counts are not integrated datasets or import approvals. Rebaseline the original native mappings against current code before implementation. |
| #524 / 3a99ed44f6867a48d7f6a0251a7ffacd37a4885f | NAM requirements/source reconciliation and six explicit collections: tariffs, wholesale, PPA benchmarks, reported contracts, finance references and O&M/technical references. | Still a proposed six-collection implementation backlog. Start with NAM-0 current-master audit, then one complete tariff, one market product and one scoped benchmark. Refresh source releases and price bases. |
| #704 / d7dea035757ea8ba14c2861a0eb4f4f920a95f75 | Template-assumption report appendix. | Improves disclosure; does not add source data, convert money years or validate parameters. |
| #694 / 101e8745ca44eb45b20a8ebb1a1a076cf5edb2b8 | Renewable-profile tuning/compare backend. | Helps inspect/use profiles; does not create a regional equipment, tariff or lifecycle dataset. |
| #712 / c117493f494a74d7d19d9a053286cb7f347730e9 | Optional manual equipment envelopes/proxies, including truthful library fallback. | Open/unmerged at inspection. No sourced OEM footprint collection or universal capacity-to-footprint law. |
| #711 / bd26ea2cb8bfe06b9b302d4bf9853756a50432a9 | Campus/scene/binding/AI workflow specifications and bounded implementation handoff. | Specification only; exact source/resource/identity contracts remain prototype gates. It does not populate a 3D library. |
| #527 / 5b2634355c218828e6f54fd6dd326987a8d38331 | Native rapid-load/reserve workflow proposal; specialist engines deferred. | Measured workload/event/controller evidence and supported event adapters remain necessary; no justification for importing arbitrary dynamics presets as operational settings. |

The EU research overview, HANDOFF and integration matrix on master also need a status reconciliation: reference lookup/apply and the two TenneT common items now exist even though original package documents call them future work. Update those statements without deleting their historical validation context. Do not duplicate already-shipped reference/apply infrastructure.

## 4. Prioritized research work packages

### R0 — source-to-consumer register and version/basis audit (first)

Produce one current register linking each existing source/version to parameter keys, units, native consumers, scope, hashes, rights and actual approval/application status. Reconcile #491/#524 identifiers rather than summing overlapping catalogs. Close the money-year migration decision and documentation drift before adding more defaults. Record a source freshness policy and review available updates without silently repinning projects.

Acceptance: known old project loads unchanged; new template/study uses a consistent reviewed basis; stale source/unknown year is visible; each advertised capability has current code and tests. This is largely reconciliation/implementation work, not another broad literature survey.

### R1 — electrical equipment plus dimensional reference subset (highest-value new data)

Target the assumed transformer/cable/STATCOM/switchgear fields above, especially the missing H2-hub voltage pair. Research exact OEM model/revision datasheets and test records, rated voltage/frequency, MVA/Mvar, impedance/per-unit base, losses at stated load/temperature, cable conductor/installation/thermal basis and footprint dimensions. Keep body dimensions, service/keep-out space and installation land separate. Price quote/EPC scope remains distinct from a physical nameplate.

Public candidate families: California Energy Commission equipment lists for PV/inverter/ESS model identity/performance; DOE CCMS for relevant distribution-transformer efficiency records; exact OEM dimensional/electrical datasheets for model-specific gaps. CEC and DOE explicitly qualify manufacturer-reported information. Neither source by itself establishes project compliance, full fault/control models or all campus voltage classes.

First deliverable: a small model-identified subset covering the most-used battery/inverter/genset/transformer classes in EU and NAM, plus explicit unresolved STATCOM prices where public data is inadequate. Choose sizes only after checking the actual template asset demand. Native applicability and rights must be recorded per field. A 1 MW value may scale some planning area assumptions; it must not linearly stretch every equipment dimension. Use counted units or explicit user dimensions when appropriate.

### R2 — BESS and hydrogen lifecycle/cost boundaries

Research power-versus-energy costs, duration/chemistry/scale, efficiency boundary, calendar and cycle life conditions, augmentation/stack replacement, fixed/variable maintenance and end-of-life assumptions. For H2 include AEC/PEM balance of plant, electrical input versus H2 output, LHV/HHV, water/cooling/compression pressure and plant versus stack life. Extend existing versioned packs/reference readers where the source actually matches a native field.

Start with the exact DEA storage/fuels/industrial-heat releases and PNNL's v2024 database/methodology. PNNL's landing page explicitly notes that hydrogen/gravity capital estimates retain an earlier vintage. DOE H2A supports input taxonomy/reference cases, not an automatic current quote or replacement finance engine.

Cell-level ageing datasets are a separate later calibration stream. Oxford's eight-cell dataset is explicitly ODbL, not assumed CC BY; Battery Archive's complete CSV access may require a request. Neither establishes a site BESS failure law. No publisher contact occurred. Keep detailed degradation curves reference-only until a native consumer and independent validation exist.

First deliverable: one duration/chemistry-defined storage reference and one electrolyser component-boundary reference with independent cost/efficiency/lifetime/replacement oracles. Do not invent a power/energy cost split from an aggregate total.

### R3 — complete tariffs and settlement histories

For tariffs, map operator/customer class/voltage/effective dates, taxes, contracted/peak demand, reactive/kVA, ratchets, holidays/TOU and export treatment. Preserve DSO-included TSO components and unsupported terms. Research exact missing TenneT weighted-peak rules if extending those items, but do not call the existing partial bill complete.

First tariff deliverables: one independently reproducible EU schedule and one US commercial/industrial schedule that the present engine can fully represent. Reuse URDB and official utility/regulator schedules; add supported Danish DataHub data only after exact rights/currency/validity handling is demonstrated. Then expand to selected Canadian provinces and verified Mexican resources from #524.

For prices, select a native-supported product such as German day-ahead or CAISO DAM rather than a generic “market price” dataset. Preserve node/zone, DA/RT, total/components, currency, interval-start/end, timezone and revision/finality. German day-ahead changed to 15-minute products on 1 October 2025; do not flatten the source to an assumed hourly calendar.

First price deliverable: one exact product/node/day then a DST transition week and a year, with independent price×energy oracles. Add a versioned semantic manifest alongside numerical series rather than pretending existing hashes prove every interpretation. Resolve absent native currency/product paths before monetary use.

### R4 — updated PPA and empirical renewable-cost benchmarks

Refresh #524's Berkeley Lab solar research against the newly published **2026 edition**. The publisher reports a new public workbook and separate plant-level generation/value artifacts. This assessment verified the publication, not the new workbook cells or blanket rights; the OEDI resource fetch returned429 and was not bypassed. Record access failures and fetch a bounded official sample later.

Compare sheet names, execution versus COD vintage, region, PV/hybrid boundary, AC/DC, CapEx/O&M inclusion, money year, levelized versus first-year price and escalation/discount basis before creating a new benchmark version. Preserve earlier snapshots and handle third-party price-index sheets separately. A historical executed PPA benchmark, CfD auction strike and current customer offer remain distinct.

First deliverable: a small attributable 2026 benchmark subset with exact worksheet cells and independent comparability checks, displayed read-only through the current reference pattern. No automatic signed-contract strike, swap or forward-curve admission.

### R5 — reliability, process and representative demand data

Separate supply quality (EIA-861/OEB/Enedis service indexes) from fleet outage statistics and component fail-on-demand/repair observations. Research exact cohort, exposure, event definition, planned/forced separation, censoring, climate/age and common-cause assumptions. SAIDI/SAIFI do not supply UPS/genset/BESS FOR and MTTR. Retain VRE outage-inclusive profile safeguards.

For C&I demand, a bounded ComStock 15-minute, end-use/climate subset can improve on two generic shapes. Its profiles are simulations informed by calibration, not customer meter records. Avoid bulk downloads of national datasets. For mining, H2, NH3 and DCs, prioritize process boundary, product units, specific consumption, minimum-load/start-up constraints and measured customer/vendor evidence where public values are not transferable.

Control and rapid-load data must retain sample rate, model version, operating point and approved applicability. The existing generic series store deliberately refuses sub-second timestamps; milliseconds traces require a separately validated event adapter, not silent rounding or a new default engine. Follow #527's native workflow placement and specialist-engine deferrals.

First deliverable: a small supply-quality reference subset plus one representative C&I profile; component failure/control presets remain unfilled until suitable evidence exists. Reliability and controls are particularly poor candidates for an unqualified universal public-default database.

## 5. Source-admission record and validation

For every new record preserve: source ID and exact resource/version/hash; publication/retrieval/effective/projection dates; licence and permitted scope; original row/cell/value/unit; transform and independent expected value; currency/money year/real-versus-nominal and capacity/energy basis; geography/technology/size/test/operating conditions; included/excluded costs; observed/manufacturer-reported/benchmark/synthetic/assumed applicability; limitations/uncertainty; exact native target and consumer; and accepting review evidence.

Keep numerical and semantic provenance versioned together while retaining old numerical pins. An assumed range is not a statistically validated P10/P90 interval. A missing datum stays unavailable rather than becoming zero or a made-up scale factor. Customer quotes, meters and approved control settings remain organization/project scoped.

Minimum acceptance by collection:

| Collection | Independent checks before use |
|---|---|
| Costs | Currency/base/projection year, AC/DC/input/output basis, power/energy split, FOM absolute-versus-share, no EPC/connection double count, old pin unchanged. |
| Equipment/footprint | Exact model/revision/ratings/frequency, unit/per-unit conversion, dimension envelope versus clearance, unsupported scale refusal, physical proxy/packing agreement. |
| Tariff | Eligibility/effective version, full selected bill arithmetic, demand interval versus TOU bands, DST/holiday/ratchet handling, unsupported-clause refusal/explicit partial total. |
| Market/PPA | Product/node/currency/revision/time semantics, negative/missing/duplicate intervals, first-year versus levelized and benchmark-versus-contract distinctions, independent cashflow oracle. |
| Lifecycle/reliability | Calendar versus operating/cycle exposure, chemistry/cohort, replacement reset, planned/forced basis, no profile-outage double count, no service-index-to-equipment-rate substitution. |
| Shared workflow | Source → review → preview → confirmed native use → pinned evidence; no silent source update; org isolation/export rights; stale-result behavior and truthful assistant explanations. |

## 6. Current validation and next implementation sequence

On inspected master, the selected existing Library suites completed **379 passed** (see library-tests.xml/log): defaults/typed/regional/money-year/campus-reference, European reader and reference apply, common tariffs, series/items/bundle pins and settlement references. This supports the existing contracts; it does not certify every source value, every UI journey, engineering suitability or prospective importers. No new tests or runtime source connector were implemented.

Recommended implementation sequence: R0 reconciliation and basis migration; R1 small equipment/reference subset; independent R2 lifecycle and R3 tariff/market pilots; R4 source-edition refresh; R5 carefully scoped reliability/profile work. Ship each bounded collection separately through existing Library/reference/apply paths. Extend typed kinds only where a concrete consumer is demonstrated. Avoid a new database, automatic live scraping, extra user modes or wholesale engine replacement.

Research outputs should be source-to-field records, small reproducible samples, parser/refusal cases and numerical oracles—not another long unranked source list. Actual rights/field/resource checks and independent domain/customer calibration remain prerequisites for stronger claims.

## Primary web sources checked for this assessment

- DEA technology catalogue categories: https://ens.dk/technologydata
- Berkeley Lab 2026 solar publication: https://emp.lbl.gov/publications/us-utility-scale-solar-2026-edition
- PNNL cost/performance database and releases: https://www.pnnl.gov/projects/esgc-cost-performance and https://www.pnnl.gov/projects/esgc-cost-performance/download-reports
- California Energy Commission equipment lists and disclaimers: https://www.energy.ca.gov/programs-and-topics/programs/solar-equipment-lists
- DOE compliance certification database: https://www.regulations.doe.gov/certification-data/
- DOE H2A input/model scope: https://www.hydrogen.energy.gov/program-areas/systems-analysis/h2a-analysis
- EIA outage/service-metric scope: https://www.eia.gov/tools/FAQs/faq.php?id=1194&t=6
- SMARD actual 15-minute transition: https://www.smard.de/en/15-minute-wholesale-prices-available-218078
- Building end-use profile scope: https://www.nlr.gov/buildings/end-use-load-profiles
- Oxford cycling-data scope/licence: https://ora.ox.ac.uk/objects/uuid%3A03ba4b01-cfed-46d3-9b1a-7d4a7bdf6fac
- Battery Archive access boundary: https://www.batteryarchive.org/

No emails, reviewer contact, paid data/model/media call, bulk dataset download, ingestion, deployment or PR/source-branch modification was performed for this assessment. Primary pages were checked; failed access is not represented as verified data. Research PR and source counts overlap and are never summed as data coverage.
