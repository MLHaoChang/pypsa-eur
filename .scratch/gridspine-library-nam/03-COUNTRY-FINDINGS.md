# Country findings: tariffs, markets and data access

See [source catalogue](02-SOURCE-CATALOGUE.md) for exact URLs and evidence IDs. Source register entries separate actual sample inspection from catalogue/documentation discovery. Prices are not copied into a common default pack in this research.

## United States

### Utility tariffs

**URDB is the strongest first adapter to reuse**, because `services/library/urdb.py` already exists. The official portal covers residential, commercial and industrial discovery and states CC0 unless otherwise noted. The API schema describes utility/EIA identifiers, effective dates, service applicability, energy tiers, demand structures, schedules, fixed charges and ratchets. API documentation was inspected; API authentication and bulk download were not completed. A failed bulk endpoint does not prove there is no bulk service.

Two actual record pages matter. The first discovered record was 2012 residential, so it was rejected as evidence of current industrial coverage. The second is SDG&E `TOU-A-3 Primary (Above 50kW)`, effective 1 June 2026, with approval history and a reactive-power charge. Its apparent voltage-category metadata differs from the rate name, so it needs authoritative utility verification. It is a useful **refusal/eligibility test**, not an automatically admissible data-centre tariff. Blank structures, long repeated schedule renderings and an “expert verified” badge cannot substitute for field-by-field source validation.

**The native importer is deliberately narrower than the database.** It rejects minimum-bill and coincident-peak fields, non-kW demand and sell tiers. It supports specific demand windows and ratchet forms. Missing demand-window data results in an explicit 15-minute assumption in current code; any pilot should require the source's actual interval and adequate load resolution. An hourly mean cannot reproduce a 15-minute demand bill.

Primary utility schedules provide the bill oracle:

- **PG&E B-20:** actual PDF retrieved. Select the effective sheets and service voltage, reconcile supply/delivery and all riders, and enumerate minimum/standby/export conditions. Do not equate URDB rate components with a complete invoice.
- **Dominion Virginia GS-3/GS-4:** actual PDFs retrieved. GS-4 says effective usage 1 January 2026 and includes different distribution demand blocks, on/off-peak supply, reactive `rkVA` charges and term commitments. This supports a large-site pilot's clause inventory, but it is not evidence of a universal special data-centre tariff.
- **SCE:** official tariff book retrieved, but no particular current industrial schedule was admitted. The failed Con Edison URL stays a failed observation; no claim of a verified Con Edison tariff.
- **BPA:** useful federal wholesale rate/proceeding references for eligible products and counterparties. It is not a price quote for an arbitrary industrial renewable PPA.

**EIA retail averages are context.** The sales/revenue/price XLSX and monthly sector-price page were retrieved. Use state/sector/year averages to flag implausible assumptions, while retaining average-versus-marginal and delivered-versus-component distinctions. They cannot supply TOU schedules, demand billing, voltage eligibility or a negotiated retail contract. An attempted PDF URL redirected to a generic HTML page; that attempt is recorded as a semantic failure, not successful table extraction.

### Wholesale market series

| Operator | What was actually inspected | Parser and admission consequence |
| --- | --- | --- |
| CAISO | One DAM `PRC_LMP` ZIP for `TH_NP15_GEN-APND`, operating 20 October 2026; 120 records across 24 hours and five component labels | UTC-aware interval starts/ends. Value column is `MW` in this price query. Keep total LMP separate from MCE/MCC/MCL/MGHG. Validate query identity and reject XML-error payloads masquerading as ZIP/data. |
| NYISO | DA zonal LBMP CSV,20 October 2026; 360 data rows, 15 locations, 24 hours | Timestamp has no offset. Preserve zone name and PTID, correct operator DST convention and source-specific congestion sign. Do not assume a universal component-addition formula. |
| MISO | DA ex-post CSV,20 October 2026; 7,884 component rows, 24 hour-ending values per row | Preamble explicitly states **Eastern Standard Time**. LMP/MCC/MLC each have 2,628 rows. Use fixed EST, not a DST-varying New York clock. Do not fabricate a missing energy column. |
| ERCOT | Current DAM/RT settlement-point product pages | No actual price file parsed here. Settlement-point price differs from arbitrary node LMP; interval, product, revisions, adders and history/access need actual validation. |
| PJM | Data Miner page and current distribution policy | No numerical sample accessed. Policy states internal use; redistribution of raw or derived information requires active PJM membership, minimum Associate. Verify deployment entitlement before choosing a data path. |
| ISO-NE | Official web-service documentation | Account/authentication and exact product terms remain gates; no authenticated sample tested. |
| SPP | Public portal returned a 609-byte JS shell | This is discovery only. No fresh product/schema/price coverage claim. |

The app currently names ERCOT/PJM/CAISO among its NAM `MarketRegion` enum values. NYISO/MISO CSV storage is not proof those market products are executable. Begin with one admitted region/product, then extend the enum only where an engine behavior actually needs it and has product-specific tests. A `gridstatus` adapter may save parsing effort later; its software licence does not clear the upstream data.

**EIA-hosted ICE hub prices:** the actual 2026 workbook was downloaded and the official page describes daily weighted-average/high/low/volume data. This is different from hourly nodal settlement or a forward curve. The page explicitly states ICE permission. EIA's own public-domain policy must not be applied blindly to third-party ICE content.

