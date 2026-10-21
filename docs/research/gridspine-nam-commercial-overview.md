# GridSpine: North American public-data research

Research date: **21 October 2026**. Geography: **United States, Canada and Mexico**. This is the entry point to the research and implementation handoff, not a claim that new feeds are integrated.

The best next investment is a **small, traceable commercial library**: supported utility tariffs, selected market-price series and clearly labelled PPA benchmarks. Use the existing Library, source pins and regional cost packs. A universal North American tariff/PPA database would conceal important gaps in customer eligibility, billing rules, contract disclosure and data rights.

The tool was examined **before source selection**: [48 native data requirements](../../.scratch/gridspine-library-nam/01-DATA-REQUIREMENTS.md), covering tariffs, wholesale products, contracts, finance and provenance. All 48 have a [requirement-to-source assessment](../../.scratch/gridspine-library-nam/source-map.json); a mapping can explicitly conclude that public data or native calculation support is missing.

## What is useful now

| Priority | Recommended addition | Benefit and boundary |
| --- | --- | --- |
| 0 | Currency, calendar, rights and applicability admission checks | USD/CAD/MXN fields and a reachable URL are insufficient. Check the full calculation/export path, exact source product and permitted use before numerical admission. |
| 1 | URDB supported tariff subset, verified against utility schedules | Strong US discovery source with an existing importer. Preserve effective dates, eligibility and unsupported clauses. Do not silently discard demand, reactive, minimum-bill or coincident-peak charges. |
| 1 | One permitted ISO historical price pilot | Actual CAISO, NYISO and MISO files were inspected. Begin with a supported native region after resource rights review; do not enable every market from a generic CSV alone. |
| 1 | Berkeley Lab solar/wind PPA benchmark references | Actual 2025 solar and 2026 wind workbooks were inspected. Retain historical/executed versus offered, real versus nominal, levelization year and delivery profile. Benchmarks must not become a user's signed contract. |
| 1 | Reuse shipped US/Canadian technology costs | Master now contains PR #499's narrow `us_federal` and `ca_federal` overlays. Expand coverage only where the source supports the component, capacity basis and currency; do not duplicate these packs. |
| 2 | Ontario price and tariff pilot; FERC EQR observations | IESO renewed-market CSV/XML samples are usable research evidence. GA/ICI needs its own billing treatment. EQR can add reported wholesale transaction/contract evidence after a current-schema extraction. |
| 2 | Mexico source and product admission | A real historical CENACE PML file was inspected. Current corrected price history and CFE tariffs still need verified access, rights, currency and calendar treatment. |
| 3 | Financial swap/forward data and complex contract terms | CFTC dissemination is a research lead; CME and private PPA services are not an established free common feed. Keep user-supplied, private inputs available. |

## The most consequential findings

- **Tariffs:** URDB is valuable, but the recent commercial record inspected includes a reactive-power charge; its apparent voltage metadata also warrants checking against the utility document. Dominion GS-4 includes reactive charges and commitments. BC Hydro's current contents list transmission schedule 1830 and retain legacy 1823 references elsewhere: an older schedule mapping cannot be assumed current.
- **Market prices:** MISO explicitly uses fixed EST; NYISO sample timestamps have no offset; CAISO's `PRC_LMP` CSV puts price values in a column named `MW`; IESO separates creation and delivery dates. Mexico's historical ZIP contains two publication versions of the same operating day. A generic “date, price” importer would lose material information.
- **PPAs:** Solar executed prices are levelized in real 2023 dollars at 4% real; wind's inspected price header is real 2025 dollars. Solar workbook offer indices are first-year nominal offers. None is interchangeable with a current bilateral strike or a complete forward curve.
- **Actual reported contracts:** FERC EQR deserves a focused implementation study. Its public regulatory observations can complement Berkeley Lab benchmarks, but do not cover every private PPA. Order 917 changed reporting requirements in 2026; the new XBRL-CSV implementation timeline was explicitly still unset on the inspected page.
- **Public access versus reuse:** OpenEI's portal states CC0 unless otherwise noted, and the solar OEDI record explicitly links CC BY 4.0. Conversely, PJM currently conditions redistribution of raw **and derived** Data Miner data on active membership, minimum Associate. AESO and BC Hydro have restrictive site terms. EIA's ICE hub series is third-party data, distinct from EIA-produced public-domain statistics. These differences must survive export and chat answers.
- **No complete public price tape:** Current private corporate PPA quotes, collateral, sleeving fees, industrial H2/NH3 off-take terms, insurance and customer connection quotes remain incomplete or private. Missing data must stay unknown.

## Read the complete package

1. [Requirements and native bindings](../../.scratch/gridspine-library-nam/01-DATA-REQUIREMENTS.md).
2. [Source catalogue: 49 source families, 140 retrieval observations](../../.scratch/gridspine-library-nam/02-SOURCE-CATALOGUE.md), with exact sources, access failures and rights boundaries.
3. [Country findings and actual file schemas](../../.scratch/gridspine-library-nam/03-COUNTRY-FINDINGS.md).
4. [PPA, contracts, costs and comparability](../../.scratch/gridspine-library-nam/04-CONTRACTS-COSTS.md).
5. [Implementation sequence and validation matrix](../../.scratch/gridspine-library-nam/05-INTEGRATION-VALIDATION.md).
6. [Portable handoff](../../.scratch/gridspine-library-nam/HANDOFF.md), [machine-readable source register](../../.scratch/gridspine-library-nam/source-register.json), [validation record](../../.scratch/gridspine-library-nam/VALIDATION.json).

The 49 families reconcile all 19 prior North America commercial source IDs; they are not 49 additional independent datasets. The 140 observations include documentation, samples, terms pages, failed requests and semantic failures. Seven bounded source samples were inspected offline, plus selected cells in two PPA workbooks. This research does not ingest price histories, implement new billing engines, approve domain correctness or establish comprehensive market coverage.
