# Independent cross-source review of the parameter-library research

Review date: 2026-10-21 (Europe/Berlin). Reviewer: a separate AI research agent, reviewing the other source-research sessions and root synthesis. This is a research/design assessment, not a legal opinion, approved data ingestion, numerical validation or independent human engineering review.

## Review status

**GO for the research/design package and staged source-specific pilots, subject to the gates below.** The initial109 assessments, five supplemental references and complete80-family source-coverage register have now been inspected. The identified rights-classification and source-role issues were corrected. This is not approval to ingest, redistribute or trust all114 sources; it is a recommendation to proceed with bounded per-source feasibility and implementation contracts.

## What was inspected

- All initial six topic JSON registers: costs/lifecycle18, European commercial22, North American commercial19, profiles/macro/emissions14, reliability18 and technical18.
- Their six topic Markdown notes/indexes, the root README and INTEGRATION-PLAN.md.
- Final combined `source-catalog.json`: **114 unique source IDs**, comprising the initial109 plus five legal/method references. Its record content matches every topic JSON record (apart from the added originating-file field). These are assessments of sources/products, not114 approved or independently tested databases.
- `research/additional-reference-sources.json`: ACER VoLL/reliability-standard methodology, LBNL ICE interruption-cost methodology, IRS depreciation/credit guidance and Canada clean-economy investment-credit guidance. They remain dated method/legal references, not actual project values or inferred eligibility.
- `source-coverage.json` and SOURCE-COVERAGE.md: **80 unique family IDs**, complete against the inventory key set. Coverage is explicitly28 partial candidates,23 benchmark-only,14 method-reference-only and15 public-data gaps. Every linked source ID/URL/geography/rights status was checked against the catalog; all `automatic_import_approved` values are false.
- Existing parameter inventory was used only as a mapping key and scope reference. Its author is this reviewer; this cross-review does not independently certify that inventory.

No new web fetch, package/model execution, connector operation, paid call, source-owner contact or data purchase was performed for this cross-review. Findings are based on saved research records, linked evidence descriptions and source-read limitations. I did not re-open every linked primary publication or inspect every workbook/data file. Some research sessions report access failures; these remain unresolved, not bypassed.

## Findings and required source-specific gates

| ID | Assessment | Required treatment |
| --- | --- | --- |
| CR01 | Initial catalog correctly separates public access from commercial use, cache, display/export and common redistribution. Software licensing is not assumed to license all underlying data. | Preserve per-resource rights/obligations and descendant restrictions. `open_verified` is a research finding for described scope, not automatic import/common-seed permission. |
| CR02 | Eurostat initially appeared as EU-C19 (`mixed`) and MAC-EUROSTAT (`open_verified`), with overlapping non-household-price coverage and country/third-party exceptions. | **Corrected:** both topic and combined catalog now use `mixed`; coverage explicitly preserves conservative exact-table/geography/terms review. A duplicate source ID must not provide a route around applicable restrictions. |
| CR03 | PJM, AESO, AHRI, NESO Grid Code and the restricted Enedis service catalog are not open common-library seeds. User-scoped retrieval is not an exemption from a commercial-use restriction. | Keep these reference-only or unavailable for that intended use until the actual required permission exists. Do not transform restricted raw data to evade derived-data restrictions. |
| CR04 | NERC GADS class metrics, outage notices, component reliability and supply-quality indices are distinct. Initial reliability notes preserve the distinction and native FOR/EFORd/outage-profile rules. | No SAIDI→component MTTR, SAIFI→generator failure law, event-count→rate without exposure, maintenance interval→random failure frequency, or capacity factor→forced-outage probability. Retain reporting thresholds, censored/revised events, denominators and uncertainty. |
| CR05 | Tariffs and price observations are well differentiated in the initial records, including regional and settlement changes. | Do not substitute EIA/Eurostat average prices for tariff schedules, auction strikes/support premiums for private PPA/H2 prices, old HOEP for new Ontario prices, or an hourly dispatch for a proven15-minute demand peak. Preserve unsupported native tariff fields and incomplete-bill status. |
| CR06 | Technical catalog coverage is useful but incomplete, and software standard types can be region/frequency specific. | Preserve50/60Hz admission, test conditions, named-type versus explicit-parameter exclusivity, fitted/declared/certified distinctions and per-model applicability. Efficiency listing is not proof of outage, protection, GFM or dynamic behavior. |
| CR07 | Cost/lifecycle references state meaningful boundaries and major gaps. | Preserve original money year versus model/projection year, currency/real-nominal/LHV-HHV basis, power-energy cost split and included EPC/connection/insurance/tax. Do not annualize twice, add a system quote to its own components, or convert cell fade into pack power/availability/warranty. |
| CR08 | Public data is not uniformly available across Europe and North America. The initial records do not incorrectly claim uniform coverage. | Maintain UK/GB, EU/EEA/Nordic/Swiss and country/operator distinctions; US, Canadian province and Mexico sources separately. FX conversion is not local engineering-cost calibration. Keep Mexico technical catalog/COPAR and exact CFE acquisition gaps visible. |
| CR09 | Exact importer/model readiness remains unproven for most sources, appropriately disclosed in topic notes and root plan. | A linked page, data dictionary or successful small endpoint request does not certify a complete source adapter. Before availability: exact release/resource/rights, bounded parse, units/calendar/revision fixtures, native round-trip and source-specific numerical/semantic checks. |
| CR10 | First coverage draft used broad `candidate_subset` roles for some context/reference sources. | **Corrected:** EIA retail/fuel averages are context, not a named wholesale endpoint; WECC structures are method references for converter limits; DEA life/cycle assumptions are benchmarks for ageing, not calibrated fade laws; ERA5 is weather context for thermal demand; SimBench/SMART-DS links use benchmark-fixture roles. Family-level partial coverage never means every linked source supplies every family field. |

