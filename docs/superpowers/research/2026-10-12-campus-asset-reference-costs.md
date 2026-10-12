# Campus asset library: reference costs from public sources (2026-10-12)

**Question.** The campus asset library (`gridspine/templates/data/campus_assets.yaml`, 81 entries) tags every capex, fixed-O&M share and lifetime `assumed`: they are order-of-magnitude placeholders. The owner decided on 2026-10-12: "Reference costs from sources". What do public, citable, high-trust sources say these costs are, and what changes when the study buys at those prices?

**Answer, in short.**
- One regulator's catalogue covers most of the library at the right voltages and sizes: the Swedish Energy Markets Inspectorate's (Ei) **standard values for grid assets, 2024-2027**. It prices installed transformers (123-145/12-24 kV, 123-145/36-52 kV, 24-36/12 kV, 24/0.4 kV, by MVA), buried MV cables (by voltage class, cross-section and ground type), MV and HV switchgear bays, and MV capacitor banks (base cost plus a cost per Mvar), in 2022 SEK.
- The **Danish Energy Agency's technology catalogue for energy transport** gives the fixed O&M shares (1.5 % a year for an onshore AC substation; 0.51 % for distribution assets) and the technical lifetime (40 years for distribution stations and cables).
- **No usable source was found** for medium-voltage shunt reactors and STATCOMs (the only European figures are for 380 kV, 100 Mvar units), for the lifetimes of compensation equipment, for a switchgear bay's price by short-circuit rating, or for some sizes outside Ei's ranges. These stay `assumed`, each with a note saying why.
- 49 of the 81 capex figures, 72 of the 81 O&M shares and 54 of the 81 lifetimes are now cited (`literature`); the rest stay `assumed`.
- **On the Data Center Energy Hub template the study buys the same equipment as before**, at EUR 6.70 M capex and EUR 603 k a year, compared with EUR 6.89 M and EUR 610 k at the placeholders. The 132/33 kV transformer costs a third less; the three 33/11 kV units cost 42 % more.
- **These are reference values, not quotes.** They are a regulator's standard costs for valuing Swedish network companies' assets, converted to 2025 euro. They do not replace a vendor's price.

**Where the values are.** `2026-10-12-campus-asset-reference-costs.yaml`, beside this note, is a complete asset library with these costs. It is **a proposal and is not wired in**. The shipped library is unchanged, because the investment case's defaults pack (version 2026-10-07) transcribes the shipped costs, and its parity test (`pypsa-gui/backend/tests/test_library_defaults_pack_campus.py`) fails on any price change: "a price change needs a new pack version, never an edit of 2026-10-07". That pack is owned by the investment-case session. The change it would make is in [Adopting these values](#adopting-these-values-for-the-pack-owner). Until then, a project can use the proposal by saving it as its own library.

## Sources

Every source below was downloaded and read for this note. Downloaded files were kept outside the repository; PDFs were read as text, and the Ei spreadsheet was read cell by cell.

