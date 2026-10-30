# Contracts, technology costs and commercial comparability

The public library should improve the quality of assumptions without pretending that an observation is the user's negotiated contract. See the [register](source-register.json) for exact sources and the [48 requirement mappings](source-map.json) for native boundaries.

## Four different things called a PPA price

| Evidence | What it can support | What it cannot establish |
| --- | --- | --- |
| Historical executed-contract sample | Benchmark by execution vintage, technology, region, tenor and stated economic basis | Today's offer, undisclosed legal terms or the user's actual bill |
| First-year offered index | Offer-market context for its percentile, methodology and covered products | An executed trade, lifetime levelized price or every buyer's credit-specific quote |
| Reported transaction/contract filing | Observed reported unit price, quantity, product, timing and counterparties where disclosed | Complete corporate PPA coverage, full legal risk allocation or necessarily a renewable asset contract |
| Auction award/procurement result | Award facts for exact energy/capacity/certificate product and support mechanism | An unbundled current merchant PPA quote or guaranteed future revenue for another project |

Retain these dimensions in any benchmark reference: publisher/release/file hash, sheet/cell or row keys, execution date, delivery date if known, technology, location, profile, physical/financial form, tenor, volume/weight, currency, money year, nominal/real, first-year/levelized, discount rate, escalation, certificate/tax inclusion, sample limitations and source rights. Unknown must be distinct from zero, not-applicable and redacted.

### Berkeley Lab solar: actual workbook inspection

The 2025 Utility-Scale Solar workbook was retrieved from the official publication link, hashed and opened read-only with cached values. The OEDI record explicitly links CC BY 4.0. This is stronger primary licence evidence than a third-party description, while individual third-party contributions still require separate assessment.

Selected evidence:

- `PPA Price by Project (PV only)`, row 25: execution date, MW, term and region price columns. Notes in `A23` describe **full-term levelization**, escalation/time-of-delivery adjustments, conversion to **2023 dollars**, and a **4% real discount rate**. Missing regional cells include `#N/A`; these must not become zero-price contracts.
- `Annual Average PPA Price`: regional averages require at least two PPAs in a region/year, and generation weighting matters. Do not mix project counts, MW weights and generation weights.
- `LevelTen Solar PPA Prices` and `Trio Solar PPA Prices`: selected notes describe **25th-percentile, first-year, offered** nominal$/MWh prices. These are methodologically different from the executed real levelized sample and carry third-party source obligations.
- Hybrid PPA sheets need a separate price/product label: storage availability, tolling, capacity and dispatch terms cannot be inferred from a PV-only strike.

Recommended product: a **benchmark reference drawer** showing basis, source date, sample scope and range with a “use as an explicit assumption” action only after the native contract form is supported. A benchmark should never overwrite a signed contract automatically. Do not reverse a levelized price into a first-year strike without the full escalation, discount and delivery assumptions.

### Berkeley Lab wind: newer 2026 source

The official 2026 Land-Based Wind workbook was retrieved and inspected. `Wind PPAs by Plant` has execution date, capacity, term, region/macro-region and a **Real 2025$/MWh** price column. This supersedes a blanket recommendation to use only the 2025 update. It does not imply the newest contracts execute in 2026: observation dates must be read separately from release date.

The workbook filename says PUBLIC, which is not itself a commercial redistribution licence. Its REC-price sheet credits Marex Spectron, a third-party source. Preserve attribution/rights per sheet; do not bulk clear every tab because the workbook is government-hosted. The empirical wind PPA and cost/performance references are useful candidates after the exact intended-use review.

### FERC EQR: useful addition, with current-schema work remaining

The official EQR page, public report viewer, legacy dictionary and Order 917 page were inspected. The dictionary identifies contract execution/commencement/termination, term/class/product, rate units and point-of-delivery fields. Transaction records distinguish quantity, price, transmission and total charges. This makes EQR a good next source for **reported wholesale transactions and contract observations**.

It is not a universal PPA tape. Filter actual product and units; avoid counting energy, capacity, ancillary and transmission together. Do not divide total contract value by MW and call it$/MWh. Join contract and transaction keys with reporting quarter, filer and revision; preserve missing/redacted terms and flag aggregate/affiliated trades. Publication does not remove selection bias.

The inspected Order 917 page says certain reporting requirements ended 26 May 2026 and material billing adjustments should be handled through refiling. It explicitly says the implementation timeline for the new XBRL-CSV system is **not set**. Therefore the 2020 dictionary is a starting reference, not sufficient evidence of the current submission schema. A first EQR implementation must retrieve one current quarter, its current field specification and a corrected/refiled example before claiming coverage. No transaction quarter was downloaded in this research.

### Contract forms, swaps and forwards

EEI's model wholesale power agreement and optional provisions offer a useful **term taxonomy**: price, quantity, location, duration, credit and product definitions. A blank agreement contains no market price data and does not grant unrestricted rights to redistribute its legal text. SEC EDGAR may reveal selected executed agreements, but prices/clauses can be redacted and issuer-authored exhibits have separate rights. Neither should become a generic “legally complete PPA generator.”

