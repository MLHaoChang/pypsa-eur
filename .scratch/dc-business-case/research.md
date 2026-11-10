# Data-centre business case: revenue, cost and investor metrics

**Summary**
1. There are three revenue models. Retail colocation is priced per cabinet or per kW-month, at about $2.4–2.7k per cabinet-month at Equinix [8] and $269–282 per kW-month for 0–1 MW deals at Digital Realty [12]. Wholesale/hyperscale leases run about $180–205 per kW-month, last 9–10 years and are mostly net, with power passed through [12][11]. GPU cloud is sold per GPU-hour, at about $4.6–6 for an H100 and $7.7–14 for a B200 on demand [16][17][18], but 98% of CoreWeave revenue comes from take-or-pay contracts [1].
2. Power is mostly a pass-through for landlords. At Digital Realty, utilities are the largest expense and "the vast majority" is passed through [11]. Equinix bills metered power as variable consideration [8].
3. Margins differ widely. Adjusted EBITDA margin was about 49% at Equinix in 2025 [8], about 55% at Digital Realty (derived from [11][12]) and 60% at CoreWeave [2]. CoreWeave still lost $1.17 bn in 2025, because depreciation (48% of revenue) and interest (24%) consumed its EBITDA [1][2].
4. Hyperscalers depreciate servers over 5–6 years: Microsoft 2–6, Alphabet 6, Amazon 5–6 and Meta 5–5.5 [3][4][5][6][7]. CoreWeave uses 6 years for GPUs [1]. Shell and core costs about $10.7 M/MW in 2025 and $11.3 M/MW in 2026, and an AI fit-out costs up to $25 M/MW more [22].
5. A model needs these inputs: sellable IT kW, a price basis (kW-month or GPU-hour), occupancy or utilisation ramp, a power pass-through flag, IT capex and its life, and non-energy opex as % of revenue or $/kW. An hourly IT-MW profile drives power cost directly. It also drives revenue under metered or GPU-hour pricing, but not under contracted-kW pricing.

All "derived" numbers are my arithmetic on the cited figures and are labelled as such. Dates are filing or publication dates.

---

## 1. Revenue models

### 1a. GPU / compute-hour rental (AI cloud)

How it is sold. CoreWeave sells "either through committed contracts, which are take-or-pay, or on-demand, which are pay-as-you-go". Committed contracts made up more than 98%, 96% and 88% of revenue in 2025, 2024 and 2023. The weighted-average contract duration was about 5 years, and remaining performance obligations were $60.7 bn at 31 Dec 2025. Committed contracts "typically include a prepayment", and the weighted-average prepayment was 15–25% of total contract value [1]. CoreWeave finances its build "through asset-level debt supported by take-or-pay customer contracts" [1].

List prices per GPU-hour (on demand unless stated):

| Provider (source, date) | H100 | H200 | B200 | Notes |
|---|---|---|---|---|
| Lambda, 8x instance [16] (prices effective 16 Nov 2026) | $4.59 | n/a | $7.69 | 1x: H100 $4.89, B200 $7.99 |
| Lambda 1-Click Clusters, 2 wk–1 yr [16] | $5.54–6.16 | n/a | $8.87–9.86 | Commitments longer than 1 yr: "talk to our team" |
| CoreWeave 8-GPU instance [17] (accessed Nov 2026) | $6.16 (=$49.24/8, derived) | $6.31 | $8.60 | "up to 60% discounts" for committed usage |
| AWS EC2 Capacity Blocks, US East [18] (accessed Nov 2026) | $5.97 | $6.87–7.90 | $14.21 | GB200 UltraServer: $10.58 per GPU-hour |

Price risk. On 5 June 2025 AWS cut P5 (H100) prices by up to 45% and P4d (A100) prices by up to 33% [19]. Treat GPU-hour prices as a declining curve, not a flat input.

Power per GPU (to tie GPU-hours to IT MW):

| System | Max system power | GPUs | kW per GPU (derived) | Source |
|---|---|---|---|---|
| H100 SXM (chip) | up to 700 W TDP | 1 | 0.70 | [13] |
| DGX H100 | 10.2 kW max | 8 | 1.28 | [14] |
| DGX B200 | ~14.3 kW max | 8 | 1.79 | [15] |
| DGX GB200 NVL72 rack | ~120 kW | 72 | 1.67 | [15b] |