## Regional coverage assessment

| Region | Credible research/pilot entry points | Limits that must survive prioritization |
| --- | --- | --- |
| Continental Europe | Existing technology-data lineage/DEA, SMARD/Energinet, selected official DSO/TSO tariffs, Enedis benchmarks, ERAA assumptions and ENTSO-E event candidates | DEA is representative, not every country's vendor cost. TSO charges may already be included in DSO terms. Event reuse is dataset-specific. No complete open industrial component-maintenance/health library established. |
| UK/GB | DESNZ assumptions, Elexon candidate datasets, NESO charging data, public CfD awards | GB system/code is not all UK electrical jurisdiction. Open charging data does not license the Grid Code. Award/reference price is not a current corporate quote. |
| Nordics/Switzerland | Energinet, Statnett, Nordic/Baltic disturbance reports, Swissgrid/ElCom references | Exact operator/voltage/role/effective year and currency; not one generic Nordic/Swiss bill or outage law. |
| United States | EIA benchmarks, existing URDB importer candidate, public official utility schedules, selected technical/cost/profile references | URDB rights/resource freshness and complete bill eligibility still checked. ISO rights vary; PJM is restricted. Lab/agency hosting does not clear all contributed data. |
| Canada | CER regional cost data, OEB distribution/reliability records, Statistics Canada, provincial tariff references | Ontario is not all Canada; distribution rates are not total delivered bills. IESO and AESO rights differ. Province/model/basis and exact account eligibility required. |
| Mexico | CFE/CENACE official candidates, INEGI/Banxico references, public distribution report and COPAR discovery lead | Structured cost/catalog coverage and automation/terms are incomplete. Exact designated open CENACE report scope matters. No silent replacement with US rates or equipment defaults. |

## Data gaps and honesty assessment

The initial research correctly leaves these gaps visible: complete public component outage/repair/maintenance data for commercial UPS/BESS/processes; actual customer CMMS and asset condition; current negotiated PPA, forward commodity and delivered H2/NH3 prices; vendor-specific ageing/warranty/control/dynamic models; full site/EPC cost; and safety/black-start/protection settings. A benchmark may suggest an explicitly assumed range, not fill those missing facts as verified inputs.

Ingesting a source must not imply engineering domain approval, actual customer eligibility, verified green-fuel status, accredited capacity or a checked chatbot result. These remain separate contracts with their own evidence and limitations.

## Final coverage and verdict

The final join is coherent at research-family level. It carefully leaves gaps for actual site maintenance/condition/criticality, OEM failure/control/ageing data and current confidential commercial prices. Nuclear component references are not admitted campus priors; requirements/model structures are not validated GFM settings; public tariffs are not signed connection offers; hydrogen support premiums are not offtake prices. Canada's province-specific and Mexico's incomplete technical/cost data coverage remain visible.

No blocking research-level source-semantic or blanket-rights finding remains after the corrections. The staged priorities are reasonable: native provenance/kind/schema work first; regional costs and source-backed equipment; dated tariffs/historical prices; properly scoped reliability benchmarks; only then event/maintenance/degradation/control depth with its own admission.