| key | source | publisher, year | what was used | money |
|---|---|---|---|---|
| [EI-NVL] | *Normvärdeslista elnät 2024–2027*, xlsx, sheet "NVL RP4". [Download](https://ei.se/download/18.61d7efeb18b048db98c4e48/1697184391450/Normv%C3%A4rdeslista-2024-2027.xlsx), linked from Ei's [documents page for the 2024–2027 revenue caps](https://ei.se/bransch/rapportera-in-uppgifter-till-ei/forhandsreglering-natavgifter/dokument---forhandsreglering-av-intaktsramar-elnat-for-tillsynsperiod-2024-2027) | Energimarknadsinspektionen (Swedish regulator), 2023-10-13 | Installed cost of transformers, cables, switchgear bays and their control equipment, and capacitor banks, per norm code | SEK, column "Normvärde 2022 [SEK]": 2022 prices |
| (method) | *Konsultrapport Normvärdeslista 2024–2027* ([PDF](https://ei.se/download/18.61d7efeb18b048db98c4e5a/1697035558170/Konsultrapport-Normv%C3%A4rdeslista-2024%E2%80%932027-SWECO.pdf)) | Sweco for Ei, 2022-11-15 | How the norm values were set: the 2018 list indexed by the construction cost index (byggkostnadsindex) to 2021, a benchmark against the industry's EBR cost catalogue, and expert review; Ei's final list is labelled 2022 | – |
| [DEA-TRANS] | *Technology Data for Energy Transport*, version 0008, chapter 1 "Transmission of electricity" ([PDF](https://ens.dk/media/7680/download)) | Danish Energy Agency and Energinet, Dec 2025 | Table 13: O&M in % of capex a year (onshore AC substation 1.5 %, onshore AC cable 2.5 %); Table 7 note 5: an AC substation includes transformers, switchgear and compensation. Also read for context: Tables 8, 10–12 (transformer and compensation costs at transmission level) and the 2019–2023 price-rise discussion (pp. 56–57) | EUR 2020 |
| [DEA-DIST] | *Technology Data for Energy Transport*, version 0006, chapter 111 "Electricity distribution grid", data sheets (pp. 40–45) ([PDF](https://ens.dk/media/6440/download)) | Danish Energy Agency and Energinet | Technical lifetime 40 years for stations and cables (note C, citing Ei's regulation); fixed O&M 0.51 % of investment a year (notes L and Q). Version 0008's amendment sheet says the distribution-level data "remains the same as before" | – |
| [NEP-2021] | *Netzentwicklungsplan Strom 2035, Version 2021, 2. Entwurf: Kostenschätzungen* ([PDF](https://www.netzentwicklungsplan.de/sites/default/files/2023-02/26_NEP_2035_V2021_2E_Kostenschaetzung_0.pdf)); cost basis mid-2020 per the plan's chapter 5.3.1 ([PDF](https://mst.dk/media/xq3jv14h/netzentwicklungsplan-strom-2035-version-2021.pdf), p. 132) | The four German TSOs, 2021 | Quoted in notes only: 380 kV, 100 Mvar reactor 2.1 M€, STATCOM 13.0 M€, switched capacitor (MSCDN) 2.0 M€; a 110 kV transformer bay 2.0 M€ | EUR mid-2020 |
| (rate) | ECB, euro foreign exchange reference rates, series `EXR.A.SEK.EUR.SP00.A` ([data API](https://data-api.ecb.europa.eu/service/data/EXR/A.SEK.EUR.SP00.A?format=csvdata)) | ECB | 2022 annual average: 10.6295750972763 SEK per EUR | – |
| (index) | Eurostat, HICP annual data, `prc_hicp_aind`, EA20, all items (CP00), annual average index 2015=100 ([API](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_aind?format=JSON&geo=EA20&coicop=CP00&unit=INX_A_AVG&sinceTimePeriod=2015)), updated 2026-02-06 | Eurostat | 2020: 105.05; 2022: 116.83; 2025: 128.76 | – |
| (cross-check) | *Stromnetzentgeltverordnung (StromNEV)*, Anlage 1 "Betriebsgewöhnliche Nutzungsdauern" ([text](https://www.gesetze-im-internet.de/stromnev/anlage_1.html)) | German federal regulation | Regulatory depreciation lives: MV cables 40–45 a; distribution stations 25–35 a; switching equipment 30–35 a; distribution transformers 30–35 a; HV station equipment including transformers and switches 35–45 a. Used only as a cross-check on the 40 years | – |

**Read but not used for values.**
- **ACER, *Unit Investment Cost Indicators*, 2023** ([report](https://www.acer.europa.eu/sites/default/files/documents/Publications/UIC_report_2023.pdf), [indicator table of 04.07.2023](https://www.acer.europa.eu/sites/default/files/documents/Publications_annex/ACER_UIC_indicators_table.pdf)). These are EUR 2023, deflated with HICP. Their transformers start at 150/60 kV, with 3 assets (EUR 1.21 M each, 63.8 kEUR/MVA). Their substation bays start at 220 kV. They have no MV equipment, no capacitor banks, no reactors and no STATCOMs; SSSC is the only power-electronic indicator. DEA's version 0008 also discards ACER's substation data, which it finds about three times lower than the German TSOs' (p. 61).
- **ACER, *UIC report*, 2015** ([PDF](https://www.acer.europa.eu/sites/default/files/documents/Publications_annex/UIC%2520report%2520%2520-%2520Electricity%2520Infrastructure%2520corrected_0.pdf)). Its transformers are 150–800 MVA (9.5 kEUR/MVA median), so nothing is at campus scale.
- **MISO, *Transmission Cost Estimation Guide* (MTEP22)**: the primary PDF could not be downloaded; the address DEA cites now returns an error page. DEA transcribes it (version 0008, Table 10): reactor 16, capacitor bank 12, SVC 115 and STATCOM 227 kEUR/Mvar, 69–765 kV, 2022 USD with US taxes and contingency included. It was not cited, because the primary text was not read.
- **Swedish revenue-cap ordinance (SFS 2018:1520), Annex 1** (economic lifetimes by asset category). The published consolidated text omits the annex ("Bilagorna ej med här"). DEA's 40 years cites the same regulator.

## Conversion

- **Currency and price year.** 2022 SEK → EUR at the ECB's 2022 annual average rate (10.6296 SEK/EUR), then 2022 → 2025 by the euro-area HICP (116.83 → 128.76, × 1.1021). Combined: **1 SEK (2022) = 0.10368 EUR (2025)**. Rounded to 100 EUR. The library's `price_year` becomes **2025**: the latest year with an annual HICP (published 2026-02-06). The placeholders claimed 2026 money.
- **Why HICP.** It is the index ACER's 2023 report uses ("general inflation … HICP", §4.2.3, p. 17). It probably **understates** equipment prices. DEA reports that transmission-asset prices rose 20–100 % in nominal terms from 2019 to 2023 (version 0008, p. 56), and it adds 20 % on top of inflation for 2025 (50 % for transformers). Ei's own indexation used the Swedish construction cost index. A user who wants that view can scale the capex.
- **Sizes between two of Ei's sizes** (31.5 and 50 MVA at 132/33 and 110/20 kV; 400 and 630 kVA at 20/0.4 kV) are interpolated on a straight line in log-log through the two neighbouring sizes (a local power law, cost ∝ S^b). Each note gives b. No external scaling rule was needed. Interpolated values are tagged `literature`, since they lie between two cited figures.
- **Sizes outside Ei's range** (33/11 kV at 2.5, 31.5 and 40 MVA, where Ei covers 4–25 MVA; 20/0.4 kV at 1.6 and 2.5 MVA, where Ei covers 315–1250 kVA) are extrapolated with a power law fitted to the class's rows. They are tagged **`assumed`**, because an extrapolation is not a figure read from the source.

## How each kind was mapped

- **Transformers.** Ei's class is chosen by the highest equipment voltage Um, which is the IEC standard voltage above the nominal: 132 kV and 110 kV fall in 123–145 kV, 33 kV in 36 kV, 20 kV in 24 kV, 11 kV in 12 kV. So 132/33 kV is *123–145/36–52 kV* (R-NR-T-5-x), 110/20 kV is *123–145/12–24 kV* (R-NR-T-4-x), 33/11 kV is *24–36/12 kV* (R-NR-T-1-x), and 20/0.4 kV is *24/0,4 kV* (NG159xx, L-NG-T-2-1).
  - Ei's cost of an HV/MV unit is installed: "base cost, transformer bay including foundation and oil pit, cable joints, transformer with on-load tap changer including transport, erection, design, project management; excluding neutral-point equipment".
  - **The source is not monotone at 33/11 kV.** 16 MVA costs 8.26 MSEK and 20 MVA 7.54 MSEK. It is kept as published, so the study may prefer 20 MVA to 16 MVA.
- **Cables** use Ei's *Jordkabel Landsbygd normal* rows: buried cable in rural, normal ground, three-core XLPE ("PEX 3x…"). A campus is private land without paved streets, which is closest to this ground type. Ei's urban (*tätort*) rows are about twice as dear.
  - The 12 kV and 24 kV rows include survey ("inmätning") and exclude the municipal trench fee and wayleave payments. They do not state the conductor material. The 36 kV rows say aluminium and include joints, trench, traffic measures, earth wire, signal cable and cover.
  - Ei has no three-core 95 mm² cable at 36 kV. Its single-core 3x1x95 is dearer than the three-core 3x150. So `CB_33_AL95` is the 3x150 row scaled by the 24 kV ratio of 95 to 150 mm² (0.9255), tagged `assumed`.
- **Switchgear, per bay** = one circuit-breaker feeder bay (*1 brytarfack ledning*) + that bay's control equipment (*Kontrollutrustning ledning*: relay protection, cabinets, design, erection), because Ei prices the bay "excluding control equipment".
  - At 11, 20 and 33 kV the bay is indoor metal-clad (R-NR-SF-1-3, -2-3, -3-3). At 110 and 132 kV it is outdoor air-insulated with a single busbar (R-NR-SF-7-5, -8-5).
  - The control equipment is R-NR-KF-1-1 at 12–24 kV and R-NR-KF-2-1 at 36–170 kV.
  - **Ei does not price by short-circuit rating.** The lowest rating at each voltage carries Ei's figure (`literature`). Each higher rating keeps the shipped library's ratio over it (for example 40/25 kA at 33 kV is 1.272), tagged `assumed`.
  - The German TSOs' 110 kV transformer bay is 2.0 M€ ([NEP-2021]), about six times Ei's 145 kV bay. It includes the bay's share of a TSO substation's infrastructure. It is noted, not used.
- **Capacitor banks** = Ei's base cost per bank (foundation, fence, gate, rack, unbalance protection, connection) + its cost per Mvar × the bank's Mvar: 12–24 kV R-NR-KB-1-1/1-2 for 11 and 20 kV, and 36–52 kV R-NR-KB-2-1/2-2 for 33 kV.
  - The bay is not included; the study prices it as a switchgear bay.
  - The number of switched steps is not priced, since Ei prices a bank, not its steps.
- **Shunt reactors and STATCOMs: no source.**
  - The European figures are for 380 kV, 100 Mvar units: [NEP-2021] gives 21 and 130 kEUR/Mvar, mid-2020. DEA's MISO transcription gives 16 and 227 kEUR/Mvar at 69 kV and above.
  - None is for 1–20 Mvar at 11–33 kV, where the fixed part of the cost dominates. Applying them would put a number on an unsupported scaling.
  - The shipped capex is kept, tagged `assumed`, and the note quotes NEP.
- **Fixed O&M share.**
  - 1.5 % a year ([DEA-TRANS] Table 13, *onshore AC substation*, whose cost includes transformers, switchgear and compensation per Table 7 note 5) for HV/MV and MV/MV transformers, switchgear, capacitor banks and reactors.
  - 0.51 % a year ([DEA-DIST], notes L and Q) for distribution assets: MV cables and 20/0.4 kV transformers. DEA notes that "there is practically no maintenance on cables".
  - The STATCOM's 3 % is kept `assumed`: a power-electronic converter is not what a substation O&M share was measured on.
- **Lifetime.** 40 years ([DEA-DIST] note C, "the technical life time for stations and cables are 40 years", citing Ei's regulation) for transformers, cables and switchgear. This is the placeholder's value, now cited. Germany's regulatory depreciation lives (StromNEV Anlage 1) span 25–45 years for the same equipment; they are book lives, not technical ones. Compensation lifetimes (bank 25, reactor 30, STATCOM 20) are kept `assumed`.
- **Discount rate.** Unchanged at 0.07, `assumed`. None of these sources sets a campus owner's cost of capital, and the project's own rate replaces the library's when the study runs (plan D1a).

## Source table: kind → source → value → price year

| kind | source (key, rows) | value (EUR 2025) | source price year |
|---|---|---|---|
| transformer 132/33 kV, 20–100 MVA | [EI-NVL] R-NR-T-5-2…5-8; 31.5 and 50 MVA interpolated | 1.31 M (20 MVA) … 1.65 M (63 MVA) … 2.20 M (100 MVA) | 2022 SEK |
| transformer 110/20 kV, 16–63 MVA | [EI-NVL] R-NR-T-4-1…4-6; 31.5 and 50 MVA interpolated | 0.79 M (16 MVA) … 1.51 M (63 MVA) | 2022 SEK |
| transformer 33/11 kV, 4–25 MVA | [EI-NVL] R-NR-T-1-1…1-6 | 0.35 M (4 MVA) … 1.13 M (25 MVA) | 2022 SEK |
| transformer 33/11 kV, 2.5, 31.5, 40 MVA | extrapolated, **assumed** | 0.24 M, 1.17 M, 1.36 M | – |
| transformer 20/0.4 kV, 0.4–1.0 MVA | [EI-NVL] NG15913/12/11, L-NG-T-2-1; 0.4 and 0.63 interpolated | 13.6 k, 18.6 k, 26.2 k | 2022 SEK |
| transformer 20/0.4 kV, 1.6, 2.5 MVA | extrapolated, **assumed** | 36.3 k, 49.6 k | – |
| cable 11 kV, 95–400 mm², per km | [EI-NVL] NG14624/25/26, L-NR-KL-1-2 | 48.4 k … 78.2 k | 2022 SEK |
| cable 20 kV, 95–400 mm², per km | [EI-NVL] NG14614/15/16, L-NR-KL-2-2 | 52.2 k … 84.9 k | 2022 SEK |
| cable 33 kV, 150–400 mm², per km | [EI-NVL] L-NR-KL-5-1/2/4 | 133.5 k … 157.1 k | 2022 SEK |
| cable 33 kV, 95 mm², per km | derived from the 150 mm² row, **assumed** | 123.6 k | – |
| switchgear bay, lowest rating at 11/20/33/110/132 kV | [EI-NVL] bay + control equipment | 40.2 k / 45.8 k / 69.0 k / 343.3 k / 343.3 k | 2022 SEK |
| switchgear bay, higher ratings | cited bay × shipped ratio, **assumed** | e.g. 87.7 k (33 kV, 40 kA) | – |
| capacitor bank 11/20 kV | [EI-NVL] R-NR-KB-1-1 + R-NR-KB-1-2 × Mvar | 29.2 k + 4.6 k/Mvar | 2022 SEK |
| capacitor bank 33 kV | [EI-NVL] R-NR-KB-2-1 + R-NR-KB-2-2 × Mvar | 41.6 k + 5.3 k/Mvar | 2022 SEK |
| shunt reactor, STATCOM | none usable, **assumed** (shipped values kept) | as shipped | – |
| fixed O&M, substation equipment | [DEA-TRANS] Table 13 | 1.5 %/a | – |
| fixed O&M, MV cables and 20/0.4 kV transformers | [DEA-DIST] notes L, Q | 0.51 %/a | – |
| fixed O&M, STATCOM | **assumed** (shipped) | 3 %/a | – |
| lifetime, transformers, cables, switchgear | [DEA-DIST] note C | 40 a | – |
| lifetime, compensation | **assumed** (shipped) | 25 / 30 / 20 a | – |

## Every capex, entry by entry

Notes are as written in the proposal file. "before" is the shipped placeholder (labelled 2026 money); "now" is 2025 money, so the two columns differ by method as well as by price year.

#### transformers

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| TR_132_33_20 | 1,100,000 | 1,309,800 | literature | [EI-NVL] R-NR-T-5-2, 123-145/36-52 kV 20 MVA, 12632363 SEK (2022) -> EUR 2025 |
| TR_132_33_25 | 1,300,000 | 1,351,000 | literature | [EI-NVL] R-NR-T-5-3, 123-145/36-52 kV 25 MVA, 13029601 SEK (2022) -> EUR 2025 |
| TR_132_33_31p5 | 1,500,000 | 1,403,200 | literature | [EI-NVL] 123-145/36-52 kV, log-log interpolation between R-NR-T-5-4 (30 MVA, 13438444 SEK) and R-NR-T-5-5 (40 MVA, 14006581 SEK), exponent 0.144: 13533150 SEK (2022) -> EUR 2025 |
| TR_132_33_40 | 1,800,000 | 1,452,300 | literature | [EI-NVL] R-NR-T-5-5, 123-145/36-52 kV 40 MVA, 14006581 SEK (2022) -> EUR 2025 |
| TR_132_33_50 | 2,100,000 | 1,546,500 | literature | [EI-NVL] 123-145/36-52 kV, log-log interpolation between R-NR-T-5-5 (40 MVA, 14006581 SEK) and R-NR-T-5-6 (63 MVA, 15920189 SEK), exponent 0.282: 14916001 SEK (2022) -> EUR 2025 |
| TR_132_33_63 | 2,500,000 | 1,650,700 | literature | [EI-NVL] R-NR-T-5-6, 123-145/36-52 kV 63 MVA, 15920189 SEK (2022) -> EUR 2025 |
| TR_132_33_80 | 3,000,000 | 1,878,200 | literature | [EI-NVL] R-NR-T-5-7, 123-145/36-52 kV 80 MVA, 18114444 SEK (2022) -> EUR 2025 |
| TR_132_33_100 | 3,500,000 | 2,199,200 | literature | [EI-NVL] R-NR-T-5-8, 123-145/36-52 kV 100 MVA, 21210929 SEK (2022) -> EUR 2025 |
| TR_110_20_16 | 800,000 | 794,600 | literature | [EI-NVL] R-NR-T-4-1, 123-145/12-24 kV 16 MVA, 7664165 SEK (2022) -> EUR 2025 |
| TR_110_20_20 | 950,000 | 939,000 | literature | [EI-NVL] R-NR-T-4-2, 123-145/12-24 kV 20 MVA, 9056806 SEK (2022) -> EUR 2025 |
| TR_110_20_25 | 1,100,000 | 1,000,400 | literature | [EI-NVL] R-NR-T-4-3, 123-145/12-24 kV 25 MVA, 9648928 SEK (2022) -> EUR 2025 |
| TR_110_20_31p5 | 1,300,000 | 1,108,900 | literature | [EI-NVL] 123-145/12-24 kV, log-log interpolation between R-NR-T-4-4 (30 MVA, 10502113 SEK) and R-NR-T-4-5 (40 MVA, 11692751 SEK), exponent 0.373: 10695146 SEK (2022) -> EUR 2025 |
| TR_110_20_40 | 1,550,000 | 1,212,300 | literature | [EI-NVL] R-NR-T-4-5, 123-145/12-24 kV 40 MVA, 11692751 SEK (2022) -> EUR 2025 |
| TR_110_20_50 | 1,800,000 | 1,349,800 | literature | [EI-NVL] 123-145/12-24 kV, log-log interpolation between R-NR-T-4-5 (40 MVA, 11692751 SEK) and R-NR-T-4-6 (63 MVA, 14550720 SEK), exponent 0.481: 13018702 SEK (2022) -> EUR 2025 |
| TR_110_20_63 | 2,150,000 | 1,508,700 | literature | [EI-NVL] R-NR-T-4-6, 123-145/12-24 kV 63 MVA, 14550720 SEK (2022) -> EUR 2025 |
| TR_33_11_2p5 | 160,000 | 244,100 | assumed | outside the source's range (Ei 24-36/12 kV: 4-25 MVA): power law fitted to its six rows, 1334738 x S^0.619 SEK (2022), extrapolated, -> EUR 2025 |
| TR_33_11_4 | 220,000 | 352,100 | literature | [EI-NVL] R-NR-T-1-1, 24-36/12 kV 4 MVA, 3396373 SEK (2022) -> EUR 2025 |
| TR_33_11_6p3 | 300,000 | 428,400 | literature | [EI-NVL] R-NR-T-1-2, 24-36/12 kV 6.3 MVA, 4131638 SEK (2022) -> EUR 2025 |
| TR_33_11_10 | 420,000 | 492,600 | literature | [EI-NVL] R-NR-T-1-3, 24-36/12 kV 10 MVA, 4750625 SEK (2022) -> EUR 2025 |
| TR_33_11_16 | 600,000 | 856,500 | literature | [EI-NVL] R-NR-T-1-4, 24-36/12 kV 16 MVA, 8260262 SEK (2022) -> EUR 2025 |
| TR_33_11_20 | 700,000 | 781,800 | literature | [EI-NVL] R-NR-T-1-5, 24-36/12 kV 20 MVA, 7540068 SEK (2022) -> EUR 2025 |
| TR_33_11_25 | 800,000 | 1,134,300 | literature | [EI-NVL] R-NR-T-1-6, 24-36/12 kV 25 MVA, 10940180 SEK (2022) -> EUR 2025 |
| TR_33_11_31p5 | 950,000 | 1,172,600 | assumed | outside the source's range (Ei 24-36/12 kV: 4-25 MVA): power law fitted to its six rows, 1334738 x S^0.619 SEK (2022), extrapolated, -> EUR 2025 |
| TR_33_11_40 | 1,100,000 | 1,359,600 | assumed | outside the source's range (Ei 24-36/12 kV: 4-25 MVA): power law fitted to its six rows, 1334738 x S^0.619 SEK (2022), extrapolated, -> EUR 2025 |
| TR_20_0p4_0p4 | 20,000 | 13,600 | literature | [EI-NVL] 24/0.4 kV, log-log interpolation between NG15913 (315 kVA, 114525 SEK) and NG15912 (500 kVA, 148100 SEK), exponent 0.556: 130807 SEK (2022) -> EUR 2025 |
| TR_20_0p4_0p63 | 26,000 | 18,600 | literature | [EI-NVL] 24/0.4 kV, log-log interpolation between NG15912 (500 kVA, 148100 SEK) and NG15911 (800 kVA, 217721 SEK), exponent 0.820: 178996 SEK (2022) -> EUR 2025 |
| TR_20_0p4_1p0 | 35,000 | 26,200 | literature | [EI-NVL] L-NG-T-2-1, 24/0.4 kV 1000 kVA, 252237 SEK (2022) -> EUR 2025 |
| TR_20_0p4_1p6 | 48,000 | 36,300 | assumed | outside the source's range (Ei 24/0.4 kV: 315-1250 kVA): power law fitted to its five rows, 251558 x S^0.702 SEK (2022, S in MVA), extrapolated, -> EUR 2025 |
| TR_20_0p4_2p5 | 65,000 | 49,600 | assumed | outside the source's range (Ei 24/0.4 kV: 315-1250 kVA): power law fitted to its five rows, 251558 x S^0.702 SEK (2022, S in MVA), extrapolated, -> EUR 2025 |

#### cables

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| CB_33_AL95 | 90,000/km | 123,600/km | assumed | Ei lists no three-core 95 mm2 cable at 36 kV: L-NR-KL-5-1 (3x150, 1287680 SEK/km) x the 24 kV 95/150 ratio 0.9255 (NG14614/NG14615) = 1191714 SEK/km (2022) -> EUR 2025 |
| CB_33_AL150 | 110,000/km | 133,500/km | literature | [EI-NVL] L-NR-KL-5-1, buried cable, rural normal ground, 36 kV 3x150 mm2, 1287680 SEK/km (2022) -> EUR 2025 |
| CB_33_AL240 | 140,000/km | 137,000/km | literature | [EI-NVL] L-NR-KL-5-2, buried cable, rural normal ground, 36 kV 3x240 mm2, 1321465 SEK/km (2022) -> EUR 2025 |
| CB_33_AL400 | 190,000/km | 157,100/km | literature | [EI-NVL] L-NR-KL-5-4, buried cable, rural normal ground, 36 kV 3x400 mm2, 1514718 SEK/km (2022) -> EUR 2025 |
| CB_20_AL95 | 70,000/km | 52,200/km | literature | [EI-NVL] NG14614, buried cable, rural normal ground, 24 kV 3x95 mm2, 503516 SEK/km (2022) -> EUR 2025 |
| CB_20_AL150 | 85,000/km | 56,400/km | literature | [EI-NVL] NG14615, buried cable, rural normal ground, 24 kV 3x150 mm2, 544063 SEK/km (2022) -> EUR 2025 |
| CB_20_AL240 | 110,000/km | 62,900/km | literature | [EI-NVL] NG14616, buried cable, rural normal ground, 24 kV 3x240 mm2, 606267 SEK/km (2022) -> EUR 2025 |
| CB_20_AL400 | 150,000/km | 84,900/km | literature | [EI-NVL] L-NR-KL-2-2, buried cable, rural normal ground, 24 kV 3x400 mm2, 818922 SEK/km (2022) -> EUR 2025 |
| CB_11_AL95 | 55,000/km | 48,400/km | literature | [EI-NVL] NG14624, buried cable, rural normal ground, 12 kV 3x95 mm2, 466377 SEK/km (2022) -> EUR 2025 |
| CB_11_AL150 | 65,000/km | 51,300/km | literature | [EI-NVL] NG14625, buried cable, rural normal ground, 12 kV 3x150 mm2, 495118 SEK/km (2022) -> EUR 2025 |
| CB_11_AL240 | 85,000/km | 57,200/km | literature | [EI-NVL] NG14626, buried cable, rural normal ground, 12 kV 3x240 mm2, 551762 SEK/km (2022) -> EUR 2025 |
| CB_11_AL400 | 115,000/km | 78,200/km | literature | [EI-NVL] L-NR-KL-1-2, buried cable, rural normal ground, 12 kV 3x400 mm2, 754518 SEK/km (2022) -> EUR 2025 |

#### capacitor banks

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| CAP_33_5M | 90,000 | 68,100 | literature | [EI-NVL] R-NR-KB-2-1 base cost 401118 SEK + R-NR-KB-2-2 51042 SEK/Mvar x 5 Mvar = 656328 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_33_10M | 150,000 | 94,500 | literature | [EI-NVL] R-NR-KB-2-1 base cost 401118 SEK + R-NR-KB-2-2 51042 SEK/Mvar x 10 Mvar = 911538 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_33_20M | 280,000 | 147,400 | literature | [EI-NVL] R-NR-KB-2-1 base cost 401118 SEK + R-NR-KB-2-2 51042 SEK/Mvar x 20 Mvar = 1421958 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_20_2M | 50,000 | 38,400 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 2 Mvar = 370176 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_20_5M | 90,000 | 52,200 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 5 Mvar = 503331 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_20_10M | 150,000 | 75,200 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 10 Mvar = 725256 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_11_1M | 30,000 | 33,800 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 1 Mvar = 325791 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_11_2p5M | 50,000 | 40,700 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 2.5 Mvar = 392368 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |
| CAP_11_5M | 85,000 | 52,200 | literature | [EI-NVL] R-NR-KB-1-1 base cost 281406 SEK + R-NR-KB-1-2 44385 SEK/Mvar x 5 Mvar = 503331 SEK (2022) -> EUR 2025; bay and control equipment not included (the bay is priced as switchgear) |

#### shunt reactors

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| SR_33_5M | 150,000 | 150,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_33_10M | 250,000 | 250,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_33_20M | 450,000 | 450,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_20_2M | 90,000 | 90,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_20_5M | 150,000 | 150,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_20_10M | 250,000 | 250,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_11_1M | 60,000 | 60,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_11_2p5M | 100,000 | 100,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |
| SR_11_5M | 160,000 | 160,000 | assumed | no source found for a medium-voltage shunt reactor; unchanged. [NEP-2021] prices a 380 kV 100 Mvar reactor at 21 kEUR/Mvar (mid-2020) |

#### statcoms

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| SC_33_5M | 450,000 | 450,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_33_10M | 800,000 | 800,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_33_20M | 1,500,000 | 1,500,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_20_2M | 250,000 | 250,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_20_5M | 500,000 | 500,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_20_10M | 850,000 | 850,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_11_1M | 150,000 | 150,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_11_2p5M | 300,000 | 300,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |
| SC_11_5M | 500,000 | 500,000 | assumed | no source found for a medium-voltage STATCOM; unchanged. [NEP-2021] prices a 380 kV 100 Mvar STATCOM at 130 kEUR/Mvar (mid-2020) |

#### switchgear

| id | capex before (2026 placeholder) | capex now (EUR 2025) | tag | from |
|---|---:|---:|---|---|
| SG_132_31p5 | 450,000/bay | 343,300/bay | literature | [EI-NVL] R-NR-SF-8-5 breaker feeder bay 3077299 SEK + R-NR-KF-2-1 its control equipment 233524 SEK = 3310823 SEK per bay (2022) -> EUR 2025; Ei does not price by short-circuit rating |
| SG_132_40p0 | 517,000/bay | 394,400/bay | assumed | the 31.5 kA bay's cited cost x the library's earlier 40/31.5 kA ratio 1.1489 (an assumption: Ei does not price by short-circuit rating) |
| SG_110_31p5 | 400,000/bay | 343,300/bay | literature | [EI-NVL] R-NR-SF-7-5 breaker feeder bay 3077299 SEK + R-NR-KF-2-1 its control equipment 233524 SEK = 3310823 SEK per bay (2022) -> EUR 2025; Ei does not price by short-circuit rating |
| SG_110_40p0 | 460,000/bay | 394,800/bay | assumed | the 31.5 kA bay's cited cost x the library's earlier 40/31.5 kA ratio 1.1500 (an assumption: Ei does not price by short-circuit rating) |
| SG_33_25p0 | 81,000/bay | 69,000/bay | literature | [EI-NVL] R-NR-SF-3-3 breaker feeder bay 431735 SEK + R-NR-KF-2-1 its control equipment 233524 SEK = 665259 SEK per bay (2022) -> EUR 2025; Ei does not price by short-circuit rating |
| SG_33_31p5 | 90,000/bay | 76,700/bay | assumed | the 25 kA bay's cited cost x the library's earlier 31.5/25 kA ratio 1.1111 (an assumption: Ei does not price by short-circuit rating) |
| SG_33_40p0 | 103,000/bay | 87,700/bay | assumed | the 25 kA bay's cited cost x the library's earlier 40/25 kA ratio 1.2716 (an assumption: Ei does not price by short-circuit rating) |
| SG_20_25p0 | 54,000/bay | 45,800/bay | literature | [EI-NVL] R-NR-SF-2-3 breaker feeder bay 365156 SEK + R-NR-KF-1-1 its control equipment 76187 SEK = 441343 SEK per bay (2022) -> EUR 2025; Ei does not price by short-circuit rating |
| SG_20_31p5 | 60,000/bay | 50,900/bay | assumed | the 25 kA bay's cited cost x the library's earlier 31.5/25 kA ratio 1.1111 (an assumption: Ei does not price by short-circuit rating) |
| SG_20_40p0 | 69,000/bay | 58,500/bay | assumed | the 25 kA bay's cited cost x the library's earlier 40/25 kA ratio 1.2778 (an assumption: Ei does not price by short-circuit rating) |
| SG_11_25p0 | 40,000/bay | 40,200/bay | literature | [EI-NVL] R-NR-SF-1-3 breaker feeder bay 311894 SEK + R-NR-KF-1-1 its control equipment 76187 SEK = 388081 SEK per bay (2022) -> EUR 2025; Ei does not price by short-circuit rating |
| SG_11_31p5 | 45,000/bay | 45,200/bay | assumed | the 25 kA bay's cited cost x the library's earlier 31.5/25 kA ratio 1.1250 (an assumption: Ei does not price by short-circuit rating) |
| SG_11_40p0 | 52,000/bay | 52,300/bay | assumed | the 25 kA bay's cited cost x the library's earlier 40/25 kA ratio 1.3000 (an assumption: Ei does not price by short-circuit rating) |

## What the study buys at these prices

The C8 least-cost selection was re-run on the solved **Data Center Energy Hub** template, as in plan C8:
- the template was built from `project_templates/eh_templates.build_eh_datacenter()` and solved with HiGHS (`n.optimize`);
- the campus was drafted from the solved network (`draft_from_project`), then `prepare_campus`, `rank_campus(k=3)` and `size_campus` were run (margin 0.2, N-1 on, profile `eu_rfg_dcc_ce`, pf 0.95);
- `invest_campus` was run once with the shipped library and once with the proposal, on the same run inputs.

The shipped run reproduces the plan's C8 table exactly. The investment step took 6.5 s with the shipped library and 5.9 s with the proposal.

| need | bought (both runs) | capex, shipped placeholders | capex, reference costs | annualised, shipped | annualised, reference |
|---|---|---:|---:|---:|---:|
| transformer GRID_IMPORT | 1 × TR_132_33_63 | 2,500,000 | 1,650,700 | 225,023 | 148,578 |
| transformer SITE_TRANSFO | 3 × TR_33_11_25 | 2,400,000 | 3,402,900 | 216,022 | 306,292 |
| reactive | none needed | 0 | 0 | 0 | 0 |
| switchgear GRID (132 kV) | 2 bays SG_132_31p5 | 900,000 | 686,600 | 76,508 | 61,800 |
| switchgear DC_MV (33 kV) | 11 bays SG_33_25p0 | 891,000 | 759,000 | 75,743 | 68,317 |
| switchgear IT_BUS (11 kV) | 5 bays SG_11_25p0 | 200,000 | 201,000 | 17,002 | 18,092 |
| **total** | | **6,891,000** | **6,700,200** | **610,298** | **603,079** |

All money is EUR. The shipped column is in the placeholders' 2026 money; the reference column is in 2025 money. Both are annualised at the library's 7 %.

- **The choices do not change.** The 33/11 kV group is still three 25 MVA units. With N-1, one survivor must carry 46.7 MVA. The largest cited 33/11 kV unit is 25 MVA, and the extrapolated 31.5 and 40 MVA units are too small to survive alone. The cheapest compliant group is three 25 MVA units at either set of prices.
- **The cost moves within the total.** The 132/33 kV transformer is 34 % cheaper (Ei 63 MVA: 15.9 MSEK). The three 33/11 kV transformers are 42 % dearer (Ei 25 MVA: 10.9 MSEK each). Switchgear is 17 % cheaper overall (EUR 1.65 M against 1.99 M). The electrical cost falls 2.8 % in capex and 1.2 % a year.
- Every check passes with the measures, re-solved; there was no escalation and nothing is unresolved.
- **The template has no cables and needs no compensation**, so the cited cable and capacitor-bank prices, and the still-assumed reactor and STATCOM prices, are not exercised by this run.

## The `literature` tag

- **A new tag.** None of the existing tags fits. `datasheet` is a manufacturer's figure for the item, `measured` is a measurement, and `assumed` is "a typical figure written down, not a figure read from a text". A figure read from a regulator's catalogue is none of these. `gridspine/templates/campus_assets.py` now accepts `literature`.
- **Only on a cost.** `literature` is allowed on `capex_eur`, `capex_eur_per_km`, `opex_frac`, `lifetime_a` and `discount_rate`, not on a rating.
  - The investment copies a bought item's ratings (MVA, kV, impedances, Mvar, kA) into the invested campus file (`static/campus_invest.py`).
  - The campus file's tags are measured | datasheet | assumed, so a `literature` rating would make the invested campus file unreadable.
  - The loader refuses it with the entry and the field named.
- **`illustrative` is false for `literature`.** `extra_owner_assets` (`campus_electrical_service.py`) sets `meta.illustrative = (tag == "assumed")`, so a `literature` cost reaches the investment case with `provenance: "literature"` and `illustrative: false`. This is deliberate.
  - The investment case's own vocabulary decides it. The defaults pack's README defines `illustrative = true` as "the figure is not taken from a cited source; it was chosen at a plausible order of magnitude", and gives its DEA and PyPSA-Eur catalogue rows `false`. Those are reference values, not quotes, too.
  - A cited reference value is therefore not illustrative in that sense. That it is not a quote is carried by `provenance` and by the texts below. No code change was needed in the service; a test pins the behaviour, and the docstring now says it.
- **Texts.**
  - The panel's lines ("every cost in the shipped library is an assumed placeholder") and the service's investment note stay true, because the shipped library is unchanged.
  - The copilot's tool texts (`campus_get_library`, `campus_set_library`, `campus_get_investment` in `harness/catalogue.py`) now name the tag. A cost read from a cited publication is `literature`, with the reference in its note. Literature values are "cited reference values, still not quotes".
  - When the shipped library adopts these values, the panel's two lines (`CampusLibrarySection.tsx`, `CampusInvestmentSection.tsx`) and `INVESTMENT_NOTES` must change with it, for example to "costs are reference values from cited public sources (tag literature) or assumed where none was found; neither is a quote".

## Adopting these values (for the pack owner)

Every path to adopting these values touches investment-case files: `services/library/*`, the pack-hash fixture, and the parity test, which compares the live campus YAML with the 2026-10-07 pack. So this branch stops at the proposal. The change, for the investment-case session:

1. **A new pack version** `pypsa-gui/backend/services/library/defaults_pack/versions/2026-10-12/` (or the date it lands), copied from 2026-10-07. The 2026-10-07 version is never edited.
   - `values.csv`: the 2026-10-05 rows unchanged. Then, for each of the 81 campus entries, the three rows (`overnight`, `lifetime`, `fom_share`) take the proposal's values, with these columns:
     - `currency_year = 2025`;
     - `source` = the note's citation key expanded, for example `"Ei Normvärdeslista elnät 2024-2027 (R-NR-T-5-6), converted"`, or `"assumed (gridspine campus_assets.yaml)"` for an `assumed` value;
     - `source_url` = the key's URL;
     - `illustrative` = `false` for `literature` and `true` for `assumed`;
     - `note` = the proposal's note.
   - `manifest.json`: `seeded_from.campus_equipment.sha256` is the sha256 of the new shipped YAML, and `price_year` is 2025.
   - `tests/fixtures/defaults_pack/pack_hashes.json`: the new version's hash.
   - `smoke/check_bundle.py`: the new manifest.
   - `tests/test_energy_hub_class_c_authoring.py`: the new manifest in its fake root.
2. **The parity test** compares the live YAML with the *latest* pack version, or with a version pinned to the YAML's sha256, rather than with 2026-10-07 by name. Its other asserts that pin 2026 money (`currency_year == 2026`, `price_year == 2026`) and the placeholder source string move to the new version's values. The 2026-10-07 assertions stay for 2026-10-07.
3. **Then, on the campus side, in one commit with the new pack version:**
   - copy the proposal over `gridspine/templates/data/campus_assets.yaml`, with its header rewritten from "proposal" to "shipped";
   - change `test_no_cost_claims_to_be_anything_but_assumed` (a test of the shipped data) to the proposal's tag rules;
   - update the panel and service texts above;
   - update the pinned shipped numbers in `pypsa-gui/backend/tests/test_campus_electrical_service.py`: `upfront_per_unit == 2_500_000` for `TR_132_33_63`, and `price_year == 2026`. These are data, not logic.
   - The IC's route that builds a case must convert or refuse money that is not in the case's year (its Known limit 2); the new library is in 2025 money.

## Limits

- **Swedish standard costs.** Ei's figures are what a Swedish network company may book for an asset, set by a consultant from an industry cost catalogue (EBR) and expert judgement. They are not a market survey. Price levels elsewhere in Europe differ.
- **Inflation.** The general HICP probably understates 2022–2025 equipment prices (see Conversion). The values are better read as a floor than as a central estimate.
- **Ground type.** Ground type is the biggest single cost driver for cables. The choice of rural normal ground is a judgement; a campus in town would be about twice as dear.
- **Short-circuit rating.** Ei does not distinguish switchgear by rating, so only the lowest rating at each voltage is cited.
- **Compensation.** Reactors and STATCOMs at MV, and all compensation lifetimes, have no source here. A future pass could look for a DSO's unit-cost list that covers MV compensation, for example Ofgem's RIIO-ED2 unit costs, which were not reached in this pass.