NVIDIA's SuperPOD guide states that DGX H100 systems "operate at or near peak utilization continuously when running AI workloads". Its expected average power and expected peak power are both 10.2 kW, so a nameplate-equals-load assumption is defensible for training clusters [14].

Derived rule of thumb: 1 MW of IT load holds about 780 H100s (DGX) or about 560–600 Blackwell GPUs. At $2–6 per GPU-hour and 70–90% sold utilisation, that is about $10–37 M of revenue per IT-MW-year, before the multi-year contract discount. CoreWeave reported $5.13 bn of revenue on 360 → 850 MW of active power (average ≈ 605 MW), which is about $8.5 M per active MW-year (derived from [1]). The gap shows that realised contract prices sit well below list prices, and that some active MW was still ramping.

Not found at a primary source: realised fleet utilisation or average contracted $/GPU-hour for any GPU cloud. CoreWeave does not disclose either [1].

### 1b. Retail colocation (per cabinet or per kW-month)

Equinix customers are "generally billed on a fixed and recurring basis each month for the duration of their contract, which is generally one to five years in length, and thereafter automatically renews in one-year increments". Recurring revenue was more than 90% of total revenue (94% in 2025) [8].

| Equinix IBX portfolio, 31 Dec 2025 [8] | Cabinet capacity | Billed | Utilisation | MRR per cabinet |
|---|---|---|---|---|
| Americas | 157,400 | 123,700 | 79% | $2,694 |
| EMEA | 141,300 | 107,200 | 76% | $2,418 |
| Asia-Pacific | 93,600 | 68,400 | 73% | $2,355 |

Digital Realty's Q4 2025 bookings in the 0–1 MW band (mainly colocation) had a GAAP base rent of $282 per kW-month, an initial stabilised cash rent of $278, and a weighted lease term of 4.2 years [12]. One-off installation fees are "deferred and recognized ratably over the contract term" [8].

### 1c. Wholesale / hyperscale leases

| Metric | Value | Source |
|---|---|---|
| DLR >1 MW signed Q4 2025: GAAP rent | $181 per kW-month (LTM $203) | [12] |
| DLR >1 MW: initial stabilised cash rent | $154 per kW-month (LTM $172) | [12] |
| DLR >1 MW: estimated opex | $46 per kW-month (LTM $51) | [12] |
| DLR >1 MW: net rent | $135 per kW-month (LTM $152) | [12] |
| DLR >1 MW: weighted lease term | 8.9 yrs (LTM 10.0), excluding renewals | [12] |
| DLR renewal spreads Q4 2025 | +6.1% cash / +12.0% GAAP | [12] |
| CBRE N. America outlook, 250+ kW, Tier III | "$200+ per kW per month"; vacancy 1.6%; 74.3% of capacity under construction preleased | [21] (19 Aug 2025) |
| Public 15-yr AI lease example | triple-net, 3%/yr escalator, 3×5-yr renewals | [26] (secondary; not verified at the filing) |

Structure. Hyperscalers "frequently commit to triple-net leases" [27]. Digital Realty states that "the majority of our rental expenses, including common area maintenance, real estate taxes and insurance, are recovered from our customers". It also says inflation risk remains when inflation "exceeds the annual rent escalation percentages" [11]. A typical escalator of 2–3% a year appears only in secondary commentary [26] and was not verified at a primary source.

### 1d. Power pass-through and PUE

- Digital Realty: "With regard to utilities expense, which is our largest expense category, the vast majority of the expense is passed directly through to our customers" [11]. It controls costs "by negotiating expense pass-through provisions … including power costs and certain capital expenditure" [11].
- Equinix: remaining performance obligations exclude "variable consideration … such as the usage of metered power" [8]. Its bookings metric excludes "the impact of power price adjustments", so power is repriced separately from space [8].
- How PUE is charged. Filings confirm metered or pass-through billing but do not publish the formula. The usual industry practice is IT kWh × tariff × PUE (or a fixed PUE factor), but no primary source confirming this was found. Model it as a flag: (a) bundled, where power is in rent and the operator keeps the margin or risk; or (b) pass-through, where IT kWh × PUE_contract × (tariff + adder), so the operator gains or loses on (PUE_contract − PUE_actual).
- Reference PUE: the 2025 Uptime survey weighted average was 1.54, and data centres of 20 MW and above averaged 1.44 [9].