**Still required before any source becomes available:** exact resource/release and row/native-field mapping; actual permitted retrieval/commercial/cache/display/export/common scope; source attribution/obligations and derived-data propagation; small real parse/round-trip and semantic/numerical fixtures; unsupported-field refusal and unit/time/money/exposure applicability; source-update/local-override/tenant tests; and a justified engineering applicability/calibration decision. A successful pilot covers its tested subset only. Domain/human review and real-use calibration remain separate.

The C09 auction-reference family includes both electricity CfD awards and hydrogen support awards. Preserve their different contract/unit semantics: a EUR/kg hydrogen support premium cannot directly populate a native currency/MWh electricity `CfdContract.strike`. Any conversion or product-contract representation needs a separately justified scope and binding.

The review is bound to the exact source/coverage/plan artifact hashes recorded below. It does not certify later edits or the correctness of unpublished raw datasets. No full-catalog ingestion/common-redistribution approval is given.

### Reviewed artifact hashes

The author froze these19 source/coverage/plan/publication artifacts for final review. Their SHA-256 hashes bind this verdict to these bytes; later material changes require reassessment.

| Artifact | SHA-256 |
| --- | --- |
| source-catalog.json | `188a3498f2d7e3f050de5ce4bf4dd226296daadccfec3fb5b8cd9b4bce786328` |
| source-coverage.json | `ac90e22c4f0df1893817bbda4006091ba22dae64df2af28bff503ac9a8fe67dc` |
| SOURCE-COVERAGE.md | `65dbafb15204fa687a72d053edfcfc7e2e036463a917036038d5f4707c90b277` |
| RESEARCH-CONCLUSION.md | `4986bc37a5a30738a5796ee400168ddf9106b30fa8ed3f36bd58fbe54ad37248` |
| INTEGRATION-PLAN.md | `6b227fe76546faa904698c6f0417a76cd8dc13f3066c9ceb532f1e8eaf37666e` |
| README.md | `da47dfbc4a9cbe429c654a0c741dd874798b61b40b318c2141614c74b6232527` |
| research/additional-reference-sources.json | `a97bf448901418eea806db9a1711a5527ea4ef7b3535e207dbcd44f9e8e56b45` |
| research/cost-sources.json | `53cd9819018ad9d35df071c42afc49bf09b32c8c7cfca33e4cf68ef4115b1057` |
| research/europe-commercial-sources.json | `aab881ab6eee30e4542dcec77ba8142666230f09849303db824ea19bb53a32fe` |
| research/northamerica-commercial-sources.json | `14e7465a3fc7427075952cdb108356bcff5b36b4aee69eb6d6088f7c0d570bb2` |
| research/profile-macro-sources.json | `0c8343447e380b61f5112a6a4f423b26cf214ebdb4bf622f7c6570fe213935ba` |
| research/reliability-sources.json | `d4c86e4d184cecec0fd6f3a79fb165bef164ccc188873a8ae7c12e98b359e344` |
| research/technical-sources.json | `9c56c68e09248935b0ea72e6493a3f5c4e616d5406bc538aa99fe7420142e670` |
| research/COSTS-LIFECYCLE.md | `4a673eae742e950b05293683489fe3a29e950f873b0d096da1f3721f454b734c` |
| research/EUROPE-COMMERCIAL.md | `29aa3d1c8ce053f00be40fd590f1a3c1f73efbbdbe282b587e5c01e5b176556b` |
| research/NORTH-AMERICA-COMMERCIAL.md | `464c8e4a51cc9cda381897b0d1479582f80b2e60770844307c28768fc2c5317b` |
| research/OUTAGE-MAINTENANCE.md | `34a01f603bd62040ffb250affb5176477bd422eef18dea7cb90374fd9da6f177` |
| research/PROFILES-MACRO-EMISSIONS.md | `28b2b040e32f4046830b791a3ea42c9e289a96c9146db48026ebc52db50bb3ee` |
| research/TECHNICAL-COMPONENTS.md | `dfde267a90ee0fa1ee460b9c7e22a11ed5f1d0a0538db324bac489f9c2b0e7c2` |

The parameter inventory and JSON are consistency keys, not independently approved by this review because I authored them. Their family IDs/native mappings were used to test completeness of the source join; actual source-cell-to-field admission remains separate.

Final structural checks:114 unique source IDs; all seven topic registers match consolidated records;80 distinct coverage family IDs match the inventory; source IDs/URLs/geographies/rights are consistent across every join; all automatic-import flags false. Rights classifications:24 `open_verified`,66 `public_terms_review`,15 `mixed`,5 `restricted`,4 `unknown`. No new numerical or ingestion tests were run.
