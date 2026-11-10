<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Plant business cases: revenue streams, cost lines and their quantity drivers

Research for [spec.md](spec.md) §6, 2026-11-10. The data centre is in
[../dc-business-case/research.md](../dc-business-case/research.md) and is not repeated.

**Verification tags.** **[V]** read on the source page itself. **[S]** read from
the source's search excerpt, page not opened. **[U]** secondary or trade press, or
derived; not to become a default. A preset default ships only from a [V] or [S]
figure, with a range and `assumed` (GK B ruling); revenue prices never default
(DCBC Q4) and appear, if BCA-Q1 picks it, only as hint text.

**Access limits.** `atb.nrel.gov` and `nrel.gov` did not resolve from the research
session and `iea.org` returned 403; NREL ATB 2024 O&M values are therefore not
given and the matching rows have no default until they are read.

## 1. Green hydrogen hub

| Line | Unit | Quantity driver | Value (year, source) |
|---|---|---|---|
| Fixed production premium (EU Hydrogen Bank, pilot) | €/kg | kg RFNBO H2 delivered, ≤ 10 years | seven winners at €0.37–0.48/kg, ceiling €4.50/kg, €720m total (2024) [S] [1] |
| Same, second auction | €/kg | same | 15 projects, €992m, ceiling €4/kg, awards ≈ €0.20–0.60/kg (2025) [U] [2][3] |
| H2 offtake price | €/kg | kg delivered | no public primary benchmark; site-specific, the premium is paid on top [U] |
| Oxygen | €/t O2 | ≈ 8 t per t H2 (7.94 stoichiometric) | one public deal (≥ €1m/yr, capacity fee + volume payment, 2023) [U] [4] |
| Waste heat | €/MWh_th | ≈ 20–25 % of input power | listed as a by-product revenue, no price [S] [5]; out of v1 (GK A) |
| Stack replacement | % of direct capex per event | operating hours | 15 % at a 40,000 h interval (DOE/NREL 2022) [S] [6]; alkaline 80,000 h (2021) [S] [7] |
| Fixed O&M | % capex/yr | capex | 3 %/yr for alkaline at US$750/kW (2021) [S] [7] |
| Water | kg/kg H2 | kg H2 | 9 theoretical, 20–60 in practice (2024) [S] [10]; no sourced price |

- The Hydrogen Bank pays a fixed €/kg on verified output, pay-as-bid, up to ten
  years [S] [1].
- Offtake is a take-or-pay or minimum-volume agreement with an industrial user
  [U]; H2Global is a double auction through an intermediary [S] [11].
- Electrolytic O2 competes with air-separation O2 and needs an offtaker on site
  [S] [5]: the preset row is off by default.

## 2. Green ammonia

| Line | Unit | Quantity driver | Value |
|---|---|---|---|
| Green NH3 (H2Global pilot lot) | €/t | t delivered | €1,000/t delivered Rotterdam, €811/t net of delivery, ceiling €1,282/t, ≥ 40 kt/yr 2028–33 (2024) [S] [11] |
| Grey NH3 benchmark | $/t | t | Tampa CFR $392–650/t (2025); NW Europe $475–610/t (2025) [U] [12] |
| Synthesis and ASU O&M | % capex/yr | capex | 2–3 % in the literature, no primary figure [U] |
| Storage, terminal, shipping | €/t | t | ≈ €189/t implied by [11] (derived) [U] |

- H2Global signs a ten-year fixed-price purchase with the producer [S] [11];
  commercial offtake indexes to a grey benchmark plus a premium [U].

## 3. Mining concentrator (copper)

| Line | Unit | Quantity driver | Value |
|---|---|---|---|
| Payable copper | % of contained Cu | dmt × grade × metal price | typically 96.5 %, minimum deduction 1 unit [U] [13] |
| Treatment charge (deduction) | $/dmt | dmt concentrate | annual benchmark $80 (2024), $21.25 (2025), $0 (2026) [S] [14] |
| Refining charge (deduction) | ¢/lb payable Cu | lb payable | 8.0 (2024), 2.125 (2025), 0 (2026) [S] [14] |
| Site operating cost | $/t milled | t milled | C$12.89/t life of mine (2025 technical report) [S] [15] |
| Process plant cost | $/t milled | t milled | US$5.58–11.26/t (2025 reports) [U] [16] |
| Freight | $/wmt | wet t shipped | not found |

- Sold to smelters at the annual benchmark TC/RC or spot, quotational periods
  M+1 to M+3, impurity penalties, wet-to-dry settlement [U] [13]. A net €/t line
  (BCA-Q3 A) folds these; the row's note names them.