### 1e. Interconnection and services

Equinix 2025 revenue split: colocation $6,475 M (70%), interconnection $1,655 M (18%), managed infrastructure $466 M (5%), other $143 M and non-recurring $478 M [8]. Equinix has more than 500,000 interconnections, which implies about $276 per interconnection-month (derived, using the year-end count) [8]. Non-recurring revenue covers installation and professional services [8]. Digital Realty's Q4 2025 bookings included $19 M of annualised interconnection revenue (secondary summary of [12]). Wholesale landlords earn little of this; it mostly comes from retail and interconnection-hub facilities.

### 1f. Lease-up and ramp

- Equinix reports regional cabinet utilisation of 73–79% (table above) for a mature portfolio [8]. Its management targets stabilisation about 2–4 years after a site is ready for service. That figure comes from earnings-call summaries [24] and is not in the 10-K.
- CoreWeave's contracted power (3.1 GW) was 3.6× its active power (850 MW) at year-end 2025. Contracted capacity is energised in phases over several years [1].
- CBRE reports that 74.3% of North American capacity under construction is preleased [21]. A pre-let wholesale build can therefore be modelled with near-full occupancy from handover, phased by data hall. A speculative retail build ramps over about 3–5 years to 75–85% utilisation. This is an inference from [8][21][24] and is not a cited curve.

## 2. Costs and P&L lines beyond energy

### 2a. IT hardware useful life (primary filings)

| Company | Server / network life and history | Source |
|---|---|---|
| Microsoft | 4 → 6 yrs from FY2023 (July 2022 study); +$3.7 bn operating income in FY2023. The FY2026 10-K shows "servers and network equipment, two to six years" | [3] |
| Alphabet | Servers 4 → 6 yrs and some network equipment 5 → 6 yrs from 2023. FY2025 10-K: servers and network equipment "generally over a period of six years"; buildings 7–40 yrs | [4] |
| Amazon | Servers 5 → 6 yrs from 1 Jan 2024 (−$3.2 bn of D&A in 2024). A subset went 6 → 5 yrs from 1 Jan 2025 "due to the increased pace of technology development, particularly in … AI". About $920 M of accelerated depreciation in Q4 2024 for early retirements | [5][6] |
| Meta | 4 → 4.5 yrs (Q2 2022) → 5 yrs (Q4 2022) → 5.5 yrs from 1 Jan 2025. FY2025 table: "Five to 5.5 years" | [7][7b] |
| CoreWeave | Computing equipment 5 → 6 yrs from 1 Jan 2023. The 10-K warns that changes to useful-life assumptions "could significantly affect" results | [1] |
| Equinix | Core systems 3–40 yrs, buildings 12–60, leasehold improvements 12–40, personal property 3–10. Lives were revised in 2024–25 | [8] |
| Digital Realty | Buildings and improvements 5–39 yrs, machinery and equipment 7–15, furniture and fixtures 3–5 | [11] |

Model default: GPUs and servers 5 years (sensitivity 4–6). Network equipment 5–6 years. Mechanical and electrical plant 15–20 years (Digital Realty machinery 7–15 [11]). Shell 30–40 years.

### 2b. Capex per MW

| Item | Value | Source |
|---|---|---|
| Shell and core, global average | $10.7 M/MW (2025), forecast $11.3 M/MW (2026); $7.7 M in 2020 | JLL, 5 Jan 2026 [22] |
| AI tech fit-out (tenant scope) | "as much as $25 million per MW" | JLL [22] |
| N. America all-in greenfield, excluding IT | $8.9–23.3 M/MW, +21% vs Q4 2024 edition | Cushman & Wakefield 2026 guide, via trade press [23] (not verified at source) |
| GPU systems | Not found at a primary price list; NVIDIA publishes no DGX list prices | — |