## Canada

### Ontario: prices and the bill must be separated

IESO's actual renewed-market sources were inspected:

- DA nodal CSV: 25,632 records across 1,068 locations, delivery 21 October 2026. Its preamble says **created 20 October for 21 October**. Use delivery date and delivery hour, not creation or retrieval date.
- DA Ontario zonal XML: delivery 20 October 2026, 24 hours, revision/schema metadata and `DSO-RD` flags. This contains capped zonal price/loss/congestion fields, not an interchangeable node LMP.
- The zonal help PDF describes its hourly product and components. A guessed nodal help PDF returned 404; that gap is retained. Resolve the relevant market time convention and CAD units from authoritative rules before admission.

The directory distinguishes legacy HOEP reports through May 2025 from renewed-market reports. Do not concatenate these into an unlabeled Ontario price series. Preserve product/regime and revision flags in both the Library and report citations.

**Global Adjustment:** Class A/ICI depends on contribution during the top five Ontario system-peak hours in a base period, allocating monthly GA costs in a subsequent adjustment period. That is not monthly site-maximum demand. Class B uses a different energy-based treatment. A public spot series alone cannot produce an industrial delivered bill. Required data includes class eligibility, base/adjustment periods, customer coincident demand, system peaks, monthly GA costs/rates and revision status. Exact financial exposure may need customer facts.

OEB pages failed 503; a generic Ontario catalogue search returned irrelevant results. No licence or tariff was inferred from them. Hydro One's official page was retrieved and helps distinguish wholesale supply, GA and delivery, but its prominent RPP/TOU/ULO values do not establish an industrial/hyperscale rate. Select the approved distributor/customer-class schedule and all applicable riders before billing.

IESO's Terms of Use grant a limited, non-exclusive, non-sublicensable, non-transferable use/reproduction licence with required notices, subject to supplemental terms. This is not the same wording as AESO's noncommercial restriction. Determine intended hosting, display, caching and customer export rights for the actual reports rather than reducing all Canadian sources to one rule.

### Alberta, BC and Québec

**AESO:** API documentation and the Demand Transmission Service PDF effective 1 January 2026 were retrieved. Pool energy, transmission, distribution, retail fees and other charges are separate inputs. No pool-price response was parsed. AESO's site terms permit specified personal/noncommercial/educational use and require written permission for other use; do not seed a common commercial feed based only on public access.

**BC Hydro:** full tariff PDF retrieved and hashed. The current contents list transmission schedule 1830 alongside TOU and critical-peak options, standby/maintenance supply, IPP station service and industrial supplements. Other sections retain legacy 1823 references. That is precisely why an older two-tier example must not be promoted to a current tariff without schedule/effective-sheet selection. kVA charging and bespoke mining/industrial supplemental agreements are material. The site policy is restrictive for content/graphics and requires case-by-case permission; assess the exact document and intended use before common distribution.

**Hydro-Québec:** business-rate and book requests returned 403. No current Rate L numerical data was extracted. Preserve this as an important industrial/mining coverage gap and use an authorized current source document when accessible; do not approximate with an Ontario or national average.

**PPA procurement:** IESO long-term procurement, BC calls for power, EPA renewal rules and BRC Canada's deal tracker provide useful product/deal context. The selected BC PDF is **renewal-program rules**, not a complete executed PPA. Public deal MW and counterparties do not disclose strike, shape or credit terms. No comprehensive Canadian corporate PPA price tape was established.

## Mexico

**CFE industrial tariffs are required but unresolved in this pass.** GDMTH/GDMTO and alternate business/industry endpoints returned 503; official data/regulator catalogue attempts returned 403. No current numerical schedule was admitted. A future importer must preserve tariff class, service voltage, division, billing month, demand calculation, energy periods, capacity/distribution components, power-factor/reactive conditions and tax treatment. Legacy URLs containing `CRE` do not establish the current regulator or applicable 2026 rules.

**CENACE provides real public market reports.** The official price page records corrections/republications. A historical original SIN MDA ZIP for 23 March 2024 was retrieved and parsed. It contains **two versions** of the nodal PML file, each with 57,936 rows over 2,414 nodes. Spanish metadata occupies seven lines before the CSV header. It also contains distributed-node and ancillary reports. Blind concatenation would double count the operating day and mix products. A corrected/final source selection policy must precede history import.

The PML manual documents total, energy, loss and congestion components and a REST service on a nonstandard port. The manual was inspected; the service was **not invoked**, and no network-policy workaround was attempted. The sample proves historical file structure only. Establish MXN/MWh, operating-hour conventions for SIN/BCA/BCS, regional DST rules, node catalogue changes and latest corrected versions before enabling settlement. The manual's general-public service statement is not a complete commercial cache/redistribution clearance.

**Long-term auctions:**2017 archive pages are reachable, but the selected actual award ZIP exceeded the 15 MB research retrieval cap. An earlier candidate link was buyer-registration evaluation, not the award; it is not counted as an inspected price dataset. Energy, capacity and CEL auction bundles are historical products, not a current private corporate PPA quote.

**Macro:** Banxico's API root returned an empty object and guessed documentation URLs failed. No FIX USD/MXN or interest-rate series was validated. INEGI's INPC and terms pages were retrieved, but no numerical series was parsed. Keep those explicit gaps rather than fabricate a Mexico finance pack or relabel USD costs as MXN.