## 4. IPP: solar, wind, hybrid with BESS

| Line | Unit | Quantity driver | Value |
|---|---|---|---|
| UK CfD AR6 strike | £/MWh (2012 money) | MWh metered | solar £50.07, onshore £50.90, offshore £58.87 (2024) [V] [17] |
| German EEG ground-mounted PV tender | ct/kWh | MWh | 4.66 (Mar 2025), 4.84 (Jul 2025) volume-weighted [S] [18] |
| Corporate PPA | €/MWh | MWh | Europe solar P25 €59.62 (Q2 2025) [S] [19] |
| GB capacity market | £/kW-yr de-rated | de-rated kW | T-4 2028/29 £60; T-1 2025/26 £20 (2025) [S] [20] |
| Poland capacity market | PLN/kW-yr | kW | 2029 main auction PLN 264.90 (2024) [S] [21] |
| Italy capacity market | €/MW-yr | MW | 2027 delivery €47,000 (2025) [U] [22] |
| BESS ancillary + trading (DE) | €/MW-yr | MW | ≈ €150k–290k (2025–26 indices) [U] [23] |
| Guarantees of origin | €/MWh | MWh | €0.3–2 (2024–25) after €7–10 (2023) [U] [24] |
| Fixed O&M | $/kW-yr | kW | NREL ATB 2024 includes insurance, land lease, labour; values not read [S] [25]; land wind US$43/kW-yr (FY25) [S] [26] |

- The template's PPAs, merchant export and CfD are ledger streams already (spec
  §2, G2); only capacity payments, GOs and non-energy costs are new rows.

## 5. Microgrid

| Line | Unit | Quantity driver | Value |
|---|---|---|---|
| Energy tariff to served customers | €/kWh | kWh delivered | site-specific, usually a discount to retail or a PPA [U] |
| EaaS fee | €/month | contracted service | provider builds, owns, operates [U] [27] |
| Resilience value | — | — | valued with VoLL in the literature; no standard framework [S] [28]; **not** modelled (B4) |
| Capex benchmark | $M/MW DER | MW | community 2.1, campus 3.3, commercial 4.0 (2018) [S] [29] |
| Non-fuel O&M | $/yr | — | one design: $443k/yr (2024) [S] [30]; no per-MW benchmark |

## 6. Generic industrial site (behind the meter)

Qualitative: avoided grid energy, demand and network charges are already the
IC's ledger (the counterfactual difference), so the business case adds only what
the energy model does not see: product sales, service or tolling fees,
flexibility availability, GOs, staff, maintenance, insurance, property tax,
land lease, SG&A and the process plant's capex. These are the offered rows of
spec §6.1.

## Sources