### 2c. Operating cost structure (2025 10-Ks, % of revenue, derived)

| Line | Equinix [8] | Digital Realty [11] | CoreWeave [1] |
|---|---|---|---|
| Cost of revenue / property opex | 48.9% (depreciation, rent, utilities, bandwidth, staff, R&M, security) | 41.0% (rental property operating and maintenance; utilities largely recovered) | 28.3% (rent, power, depreciation of power systems, data-centre staff) |
| Property tax and insurance | inside cost of revenue | 3.6% | n/d |
| Technology and infrastructure | — | — | 57.1% (mostly server and network depreciation) |
| Sales and marketing | 9.8% | inside G&A | 2.8% |
| G&A | 20.0% | 9.3% | 12.7% |
| D&A (total) | ~$1.9 bn depreciation (~21%) | 31.0% | 47.8% |
| Interest | — | — | 24.0% |

Other lines worth a separate input:
- Bandwidth is named in Equinix's cost of revenues [8].
- Leasing commissions: Digital Realty's leasing cost was $1 per kW for >1 MW deals and $46 per kW for 0–1 MW deals in Q4 2025 [12].
- Rental concessions: about $2–3 per kW-month [12].
- SLA credits are booked as a revenue reduction and "historically … have not been significant" (Equinix) [8].
- Outage cost: one in five operators with a significant outage put its cost above $1 M [9].
- Working capital: GPU-cloud prepayments of 15–25% of total contract value [1]. Equinix defers installation fees [8].
- Recurring or maintenance capex is deducted in AFFO. Digital Realty guides $400–425 M for 2026 (recurring capex plus capitalised leasing costs) [12].

Not found at a primary source as a per-MW figure: staff headcount or cost per MW, and insurance per MW. Use the % of revenue lines above, or Digital Realty's estimated opex of $46–81 per kW-month [12], which bundles them.

## 3. Investor metrics

| Metric | Definition / benchmark | Source |
|---|---|---|
| Adjusted EBITDA margin | Equinix 2025: $4,530 M / $9,217 M = 49.1% (derived). Its definition excludes D&A, SBC, restructuring, impairments, transaction costs and gains on asset sales | [8] |
| | Digital Realty Q4 2025: $856.8 M / $1,634.7 M = 52% (derived); FY2026 guide $3.60–3.70 bn on $6.60–6.70 bn revenue ≈ 55% | [12] |
| | CoreWeave 2025: 60% (2024: 64%). Adjusted operating income margin was 13%, and the net loss margin was −23% | [2] |
| Revenue per MW | CoreWeave ≈ $8.5 M per active MW-year (derived). A wholesale lease at $181 per kW-month is ≈ $2.2 M per MW-year (derived) | [1][12] |
| FFO / AFFO (REITs) | NAREIT FFO, then AFFO adds back non-real-estate D&A, SBC and straight-line effects, and deducts recurring capex. Equinix AFFO was $3,761 M in 2025 | [8] |
| Development / stabilised yield | Digital Realty guides "average stabilized yields 10.0%+" on 2026 development capex of $3.25–3.75 bn. Earlier 2025 quarters were 11.6–12.5% (secondary) | [12][25] |
| | Equinix: stabilised retail assets earn about 25–27% cash-on-cash on gross PP&E, per earnings-call summaries (secondary, not in the 10-K) | [24] |
| Leverage | Digital Realty targets debt/Adjusted EBITDA of about 5.5x and fixed-charge cover above 3x. Actual Q4 2025: 4.9x and 4.5x | [11][12] |
| DSCR / covenants | Data-centre ABS cash traps at a 3-month DSCR below 1.35x (one deal) or 1.45x, with early amortisation below 1.25x. Senior interest reserves of about 6 months; LTV sweeps | [28] (trade press on KBRA/S&P/Fitch-rated deals) |
| Project finance | Lenders take a limited-recourse SPV with security over contracts and receivables, and test covenants at portfolio level. GPU or equipment debt has shorter tenors, "often up to five years", amortising with the refresh cycle | [20] |
| NPV / IRR / payback | Standard. The tool already computes them; apply them to unlevered free cash flow (EBITDA − tax − capex − ΔWC) and to equity flows after debt service | — |

