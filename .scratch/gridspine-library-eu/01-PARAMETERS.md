# European library expansion: parameter inventory

Research date: 2026-10-21. Native baseline: `d327f645a` (full SHA is recorded in VALIDATION.json). This is a focused extension of PR #491's 80-family inventory, not another database. EU countries are the primary scope; UK, Norway and Switzerland are separately labelled comparisons.

This inventory was written before the detailed source assessment. A supported field does not imply an admitted dataset, a complete national tariff engine or a validated project assumption.

| ID | Parameter to research separately | Existing family | Native destination / limitation |
| --- | --- | --- | --- |
| T01 | DSO volumetric transport rates and time bands | C02 | TariffItem energy, TariffPeriod; integer-hour windows only |
| T02 | TSO volumetric transport/system charges | C05 | Separate tariff item/payee; must identify upstream charges already included in DSO rates |
| T03 | Contracted capacity charge | C03 | capacity/per_kw_year or per_kw_month; distinguish contracted from measured peak |
| T04 | Measured demand charge and interval | C03 | demand/peak_import and settlement; 15/30/60-minute support requires matching input resolution |
| T05 | Fixed customer, metering and subscription fees | C04 | fixed/per_month or per_day; complete bill requires each applicable component |
| T06 | Reactive energy, power factor and excess capacity penalties | C03 | Not generically represented; retain unsupported_fields, never silently omit |
| T07 | Injection/export tariffs and producer access | C06 | export-side supported items; charging direction and consumer/producer eligibility explicit |
| T08 | One-off grid connection and reinforcement costs | C07 | ConnectionAgreement subset plus finance CapEx; avoid treating one-off connection cost as recurring tariff |
| T09 | Non-firm access and dynamic capacity products | C07 | Native connection kinds and envelope; actual contract-specific availability/curtailment rules required |
| T10 | Taxes, levies, exemptions, VAT and inclusive/exclusive scope | C05 | tax_levy and supported finance subset; eligibility is not inferred |
| T11 | Effective dates, operator, voltage and customer class | C02/C03 | Tariff jurisdiction, dso_or_retailer, valid dates; richer applicability needs admission |
| T12 | Annual tariff projections and provisional schedules | C14 | Dated scenario/reference, distinct from final payable schedule |
| M01 | Historical day-ahead/imbalance prices | C01 | PriceSeriesRef/series store; market regime, bidding zone, currency, UTC/local and interval retained |
| M02 | PPA fixed price, tenor and delivery profile | C08 | PpaContract price/kind/tenor_years/asset_ids; actual quotes usually private |
| M03 | Financial PPA and fixed-for-floating power swaps | C08 | Financial PPA subset only; arbitrary swap products are not a native general derivatives engine |
| M04 | PPA share, volume, shaping and sleeving fees | C08 | Native share, baseload_mw, caps and sleeving fields; balancing/shape risk not automatically priced |
| M05 | Indexation, reference year and negative-price clauses | C08/C14 | Native indexation/base_year/suspension subset; clause-level equivalence required |
| M06 | CfD strike, reference calculation and settlement | C09 | CfdContract subset; awards are benchmarks and support scheme clauses may exceed it |
| M07 | PPA/CfD standard agreement files and legal clauses | C08/C09 | Linked document references and structured archetypes; public download does not grant template redistribution |
| M08 | Forward curves, futures and swap quotations | C01/C08 | Licensed scenario/series if permitted; no open live forward feed assumed |
| M09 | Guarantees of Origin, balancing and ancillary revenues | C10/C18 | Certificates/DR/market subset; attributes, fees and market access separate |
| M10 | Hydrogen support premium vs hydrogen offtake price | C17/C19 | EUR/kg support is not an electricity CfD strike or a delivered H2 quote |
| K01 | Onshore wind investment and cost projections | E01 | Existing onwind investment pack; original money year distinct from projection year |
| K02 | Utility PV investment and cost projections | E01 | PackValue overnight/per_MW; DC/AC capacity boundary must be explicit |
| K03 | Commercial rooftop PV investment and cost projections | E01 | Separate commercial archetype; not a 50/50 household/commercial blend |
| K04 | Fixed O&M and its projection by technology | E02 | PackValue fom_share; percentage of contemporary overnight cost, not absolute currency O&M |
| K05 | Variable O&M and consumables | E03 | Native marginal_cost/finance costs; pack unit registry may not support source VOM units |
| K06 | Technical lifetime vs economic horizon | E04 | PackValue lifetime and independent finance horizon |
| K07 | Replacement, refurbishment and decommissioning | E06 | Existing replacement subset; lifetime is not an OEM maintenance schedule |
| K08 | Inflation, FX and regional cost adjustment | C13/C14 | Explicit source-basis transformations; no automatic localisation by exchange rate |
| K09 | Construction period and commissioning lead time | E05 | Finance subset; no inference from annual CapEx trajectory |
| H01 | AEC, PEM and SOEC CapEx/FOM/lifetime projections | E01/E02/E04/E18 | Existing generic electrolysis primarily AEC; subtype and power basis needed |
| H02 | Electrolyser efficiency and electricity consumption | E18 | Link efficiency on stated LHV/HHV/input-output basis |
| H03 | Stack replacement and efficiency degradation | E06/E18 | Scenario/model extension; calendar vs operating-hour ageing distinguished |
| H04 | Minimum load, ramps and startup requirements | E11/E18 | Native commitment subset; annual average efficiency is not a part-load map |
| H05 | Water, treatment, compression and auxiliary loads | E24 | Explicit conversion/auxiliary ports; scope must prevent double counting |
| H06 | Compressed H2 tanks, caverns and compressors | E23 | Native Store/Link plus per-energy or per-power cost parts; site geology/safety separate |
| H07 | Fuel cell performance and replacement | E10/E18 | Existing generic fuel-cell pack; named technology and heat-recovery boundary |
| H08 | H2 and NH3 product properties and process costs | E19/E20/C19 | Existing ammonia tracer/product factors; LHV, tonnes and delivered cost boundaries |
| D01 | PV annual performance degradation | E07 | Finance degradation supported; physical profile feedback separately validated |
| D02 | Wind age-related performance and availability | E07/R02 | Cohort benchmark vs site-specific performance; maintenance/curtailment confounding |
| D03 | Failure events, MTTR, exposure and censoring | R01/R03 | Native occurrence subset; SCADA anomalies alone are not calibrated failure rates |
| D04 | Preventive maintenance intervals and costs | R04/E02 | CMMS/vendor evidence; FOM is not a maintenance calendar |
| D05 | Battery ageing and replacement sensitivity | E08/E09/E06 | Existing placeholders/finance subset; new physical ageing law requires own validation |

Each ID receives source IDs, a concrete native binding or a named gap, and an admission disposition in `parameter-map.json`. No arbitrary swap, OEM maintenance, degradation or grid-bill capability is claimed from the existence of a document.
