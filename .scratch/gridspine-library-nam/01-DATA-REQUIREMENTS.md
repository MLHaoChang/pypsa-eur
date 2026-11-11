# North American commercial data requirements

Extracted before source research from native contracts/services at master `151fdc934d739bb790d0bd1237d85e7ac40e3719`. Scope: US, Canada and Mexico. Exact class/field bindings below are checked against the source AST. Field presence does not certify numerical or currency support. See native-model-snapshot.json for class definitions, source paths, line numbers and hashes.

| ID | Required input | Native binding / scope | Admission boundary |
| --- | --- | --- | --- |
| T01 | Utility, territory, customer class, service voltage and eligibility | `Tariff.jurisdiction`, `Tariff.dso_or_retailer` | Geography is not evidence of eligibility; class/voltage metadata needs source-level preservation |
| T02 | Effective dates, filing revision, approval and supersession | `Tariff.valid_from`, `Tariff.valid_to` | Current tariff may have riders changed after base schedule |
| T03 | Energy/TOU/tier rates and adjustments | `TariffItem.periods`, `TariffItem.tiers`, `TariffPeriod.tier_rates` | Imported billing units kWh; distinguish component price from delivered all-in price |
| T04 | Season, weekday, hour and holiday schedules | `TariffPeriod.months`, `TariffPeriod.weekdays`, `TariffPeriod.start_hour`, `TariffPeriod.end_hour` | Integer-hour windows; holiday/day-class exceptions need explicit treatment |
| T05 | Demand window, TOU peak and facility demand | `TariffItem.settlement`, `TariffItem.measured_on` | 15/30/60-minute demand requires original-resolution load, not an hourly mean |
| T06 | Contracted capacity, minimum billing demand and service commitments | `TariffItem.kind`, `ConnectionAgreement.capacity_fee` | Actual contracted entitlement and billing-demand floor are not inferred from connected MW |
| T07 | Ratchet share, lookback and history | `Ratchet.share`, `Ratchet.lookback_months`, `Ratchet.months`, `Ratchet.cyclic_year` | Imported ratchet subset only; missing history is not a zero peak |
| T08 | Fixed customer/meter/day fees | `TariffItem.unit`, `TariffItem.periods` | Annual fixed fees may be divided by 12 only with disclosed convention |
| T09 | Transmission/distribution, riders, levies, taxes and wholesale pass-through | `Tariff.items`, `TariffItem.kind`, `TariffPayeeRule.item_id` | Do not double count embedded transmission or treat retail average as a component schedule |
| T10 | Coincident peak, Ontario Class A/ICI and capacity contributions | `Tariff.unsupported_fields` | Current URDB importer refuses coincidentrate fields; five-system-peak cost is not monthly site peak |
| T11 | Minimum bill, standby, reactive/kVA and excess demand | `Tariff.unsupported_fields` | URDB importer refuses minimum bills and non-kW demand; generic tariff schema is not proof of billing support |
| T12 | Export/net billing, avoided cost and injection charges | `CommercialConfig.export_price_ref`, `TariffItem.direction` | URDB sell tiers refused; distinct export price route does not establish a full net-metering scheme |
| T13 | Connection, non-firm access and one-time reinforcement | `ConnectionAgreement.kind`, `ConnectionAgreement.import_cap_mw`, `ConnectionAgreement.envelope` | Customer-specific connection cost and contractual import limit need actual agreement |
| T14 | Fuel/power-cost adjustment and dynamic retail supply | `CommercialConfig.import_price_ref` | Effective component series versus tariff rider must be reconciled to avoid double billing |
| T15 | Rate choices for data centres, industrial sites and renewable IPPs | `Tariff.id`, `CommercialConfig.import_tariff` | Large-load service, interruptible/minimum term and credit requirements stay source-specific |
| W01 | Operator, system, node/hub/zone and market regime | `PriceSeriesRef.id`, `PriceSeriesRef.provider`, `MarketPack.region` | Current MarketRegion enum supports only US_ERCOT/US_PJM/US_CAISO/CA_ON in NAM; raw series storage is broader than market-product execution |
| W02 | DA/RT/imbalance product and price components | `PriceSeriesRef.id`, `MarketPack.price_series` | Separate energy, congestion, losses, scarcity/adders; do not sum total LMP and components |
| W03 | Interval length, hour ending/start and timestamps | `CommercialConfig.timezone` | Preserve UTC offset/DST fold/time-standard rules; naive timestamp plus timezone is refused by series parser |
| W04 | Currency, price units and FX valuation basis | `Participant.currency`, `PriceSeriesRef.source`, `FinanceInputs.currency` | FinanceInputs and Participant carry currency; SeriesMeta carries unit/source_unit; PriceSeriesRef has no dedicated currency/unit and several commercial fields remain EUR-named. Prove end-to-end USD/CAD/MXN valuation and semantic pinning; do not relabel values. |
| W05 | Finality, revisions, missing/duplicate intervals and coverage | `PriceSeriesRef.version`, `PriceSeriesRef.hash` | Newest publication is not always final; missing prices are not zero |
| W06 | Negative and extreme prices and validity flags | `PriceSeriesRef.hash` | Retain signs and source status; do not clip to positive prices |
| W07 | Historical spot vs futures/forwards and scenario curves | `PriceSeriesRef.vintage_year` | Historical data does not establish an actionable forward quote |
| W08 | Ancillary/reserve/DR/capacity products and performance terms | `MarketPack`, `DrContract` | Prices alone do not establish accreditation, dispatch feasibility or simultaneous revenue eligibility |
| W09 | Ontario renewed market, legacy HOEP and Global Adjustment | `PriceSeriesRef.provider`, `Tariff.unsupported_fields` | Keep pre/post-May-2025 regimes separate; OEMP/LMP/HOEP are not interchangeable |
| W10 | Mexico nodal PML components, system and time basis | `PriceSeriesRef.id`, `CommercialConfig.timezone` | SIN/BCA/BCS and regional DST rules; pesos per MWh are not dollars per MWh |
| W11 | Wholesale fuel and carbon/certificate benchmarks | `CommercialConfig.co2_price` | Heating value, delivered vs hub price, regional carbon scheme and certificate attribute must be retained |
| P01 | Fixed price, strike, premium and observation vintage | `PpaContract.price`, `PpaContract.premium_eur_per_mwh`, `CfdContract.strike` | Public awards and historical samples are benchmarks, not current signed bilateral offers |
| P02 | Start/commissioning date, tenor and delivery year | `PpaContract.tenor_years`, `PpaContract.base_year` | Base money/index year is not a full delivery-start schedule |
| P03 | Pay-as-produced/baseload/consumption profile, share and volume | `PpaContract.kind`, `PpaContract.share`, `PpaContract.baseload_mw`, `PpaContract.volume_cap_mwh_per_year` | Generation share, annual cap and fixed notional are different volume risks |
| P04 | Physical/financial settlement, buyer/seller and asset binding | `PpaContract.settlement`, `PpaContract.seller`, `PpaContract.buyer`, `PpaContract.asset_ids` | Financial PPA native validator allows fixed-price pay_as_produced only; not a general swap engine |
| P05 | Reference hub/node, basis and capture-price calculation | `PpaContract.reference_price`, `CfdContract.reference` | Node-to-hub basis, monthly capture and delivery location cannot be assumed equal |
| P06 | Escalation, nominal/real basis and base year | `PpaContract.indexation_pct_per_year`, `PpaContract.base_year` | Levelized PPA benchmark differs from first-year price; retain discount/escalation basis |
| P07 | Caps/floors, negative-price suspension and curtailment | `PpaContract.floor`, `PpaContract.cap`, `PpaContract.suspend_on_negative_price` | Native financial PPA refuses floor/cap; contractual curtailment compensation not implied |
| P08 | Sleeving, shaping, imbalance, losses and network charges | `PpaContract.sleeving_fee_eur_per_mwh`, `PpaContract.sleeving_party` | Named EUR fields and incomplete balancing-risk scope need currency-aware admission |
| P09 | REC/GO/CEL/environmental attribute ownership | `ValueStream.kind`, `TariffItem.kind` | Price may bundle certificates/tax value; no automatic claim of hourly matching or green hydrogen certification |
| P10 | Credit, collateral, guarantees, termination and legal terms | `Tariff.unsupported_fields` | No generic credit-risk/collateral/termination engine; document as unsupported contract terms |
| P11 | Public contract files, awards, transaction observations and publication bias | `PpaContract.library_ref`, `CfdContract.library_ref` | Public SEC/FERC filings and awards may omit prices/clauses; legal text rights separate from price facts |
| P12 | Hydrogen/ammonia off-take, support and industrial product prices | `ValueStream.kind` | Delivered product price, support premium and modelled break-even value are separate; exact native product bindings reviewed separately |
| F01 | Wind/PV/storage/H2 CapEx, FOM/VOM and cost projections | `PackValue`, `CostPart` | Source money year, capacity basis, component inclusion and actual operating vintage separate |
| F02 | Maintenance, replacements, degradation and warranty | `CostPart.lifetime`, `FinanceInputs` | Model-class benchmark is not a calibrated equipment/CMMS law |
| F03 | Inflation/escalation and price indices | `FinanceInputs` | Keep index, currency, real/nominal basis and money year explicit |
| F04 | FX historical conversion and scenario convention | `Participant.currency`, `FinanceInputs.currency` | Raw/derived value lineage and trade date; never relabel currencies |
| F05 | Risk-free rates, WACC, debt terms and fees | `DebtTranche.rate`, `DebtTranche.tenor_years`, `DebtTranche.upfront_fee` | Sovereign yield is not a project WACC, lending quote or calibrated credit spread |
| F06 | Tax, depreciation and incentives | `TaxPack`, `DepreciationSchedule`, `Incentive` | Current law, eligibility, geography and service date; do not assume a project qualifies |
| X01 | Source identity, exact release/row and original document | `LibraryItemRef.hash`, `PriceSeriesRef.hash` | Hash source evidence and transformation rules; link source clauses/cells |
| X02 | Rights for commercial use, cache, derivatives, display/export and common scope | `LibraryItemRef` | Publicly reachable does not establish reuse permission; source obligations propagate |
| X03 | Applicability, uncertainty, actual/benchmark/synthetic and missing data | `PriceSeriesRef.source` | Keep customer facts private; refuse unsupported/missing inputs instead of fabricating defaults |
| X04 | Retrieval limits, credentials and private-data isolation | `LibraryItemRef` | No API keys inside library payloads; fixed reviewed endpoints, bounded retrieval and tenant scope |