## 4. Accounting notes

- Depreciation: every filer above uses straight-line depreciation over each asset class's useful life. A change in life is applied prospectively as a change in estimate [1][3][4][5][7]. Leasehold improvements are depreciated over the shorter of their life and the lease term [3][8][11].
- Lessor contracts (ASC 842 / 606): Equinix combines lease and non-lease components. The combined contract falls under Topic 842 "if the lease component is predominant" and Topic 606 otherwise, and "in general, customer contracts for data centers are accounted for under Topic 606" [8]. Digital Realty, a wholesale landlord, uses the 842 practical expedient to report lease and non-lease components "as a single lease component" in rental revenue, recognised straight-line [11]. Model implication: colocation is a service with ratable revenue; a wholesale lease earns straight-line rent, so GAAP rent differs from cash rent when escalators apply ([12] reports both).
- Lessee side: operators that rent buildings book right-of-use assets and lease liabilities. Digital Realty's weighted remaining terms are 12 years (operating leases) and 17 years (finance leases) [11]. Under IFRS 16 the lessee uses a single on-balance-sheet model [29]. Lessor classification under IFRS 16 was not verified on the cited page.
- Power pass-through: Digital Realty records "tenant recoveries" as revenue in the period the costs are incurred, which is gross presentation [11]. Equinix treats metered power as variable consideration [8]. Whether a given contract is gross or net depends on the principal-versus-agent assessment under ASC 606. No filer states net treatment. Model default: gross, with power revenue = recovered cost (+ adder), so EBITDA margin % falls as pass-through revenue grows.

## 5. Implications for the model

Minimal inputs:

| Group | Input | Default / range (source) |
|---|---|---|
| Capacity | Sellable IT kW (≤ grid kW / design PUE) | — |
| Business model | `colo_retail` \| `wholesale_lease` \| `gpu_cloud` | — |
| Price | $/kW-month (retail ~270–290; wholesale ~150–205) [12][21]; or $/GPU-hour (H100 ~2–6, B200 ~4–14) [16][17][18] | — |
| Escalator / price decay | Lease +2–3%/yr (unverified [26]); GPU price step cuts of up to −45% observed [19] | — |
| Contract | Term (retail 1–5 yrs [8]; wholesale ~9–10 [12]; GPU ~5 [1]); prepayment % (15–25% [1]) | — |
| Occupancy / utilisation ramp | Year-by-year % of sellable kW billed; mature 75–80% retail [8], ~100% pre-let wholesale | — |
| Power billing | `bundled` \| `pass_through` (+ contract PUE, $/MWh adder) [8][11] | — |
| Other revenue | Interconnection % of colo revenue (Equinix ≈ 26% of colocation, derived [8]); services % | — |
| IT capex | $/MW or $/GPU, life 5 yrs (4–6) [1][3–7], refresh at end of life | — |
| Facility capex | Shell and core $/MW (~10.7–11.3 M [22]), M&E life 15–20 yrs, shell 30–40 | — |
| Non-energy opex | Staff, R&M, insurance, property tax (~3.6% rev [11]), bandwidth, S&M (~3–10% [1][8]), G&A (~9–20% [1][8][11]), SLA credits | — |
| Financing | DSCR covenant / lock-up (1.25–1.45x [28]); separate GPU debt tenor ≤ 5 yrs [20] | — |

