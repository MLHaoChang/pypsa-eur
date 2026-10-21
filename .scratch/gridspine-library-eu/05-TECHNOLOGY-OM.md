# European technology costs, hydrogen, degradation and maintenance

## New admitted reference subset

The two DEA workbooks linked from the official generation and renewable-fuel pages were actually downloaded and inspected. Both `Intro` sheets explicitly state **Attribution 4.0 International**. The optional Library reference subset preserves the original workbook hashes, sheet/cell/label and the source's central projection columns. It is a **Danish representative planning benchmark**, not a country-specific vendor quote or measured customer result.

| Technology | Money basis | 2030 investment, EUR/MW | 2050 investment, EUR/MW | 2030 fixed O&M, EUR/MW/year | 2050 fixed O&M, EUR/MW/year |
| --- | --- | ---: | ---: | ---: | ---: |
| Onshore wind | 2020 EUR; electric output | 1,146,645 | 1,090,288 | 16,663 | 15,602 |
| Utility fixed-tilt PV | 2025 EUR; **AC** output | 556,221 | 461,075 | 9,880 | 9,580 |
| Commercial/industrial rooftop PV | 2025 EUR; **AC** output | 647,347 | 526,269 | 2,330 | 2,190 |
| 100 MW AEC system | 2020 EUR; total electrical **input** | 989,569 | 652,362 | 44,531 | 16,309 |
| 100 MW PEM system | 2020 EUR; total electrical **input** | 1,249,722 | 563,473 | 56,237 | 14,087 |

Rounded values for orientation; the reference data retains source precision. Do not compare across money years as though they were already harmonised or use future costs as today's quoted prices. No inflation/FX/learning-curve fitting was applied.

There are 24 explicit technology/year combinations: 2025, 2030, 2040 and 2050 for wind; also 2035 for the two PV and two electrolysis archetypes. Missing wind 2035 is refused, not interpolated. The source currently leaves some PV estimate labels blank and misspells the hydrogen 2030 central label `crtl`; extraction pins those exact inspected cells instead of silently assuming a universal workbook layout.

The native `fom_share` is derived from absolute annual O&M / same-year investment for wind/PV. A declining absolute PV O&M trajectory can still have an increasing percentage of CapEx. Do not apply a blanket nominal annual OPEX escalation to this real cohort projection and call that a calibrated maintenance trajectory.

## Reconciliation with existing packs

Current `generic_defaults` already contains technology-data v0.14.0 2030 assumptions, including onwind, generic electrolysis/fuel-cell, storage and ammonia entries. Existing versioned packs remain untouched. The new references add current primary-source detail, extra projection years, commercial-only rooftop PV and explicit AEC/PEM 100 MW distinctions.

The latest technology-data release observed was v0.15.0. This is discovery, not a justified global upgrade: exact input-data rights, changed source lineage and model compatibility still matter. v0.14.0 output rows also retain heterogeneous `currency_year` fields (for example onwind 2015 versus the native pack's 2020 declaration). This is a **source-basis discrepancy requiring reconciliation**, not evidence to silently restate or overwrite existing project assumptions. Its electrolysis investment row cites private communications/IEA; the compiler's GPL licence does not clear all input datasets.

Direct DEA August 2026 power workbook `Intro!C12:E13` specifies 2020 EUR generally and 2025 EUR for the updated PV sheets, which the new references preserve separately. Wind total investment includes source-specific connection, land/neighbour settlements and decommissioning of existing turbines. PV system investment includes connection/installation/balance of plant; utility FOM includes land. Adding those items a second time overstates costs.

## Hydrogen: useful additional datasets

The [European Hydrogen Observatory datasets page](https://observatory.clean-hydrogen.europa.eu/tools-reports/datasets) provides actual downloadable workbooks. Three were downloaded and inspected:

- **Electrolyser cost 2025:** sheet `Electrolyser cost`, row 7 names year, category/subcategory, technology, EUR/kW and EUR/kW/year. It separates stack, BoP, utilities and other CapEx for alkaline and PEM. A next importer should retain component inclusion and scale; similar component numbers are not permission to de-duplicate by guessing.
- **Hydrogen production costs 2024:** `Sheet1!B7:F7` names country, technology, cost component, **EUR/kg** and year. A CAPEX contribution to LCOH in EUR/kg is not a procurement investment input in EUR/kW. Use it as a result benchmark under matched assumptions, not as an asset cost.
- **Break-even renewable hydrogen prices 2025:** sector sheets cover refining, maritime and heavy trucks. The `Overview` includes both 2024 and 2025 rows despite the filename. Production-point and delivered break-even values differ; these are not signed offtake quotes. Negative modelled production-point values must be retained as model results with context, not silently clipped into an invented positive market price.

The Observatory legal notice authorises reproduction with source acknowledgement **save where otherwise stated**. Check table-specific/third-party attribution and transformation scope before the next ingestion; no blanket rights grant is inferred from EU hosting.

Its LCOH calculator explicitly calls defaults best estimates, not observed operating-plant costs. Defaults include stack replacement within other OPEX. Some electricity scenarios exclude grid fees/taxes for direct PV/onshore connection, while grid/offshore paths include them. Comparing these without the connection boundary can create a false technology ranking. A 4,000-cheapest-hour default also does not prove a project's chronological dispatch feasibility.

The new DEA electrolysis subset uses total electrical input CapEx and LHV efficiency. Water and electricity purchase prices, stack replacements, steam/heat, auxiliary boundary and duty-dependent degradation remain separate. The source distinguishes AEC, PEM and SOEC and onshore/offshore variants; one generic electrolyser is insufficient for those distinctions.

## Degradation and maintenance: what is available and what is missing

- DEA's updated PV sheets contain annual full-load-hour degradation benchmarks: utility PV row 41 has a cached fraction 0.0025 (0.25%/year), commercial rooftop row 41 has 0.003 (0.3%/year). Preserve Excel number formats; do not divide a stored fraction by 100 again. These are benchmark assumptions, not calibrated site laws. They were assessed but are not automatically applied by the new cost reader.
- DEA electrolysis sheets contain operating-hour degradation and stack replacement intervals. Those are not calendar years; variable loads/starts can add ageing. Source plant lifetime and stack life are different.
- A SOEC row is labelled technical lifetime in years but has values such as 25,000. It is excluded pending clarification. The H2 tank/cavern workbook also contains O&M labels per power alongside energy-based investment and notes describing percentage-of-CapEx assumptions. Those require an explicit power/energy and unit reconciliation before any derived share is admitted.
- IEA PVPS Task 13 reports provide performance-loss methods, failure descriptions and climate-specific O&M guidance. A performance-loss rate can include availability/soiling/weather/measurement effects. Reports do not supply a universal priced maintenance schedule or failure law.
- ENGIE La Haute Borne and Fraunhofer WMEP were investigated as onshore operational-data leads; fresh access was blocked/unavailable. No event/exposure/licence validation is claimed. SCADA records without labelled failures, observation exposure and censoring do not establish MTTR or failure frequency.
- OREDA's public site exposes membership/subscription context, not an open general component dataset. Do not seed its restricted equipment tables or transfer industry priors to a campus/H2 asset without applicability evidence.
- WOMBAT is an open O&M simulation candidate, not a European cost/maintenance database. Keep it optional and later: onshore logistics, crews, spares and failure inputs would need their own data and numerical validation. No additional simulator dependency is installed here.

Customer CMMS histories, service agreements, current delivered component prices, OEM stack warranties and site repair logistics therefore remain first-class private inputs. Missing maintenance data remains missing; lifetime, aggregate FOM or supply SAIDI/SAIFI cannot fill it by conversion.