CFTC's SDR directory and DTCC's public-dissemination dashboard are credible **research leads for financial swap reporting**. No actual electricity swap record/schema was parsed here. Validate asset taxonomy, jurisdiction, commodity/electricity identity, masked or capped notionals, amendments/cancellations and public-use terms before deciding it is useful. A reported financial swap is not a physical renewable supply contract or a complete forward curve.

CME's market-data overview does not establish a freely redistributable commercial forward dataset. Likewise, public LevelTen/Trio excerpts do not grant their private subscription history. Preserve an entitled/private curve import path, source visibility and tenant scope instead of building a misleading “free current PPA prices” feed.

**Native calculation limit:** `PpaContract` supports a particular financial fixed-price pay-as-produced form, not arbitrary swaps. Current financial validation refuses caps/floors and dispatch changes. `CfdContract` and `DrContract` also have specific semantics. Public discovery of a form is not authorization to weaken those guards. Credit, collateral, termination, broad shaping/imbalance allocation and negotiated curtailment compensation remain unsupported or source-specific.

## Technology costs, maintenance and hydrogen

**Reuse what master now has.** Merged PR #499 supplies narrow USD and CAD overlays for utility PV, onshore wind and simple-cycle gas reference plants. The EIA study uses 2023 USD and a utility-scale engineering boundary; the selected CER scenario uses 2025 CAD, Ontario/CM/2030. The packs deliberately refuse missing regional money rows. Do not introduce a second cost database or silently use an EUR battery/genset/H2 value as CAD/USD.

This leaves valuable extensions:

| Input | Best source candidate from this pass | Required distinction |
| --- | --- | --- |
| Wind/PV observed CapEx and O&M | Berkeley Lab 2025 solar /2026 wind | Plant size/age/region, AC/DC or nameplate basis, sample selection, included cost components |
| Technology cost projections | Existing CER scenario source; ATB candidate | Scenario, projection year and money year; a model pathway is not a procurement quote |
| Battery inverter versus energy block | No complete admitted decomposition established | Duration, chemistry, replacement schedule, round-trip efficiency and augmentation; whole-system$/kW cannot be split without evidence |
| PV/wind age performance | Berkeley Lab empirical performance/degradation tabs | Statistical output/performance aging includes more than physical component decay; weather/repowering/availability controls matter |
| Site maintenance and failure/restoration | No asset-specific public CMMS/OEM history established | FOM$/kW-year is not a failure rate, forced-outage probability or repair-time distribution |
| H2 plant process/economic assumptions | DOE H2A official methodology | Electrolyser technology, MW input vs H2 output, LHV/HHV, utilisation, water/compression/storage boundary and source vintage |
| Industrial H2/NH3 off-take | No complete transparent current price tape established | Location, delivered purity/pressure, product mass/energy basis, certification, transport, support and term |
| Mining plant equipment | Utility tariffs/industrial supplements provide commercial context | Mine process and OEM data remain project-specific; no generic mine plant cost calibration inferred |

Solar O&M notes explicitly include some engineering/maintenance/rent/training costs while excluding property taxes, insurance, land royalties and several administrative/other costs. Adding another all-in O&M factor can double count included items; assuming excluded items are zero can understate the bill. Wind O&M notes suppress some points for confidentiality; do not impute zeros.

The ATB official domain was inaccessible in this environment, and a guessed GitHub repository returned 404. It remains a strong prior-research candidate, **not a freshly downloaded or validated release**. Do not use a mirror to bypass that access block.

DOE's H2A page was actually retrieved. Its common economic assumptions include **2016 reference dollars and legacy tax/financing assumptions**. They are model defaults, not current US policy, a project WACC or a green-H2 selling price. No H2A workbook was executed. HyBlend is technical context, not a blanket permission to put any hydrogen blend into a gas asset. Keep H2 modelling/cost references separate from certificate eligibility and industrial off-take contracts.

## FX, inflation, debt and tax

Bank of Canada Valet returned a real two-business-day JSON sample: `FXUSDCAD` means **CAD per USD**, not its inverse. Preserve observation date, direction, valuation convention, weekend/missing-date policy and lineage of every derived value. Its terms permit specified use/distribution with obligations and exceptions; check third-party data notes rather than assume all Canadian-hosted data uses the federal open licence.

Statistics Canada CPI table 18-10-0004-01 and its Open Licence were retrieved as a Canadian inflation candidate. Federal Reserve H.15 and H.10 release pages were retrieved for US rate/FX context, while BLS and Treasury endpoints failed in this pass. Banxico/INEGI numerical extraction remains incomplete. Do not promote documentation access to a successful numeric-series integration.

Risk-free rates are not WACC or project lending spreads. CPI is not automatically equipment, labour, fuel or maintenance escalation. Preserve currency, real/nominal basis, money year and technology projection year separately. A conversion to a case currency needs a disclosed method and must not change an historical real benchmark into a current nominal offer by relabelling.

US IRS and Canadian CRA current programme guidance were retrieved. Current law, commissioning/commencement dates, location, emissions/labour/ownership conditions and recapture rules require project-specific review. The current IRS update redirects to the newer “Working Families Tax Cuts” page. Do not carry old H2A/ATB tax assumptions into current projects unexamined. No project was determined eligible in this research.