How the hourly IT-MW profile drives revenue:
- `wholesale_lease` / `colo_retail`: revenue = contracted kW × rate × 12 × occupancy(t) × (1+esc)^t. The load profile does not drive rent. It drives energy cost, and pass-through revenue = Σ_h IT_MW_h × PUE_contract × tariff_h (+ adder) when `pass_through`.
- `gpu_cloud`: GPUs = IT_kW_installed / kW_per_GPU (e.g. 1.28 for H100 DGX, 1.79 for B200 DGX [14][15]). Sold GPU-hours = Σ_h (IT_MW_h / IT_MW_installed) × GPUs, or GPUs × 8760 × utilisation. Revenue = sold GPU-hours × $/GPU-hour (blended between contracted and on-demand). Because DGX systems run near peak under load [14], hourly IT MW ÷ installed IT MW is a reasonable utilisation proxy.
- A generic fallback is utilised IT MWh × $/MWh-IT. That price can be derived from either basis: $181 per kW-month ≈ $248 per MWh-IT at 100% load; $4 per GPU-hour ÷ 1.28 kW ≈ $3,100 per MWh-IT (derived).
- P&L order: revenue lines → energy cost (existing) → non-energy opex lines → EBITDA (and adjusted EBITDA margin) → D&A by class (IT 5y, M&E 15–20y, shell 30–40y) → EBIT → interest → tax → net income. Then FCF → NPV/IRR, DSCR per period, payback, and stabilised yield on cost (stabilised EBITDA ÷ total capex).

Skipped / unverified:
- LBNL report PUE and utilisation tables (the PDF could not be fetched; only the DOE headline figures were used [10]).
- The IEA page itself returned 403. The IEA figures come from IEA text quoted in search results [30].
- The Iron Mountain 10-K had no usable data-centre pricing.
- Hyperscale escalator %, Equinix yields and the Cushman cost range rest on secondary sources, as flagged.
- Realised GPU utilisation and contracted $/GPU-hour are not publicly disclosed.

Context: US data centres used 176 TWh (4.4% of US electricity) in 2023, rising to 325–580 TWh by 2028 [10]. Global use was about 415 TWh in 2024, rising to about 945 TWh by 2030 in the IEA Base Case [30].

---

## References