1. European Clean Hydrogen Observatory, "European Hydrogen Bank pilot auction results", 2024. https://observatory.clean-hydrogen.europa.eu/index.php/media/news/european-hydrogen-bank-pilot-auction-results-spark-renewable-hydrogen-competitiveness
2. Hydrogen Europe, "The Commission selects 15 projects to receive nearly €1 billion", 2025. https://hydrogeneurope.eu/boosting-renewable-hydrogen-the-commission-selects-15-projects-to-receive-nearly-e1-billion/
3. Argus Media, "EU H2 bank winners benefit from ineligible bids", 2025. https://www.argusmedia.com/es/news-and-insights/latest-market-news/2691764-eu-h2-bank-winners-benefit-from-ineligible-bids
4. H2-Tech, "Everfuel signs oxygen offtake deal", 2023. https://h2-tech.com/articles/2023/january-2023/hydrogen-economist/everfuel-signs-oxygen-offtake-deal/
5. IRENA, "Innovation landscape: sale of electrolysis by-products (oxygen and heat)". https://www.irena.org/Innovation-landscape-for-smart-electrification/Power-to-hydrogen/30-Sale-of-electrolysis-by-products-oxygen-and-heat
6. NREL for US DOE, PEM electrolysis cost analysis, NREL/PR-82706, 2022. https://www.nrel.gov/docs/fy23osti/82706.pdf
7. IRENA, "Hydrogen economic perspectives" (slides), Dec 2021. https://gesel.ie.ufrj.br/wp-content/uploads/2022/06/28_IRENA-H2-econ-perspectives-PabloRalon-16-Dec-21.pdf
8. EPRI, electrolysis cost report 3002031505. https://restservice.epri.com/publicdownload/000000003002031505/0/Product
9. US DOE Hydrogen Program AMR 2022, P204. https://www.hydrogen.energy.gov/pdfs/review22/p204_james_2022_p.pdf
10. IEAGHG, "Analysis of electrolytic hydrogen", 2024-08. https://publications.ieaghg.org/technicalreports/2024-08%20Analysis%20of%20electrolytic%20hydrogen.pdf
11. BMWK press release "H2Global", 11 Jul 2024. https://www.bundeswirtschaftsministerium.de/Redaktion/EN/Pressemitteilungen/2024/07/20240711-h2global.html
12. QCIntel / S&P Global ammonia market reports, 2024–25. https://www.qcintel.com/ammonia/article/drop-in-tampa-ammonia-contract-pressures-europe-and-americas-24055.html
13. Fastmarkets, copper concentrate trade terms, 2025. https://www.fastmarkets.com/insights/sulfur-pyrite-tolling-new-levers-in-copper-concentrate-trade-sources/
14. S&P Global, 2025 TC/RC settlement, Dec 2024. https://www.spglobal.com/energy/en/news-research/latest-news/metals/120624-antofagasta-settles-copper-concs-treatment-charge-with-jiangxi-copper-at-2125mt-for-2025-term-contracts ; SMM, 2026 benchmark. https://news.metal.com/vn/newscontent/103681795-Chinese-Smelters-Antofagasta-Set-2026-Copper-Concentrate-TCRC-Benchmark-at-000%C2%A2
15. Global Mining Review, Yellowhead technical report update, Jul 2025. https://www.globalminingreview.com/exploration-development/14072025/taseko-announces-improved-economics-for-yellowhead-copper-project/
16. Project Blue, "Digging into copper costs", 2024–25. https://projectblue.com/blue/news-analysis/1288/digging-into-copper-costs:-concentrate-vs-sx-ew-and-the-road-ahead
17. DESNZ, "Contracts for Difference Allocation Round 6: results", Sep 2024. https://www.gov.uk/government/publications/contracts-for-difference-cfd-allocation-round-6-results/contracts-for-difference-cfd-allocation-round-6-results-accessible-webpage
18. Bundesnetzagentur, solar tender results, Mar and Jul 2025. https://www.bundesnetzagentur.de/SharedDocs/Pressemitteilungen/DE/2025/20250423_PVFFAV.html
19. LevelTen Energy, "Q2 2025 PPA Price Index", 2025. https://www.leveltenenergy.com/post/q2-2025-ppa-price-index
20. Ofgem, annual report on capacity market operations 2024/25, 2025. https://www.ofgem.gov.uk/sites/default/files/2025-09/annual-report-capacity-market-operations-2024-25.pdf
21. URE, final results of the 2029 main auction, 2024. https://www.ure.gov.pl/en/communication/news/413,Electricity-Market-The-President-of-URE-Announces-Final-Results-of-The-2029-Main.html
22. ESS News, Terna capacity auction for 2027 delivery, Feb 2025. https://www.ess-news.com/2025/02/28/terna-energy-storage-capacity-auction-2027-delivery-results/
23. Modo Energy, Germany BESS benchmark report, 2026. https://modoenergy.com/research/germany-bess-benchmark-report-revenues-wind-autumn-drop-ancillary-saturation
24. EEX, GO futures FAQ, 2026. https://www.eex.com/fileadmin/EEX/Downloads/Markets/Guarantees_of_Origin/GO_Futures/20260211_GO_FAQ.pdf
25. NREL, "2024 ATB definitions", 2024. https://atb.nrel.gov/electricity/2024/definitions
26. NREL, "Cost of Wind Energy Review", FY25. https://nrel.gov/docs/fy25osti/91775.pdf
27. NASEO, microgrid executive summary. https://aeecenter.naseo.org/Data/Sites/1/naseo_microgrid_exec.pdf
28. US DOE Office of Electricity, "Capturing the value of microgrids' resilience". https://www.energy.gov/oe/articles/power-storm-capturing-value-microgrids-resilience
29. NREL, "Phase I Microgrid Cost Study", NREL/TP-7A40-67821, 2018. https://www.nrel.gov/docs/fy19osti/67821.pdf
30. NREL, community microgrid conceptual design, 2024. https://www.nrel.gov/docs/fy24osti/89527.pdf

## Gaps

- NREL ATB 2024 O&M (PV, wind, BESS): not read; the rows have no default until
  they are (the hosts need allowing in the environment's network settings).
- No primary source: H2 offtake price, O2 and heat prices, water price, copper
  reagent/media split, freight, microgrid O&M per MW, separate land-lease and
  insurance figures. Those rows have no default.
- Most figures are [S]: the preset slices (11–13) re-read each one on its page
  before it becomes a default or a hint, and record the tag they reach.