1. CoreWeave, Inc., Form 10-K FY2025, SEC, filed 2026-03-02. https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231.htm
2. CoreWeave, Q4 2025 earnings press release (8-K Ex. 99.1), SEC, filed 2026-02-26. https://www.sec.gov/Archives/edgar/data/1769628/000176962826000094/coreweave4q25earningspress.htm
3. Microsoft Corp., Form 10-K FY2023 (filed 2023-07-27) and FY2026 (filed 2026-07-29), SEC. https://www.sec.gov/Archives/edgar/data/789019/000095017023035122/msft-20230630.htm ; https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm
4. Alphabet Inc., Form 10-K FY2023 (filed 2024-01-31) and FY2025 (filed 2026-02-05), SEC. https://www.sec.gov/Archives/edgar/data/1652044/000165204424000022/goog-20231231.htm ; https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm
5. Amazon.com, Inc., Form 10-K FY2024, SEC, filed 2025-02-07. https://www.sec.gov/Archives/edgar/data/1018724/000101872425000004/amzn-20241231.htm
6. Amazon.com, Inc., Form 10-K FY2025, SEC, filed 2026-02-06. https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm
7. Meta Platforms, Inc., Form 10-K FY2025, SEC, filed 2026-01-29. https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm
7b. Meta Platforms, Inc., Form 10-K FY2022, SEC, filed 2023-02-02. https://www.sec.gov/Archives/edgar/data/1326801/000132680123000013/meta-20221231.htm
8. Equinix, Inc., Form 10-K FY2025, SEC, filed 2026-02-11. https://www.sec.gov/Archives/edgar/data/1101239/000110123926000032/eqix-20251231.htm
9. Uptime Institute, "Global Data Center Survey 2025", Keynote Report 180, July 2025. https://datacenter.uptimeinstitute.com/rs/711-RIA-145/images/2025.Annual.Survey.Report.pdf
10. US DOE, "DOE Releases New Report Evaluating Increase in Electricity Demand from Data Centers" (LBNL 2024 report), 2024-12-20. https://www.energy.gov/articles/doe-releases-new-report-evaluating-increase-electricity-demand-data-centers
11. Digital Realty Trust, Inc., Form 10-K FY2025, SEC, filed 2026-02-13. https://www.sec.gov/Archives/edgar/data/1297996/000110465926015365/dlr-20251231x10k.htm
12. Digital Realty, Q4 2025 results and financial supplement (8-K Ex. 99.1), SEC, filed 2026-02-05. https://www.sec.gov/Archives/edgar/data/1297996/000110465926010887/dlr-20260205xex99d1.htm
13. NVIDIA, "H100 Tensor Core GPU" specifications, accessed 2026-11-10. https://www.nvidia.com/en-us/data-center/h100/
14. NVIDIA, "DGX SuperPOD Data Center Design (H100): Planning", accessed 2026-11-10. https://docs.nvidia.com/dgx-superpod/design-guides/dgx-superpod-data-center-design-h100/latest/planning.html
15. NVIDIA, "DGX B200" product page, accessed 2026-11-10. https://www.nvidia.com/en-gb/data-center/dgx-b200/
15b. NVIDIA, "DGX GB200 User Guide: Hardware", accessed 2026-11-10. https://docs.nvidia.com/dgx/dgxgb200-user-guide/hardware.html
16. Lambda, "AI cloud pricing", accessed 2026-11-10 (prices stated effective 2026-11-16). https://lambda.com/pricing
17. CoreWeave, "Pricing", accessed 2026-11-10. https://www.coreweave.com/pricing
18. AWS, "Amazon EC2 Capacity Blocks for ML Pricing", accessed 2026-11-10. https://aws.amazon.com/ec2/capacityblocks/pricing/
19. AWS News Blog, "Announcing up to 45% price reduction for Amazon EC2 NVIDIA GPU-accelerated instances", 2025-06-05. https://aws.amazon.com/blogs/aws/announcing-up-to-45-price-reduction-for-amazon-ec2-nvidia-gpu-accelerated-instances/
20. Loan Market Association, "Infrastructure Insights: Data Centre Financing – Evolution and Structures", July 2026. https://www.lma.eu.com/application/files/9517/8514/2575/LMA_Infrastructure_insights_data_centre_report.pdf
21. CBRE, "North America Data Center Trends H1 2025" brief, 2025-08-19. https://www.cbre.com/insights/briefs/north-america-data-center-trends-h1-2025-ai-and-hyperscaler-demand-lead-to-record-low-vacancy
22. JLL, "2026 Global Data Center Outlook", 2026-01-05. https://www.jll.com/en-us/insights/market-outlook/data-center-outlook.html
23. IREI, "Cushman & Wakefield releases 2026 Data Center Development Cost Guide…", 2026 (secondary). https://irei.com/news/cushman-wakefield-releases-2026-data-center-development-cost-guide-citing-21-rise-in-per-mw-construction-costs/
24. Equinix earnings-call summaries (Q2 2026, VectorShift; Analyst Day notes, TD Cowen via Ocolo) (secondary). https://vectorshift.ai/research/companies/equinix/earnings/2026-q2-summary ; https://blog.ocolo.io/td-cowen-takeaways-from-equinix-analyst-day/
25. Digital Realty Q1–Q3 2025 results coverage (StreetInsider / Motley Fool transcripts), 2025 (secondary). https://www.streetinsider.com/Press+Releases/Digital+Realty+Reports+Third+Quarter+2025+Results/25495011.html
26. Global Data Center Hub, "Anthropic, Lambda, Beacon Point data center" (Hut 8 lease terms), 2026 (secondary). https://www.globaldatacenterhub.com/p/anthropic-lambda-beacon-point-data-center
27. Cleary Gottlieb, "Financing the Data Center Boom", 2026-09-09. https://www.clearygottlieb.com/news-and-insights/publication-listing/financing-the-data-center-boom
28. American Banker Asset Securitization Report, Centersquare and Iskandar/Kaveh data-centre ABS articles, 2025–26 (trade press). https://asreport.americanbanker.com/news/centersquare-issuer-get-set-to-issue-940-million-through-two-series-of-notes ; https://asreport.americanbanker.com/news/data-center-abs-iskandar-and-kaveh-bring-to-market-raising-1-4-billion
29. IFRS Foundation, "IFRS 16 Leases", accessed 2026-11-10. https://www.ifrs.org/issued-standards/list-of-standards/ifrs-16-leases/
30. IEA, "Energy and AI", executive summary, April 2025 (page returned 403; figures from IEA text quoted in search results). https://www.iea.org/reports/energy-and-ai/executive-summary
