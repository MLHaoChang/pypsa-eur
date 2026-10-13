<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# Competitive landscape: commercial behind-the-meter / edge / DER / C&I electrification investment tools

Research date: 2026-09-28. Prepared for: a PyPSA + pandapower desktop/web tool for consultants (capacity expansion, dispatch, adequacy/FMEA, energy-hub "reference design").

## 0. Method, and how far to trust it

- About 57 web searches were run. The session's shared web-search budget ran out after that, so a few planned searches (more hydrogen SaaS vendors, some pricing pages) could not be done.
- **Direct page fetches were mostly blocked** by the sandbox egress proxy. Blocked domains included ul.com, homerenergy.com, xendee.com, energytoolbase.com, nlr.gov/nrel.gov, osti.gov, readthedocs, openei.org, gridcog.com, dnv.com and ratedpower.com. Only github.com could be fetched. As a result, most "verified" statements below come from **search-engine summaries of the cited vendor, documentation or press pages**, not from reading the full page. I call these **[V]** (verified through a search summary of the URL given). The three GitHub pages that were fetched directly are marked **[V-fetched]**.
- **[I]** marks my inference or background knowledge, which was not checked in this session.
- Pricing changes often. Treat every price as "as indexed by search", and check it before quoting it to anyone.

---

## 1. Product profiles

### 1.1 HOMER Pro / HOMER Grid / HOMER Front (UL Solutions)

**Positioning [V]**: HOMER Pro finds the least-cost microgrid design. HOMER Grid designs behind-the-meter (BTM) grid-connected systems to cut bills and peaks. HOMER Front maximises project IRR for utility-scale storage, either standalone or hybrid. Sources: https://www.ul.com/software/homer-microgrid-and-hybrid-power-modeling-software, https://www.ul.com/software/homer-grid, https://www.ul.com/software/ultrus/homer-hybrid-energy-optimization

**HOMER Pro**
- *Target users and use cases [V]*: microgrid, off-grid and hybrid designers, including remote sites, islands, industrial sites and hydrogen systems.
- *Engine and UX [V]*: the user defines a **search space** of candidate component sizes and a set of **sensitivity variables**. HOMER runs a separate optimisation for each sensitivity case, simulates every system in the search space, and ranks the feasible systems by **Net Present Cost**. Results appear in a Sensitivity Cases table plus an Optimization Results table, with "overall" or "categorized" views and a Graphical mode for sensitivity and optimisation plots. The **HOMER Optimizer** is a proprietary derivative-free algorithm. Time steps run from 1 minute to 1 hour. Sources: https://homerenergy.com/products/pro/docs/latest/results.html, https://homerenergy.com/products/pro/docs/latest/sensitivity_cases.html, https://www.homerenergy.com/products/pro/docs/3.15/optimization_results.html
- *Add-on modules (paid) [V]*: Advanced Load (AC/DC and deferrable loads), Advanced Grid (real-time prices, grid extension), Combined Heat & Power (boilers, cogeneration, heat recovery), Run-of-River Hydro, Biomass, **Hydrogen** (electrolyser, reformer, H2 tank, H2 load, H2-fuelled generator, seasonal storage), Advanced Storage (Modified Kinetic Battery Model with temperature, DoD and degradation), **Multi-Year** (PV degradation, grid price escalation, load growth, fuel escalation), and MATLAB Link (the user's own dispatch strategy). Sources: https://www.homerenergy.com/products/pro/modules/index.html, https://homerenergy.com/homer-pro/hydrogen
- *Financial outputs [V/I]*: NPC, LCOE, operating cost, and a comparison against a base case (IRR, payback). [I] The Multi-Year module is needed to capture degradation and escalation. Without it, the economics use a single representative year.
- *Reporting [V]*: HOMER Pro has "Client Proposal" and "Engineer Detail" report pages (https://homerenergy.com/products/pro/docs/latest/client_proposal.html, https://homerenergy.com/products/pro/docs/latest/engineer_detail.html). Version 3.9 introduced "powerful reporting" (https://microgridnews.com/homer-pro-3-9-powerful-reporting-furthers-homers-clean-power-everywhere-mission/).
- *Pricing [V]*: Standard is USD 187.50/month or USD 1,575/year. A mid tier with 4 modules is USD 3,100/year. The all-modules tier is USD 4,650/year. There is 25% off for 4 or more licences, plus academic and student pricing. Sources: https://www.homerenergy.com/products/pro/pricing/index.html, https://homerenergy.com/homer-pro/academic-pricing
- *Weaknesses (academic critique) [V]*: closed source and costly. There is no multi-objective optimisation and limited complex tariff and billing modelling. It has no electrical network (no transformers or lines, no reactive power; active-power balance only). Thermal and heat-pump modelling is weak. Source: SAMA paper, https://www.sciencedirect.com/science/article/abs/pii/S0196890423010324

**HOMER Grid**
- *Target [V]*: C&I BTM (solar + storage + generators + EV charging), sold as demand-charge reduction and resilience. There is also a "Data Centers" solution page that frames HOMER Grid for on-site generation (solar, fuel cells, gas), BESS, backup, 24/7 CFE and bridging (https://homerenergy.com/solutions/data-centers).
- *Tariffs [V]*: automated access to tariff structures covering more than 90% of ZIP codes in the US, Canada and Mexico, plus Australia. The dispatch strategy optimises a **monthly demand limit** to maximise demand-charge savings. Sources: https://www.homerenergy.com/products/grid/index.html, https://homerenergy.com/products/grid/docs/latest/how_grid_calculates_demand_charge_and_energy_bills_savings.html
- *Financial [V]*: detailed installed and O&M costs, risk and sensitivity analysis, ITC (30%), MACRS depreciation and local incentives. Outputs include payback, projected savings and ROI, with cost comparisons between the existing and proposed system **during normal operation and during outages**.
- *Reporting [V]*: a proposal creator (since v1.7) produces client-facing reports with a **custom logo**, exportable to **PDF, HTML, DOCX and RTF**. Sources: https://microgridnews.com/homer-tip-the-new-proposal-report-in-homer-grid/, https://www.solarpowerworldonline.com/2020/02/homer-grid-solar-software-update-includes-proposal-generation/
- *Pricing*: a pricing page exists (https://homerenergy.com/homer-grid/pricing), but no figure appeared in the search results.

**HOMER Front**
- *Target [V]*: utility-scale storage and hybrid developers and IPPs. It was launched in May 2022 (https://www.pv-magazine.com/2022/05/12/ul-releases-modeling-software-for-utility-scale-energy-storage/).
- *Revenue [V]*: merchant energy arbitrage (day-ahead and real-time), frequency regulation, capacity (CAISO Resource Adequacy), time-of-delivery PPAs, and combinations of these. It covers CAISO and ERCOT. It models degradation, augmentation and replacement, and runs sensitivity analysis to de-risk IRR. Sources: https://www.ul.com/news/ul-releases-homer-front-modeling-software-maximize-revenue-utility-scale-energy-storage, https://www.homerenergy.com/products/front/index.html

### 1.2 XENDEE (Xendee Corp.; Eaton holds a minority stake)
- *Products [V]*: **DESIGN** (optimisation-based DER and microgrid design that models power and energy flow together with financial constraints "without manual iterations"). **PROPOSE** (a catalog-driven, real-time proposal tool for sales and business development, launched Dec 2023, with catalogs of DER technologies, EV charging and utility tariffs). **MOBILITY** (sizing of EV and fast-charging infrastructure). **OPERATE** (an AI microgrid controller). Sources: https://xendee.com/, https://xendee.com/propose, https://www.businesswire.com/news/home/20231205581247/en/
- *Engineering [V]*: a multi-node interface layers **power flow and voltage constraints** on cables and equipment into the investment optimisation. Resilience metrics and **N-1 redundancy** ensure critical loads are served. Sources: https://solarbuildermag.com/news/microgrids-made-easy-xendee-adds-multi-node-feature-to-enable-more-complex-designs/, https://xendee.com/glossary
- *Financial [V]*: NPV, ROI, payback and cash flow. It can simulate **Energy-as-a-Service and Charging-as-a-Service** business models, and it integrated **Inflation Reduction Act** incentives. Sources: https://xendee.com/insights/microgrid-design-platform, https://xendee.com/pricing
- *Data centres [V]*: there is a dedicated data-centre page (https://xendee.com/data-centers, https://xendee.com/power-your-data-center). A 7x24 Exchange article frames on-site microgrids as **bridge power** while the grid interconnection queue takes 3–5 years or more (https://www.7x24exchange.org/industry-news/magazine-archives/a-transformative-approach-to-bridging-the-data-center-power-gap/).
- *Partnerships [V]*: in Sept 2025 Eaton integrated Xendee design and operations software into its microgrid offer and took a minority stake (https://www.eaton.com/us/en-us/company/news-insights/news-releases/2025/eaton-and-xendee-collaborate-to-optimize-microgrid-performance.html).
- *Licensing [V]*: separate pricing pages for DESIGN and PROPOSE, plus academic pricing. **All contracts are annual.** There is a 30-day demo at the monthly "Platinum" price (https://xendee.com/pricing/design, https://xendee.com/faq). Actual figures were not retrievable. Xendee also runs "Xendee University" training courses.
- [I] Xendee's founders come from the LBNL DER-CAM team, so its MILP heritage is similar to DER-CAM's.

### 1.3 NREL (now "NLR") REopt: web tool, API, and open-source REopt.jl
- *Access and licensing [V-fetched]*: free web tool. There is an API through the NLR developer network and open-source code: REopt.jl is Apache-2.0 and REopt_API is on GitHub (https://github.com/NatLabRockies/REopt_API, https://github.com/NatLabRockies/REopt.jl). It is a MILP and can run on HiGHS, Cbc, SCIP, Xpress or CPLEX.
- *Technologies [V]*: PV, wind, battery, CHP, prime and emergency generators, GHP (expanded hybrid/central/distributed), and heating and cooling. **Hydrogen**: electrolysers, fuel cells, low- and high-pressure H2 storage, and FCEV and H2 process loads. Sources: https://www.nlr.gov/news/detail/program/2024/reopt-web-tool-offers-enhanced-energy-solutions, https://www.energy.gov/hgeo/geothermal/articles/reopt-web-tool-geothermal-heat-pump-module-now-available
- *Financial outputs [V]*: NPV of life-cycle savings, a **downloadable pro-forma Excel** with cash flows, and a dispatch spreadsheet. Ownership can be direct or third-party [I, per the REopt financial tutorial https://docs.nlr.gov/docs/fy20osti/76677.pdf]. Incentives, MACRS and escalation are inputs [I]. **Caveat [V]**: the web tool solves a single-year optimisation and extrapolates it to N-year cash flows (https://reopt.nrel.gov/tool/results/..., https://docs.nrel.gov/docs/fy24osti/90962.pdf).
- *Resilience [V]*: critical load, outage sizing, and a "Resilience vs. Financial" tab. An outage simulator gives the **probability of surviving outages** of various durations, and it accounts for technology reliability. Sources: https://docs.nrel.gov/docs/fy20osti/76678.pdf, https://www.osti.gov/servlets/purl/1606315
- *2024–25 additions [V]*: **off-grid mode** (operating reserves, a minimum percentage of load met), emissions including **health and climate costs** (CO2, NOx, SO2, PM2.5), **net billing (NEM3)**, and **portfolio screening of many sites** without code.
- *Tariffs [I]*: pulls from the OpenEI URDB, which covers 3,700+ US utilities and about 70% of US load (https://openei.org/wiki/Utility_Rate_Database). It is US-centric; elsewhere the user supplies a custom tariff.

### 1.4 LBNL DER-CAM / DER-CAM+
- *What it is [V]*: a MILP written in GAMS that minimises the annual cost of energy services. It includes amortised DER capital, O&M, and utility electricity and gas purchases. It covers waste-heat heat exchangers and **absorption chillers**. It co-optimises stacked value streams: load shifting, peak shaving, export agreements and **ancillary services**. Sources: https://gridintegration.lbl.gov/der-cam, https://building-microgrid.lbl.gov/projects/der-cam
- *DER-CAM+ [V]*: a browser UI where users **draw the single-line diagram and the heat network** and set node-level parameters. It includes a MILP linearised power flow with active and reactive power and losses, plus thermal flow. It supports resilience targets and has a stochastic EV-fleet formulation. Sources: https://building-microgrid.lbl.gov/news/der-cam-announcement, https://ets.lbl.gov/publications/optimal-investment-and-scheduling
- *Access [V]*: free after registration. A desktop client stores projects locally and runs jobs on LBNL servers (https://building-microgrid.lbl.gov/projects/how-access-der-cam).
- *Weakness [I]*: research UX, limited report generation, and a thin tariff library.

### 1.5 Energy Toolbase: ETB Developer (plus ETB Controller and ETB Monitor)
- *Target [V]*: US solar+storage developers and EPCs doing C&I and residential work.
- *Modelling [V]*: storage dispatch simulation that **forecasts site load and PV and dispatches on the forecast, the way the real controller does in the field**. The simulation mirrors the company's own EMS, formerly Acumen EMS and rebranded **ETB Controller** in 2026. It covers demand-charge reduction, TOU arbitrage, NEM programs (NEM 3.0 hourly Avoided Cost Calculator export), and grid services. Sources: https://www.energytoolbase.com/blog/energy-storage/evolution-of-energy-storage-modeling/, https://www.energytoolbase.com/blog/project-development/etb-developers-newest-feature-nem-programs/, https://www.energytoolbase.com/blog/energy-storage/energy-toolbase-unveils-energy-management-system-rebrand-acumen-ems-is-now-etb-controller/
- *Rate database [V]*: described as 70,000+ manually verified rates by one review and 120,000+ by another. Either way it is the core moat. Sources: https://www.surgepv.com/reviews/energy-toolbase, https://qbitsenergy.com/blog/energy-toolbase-review/
- *Financial [V]*: cash, **PPA, loan and lease** structures with comparisons between them, incentives, and bill savings.
- *Reporting and UX [V]*: a **Proposal Summary page** compares several designs side by side (project cost and incentives, PV and ESS details, bill savings, transaction). A **document template gallery** offers short and long forms, commercial and residential, and several themes, and templates can be copied and customised. It also produces web proposals. Sources: https://www.energytoolbase.com/blog/project-development/new-etb-developer-proposal-features-proposal-summary-page-and-document-templates/, https://www.energytoolbase.com/blog/project-development/proposal-document-template-gallery/, https://www.energytoolbase.com/blog/project-development/solar-energy-storage-proposal-management/
- *Pricing [V]*: reported as USD 299/user/month billed annually (Individual) and USD 333/user/month billed annually (Business, which includes 5 users). Older figures are USD 199 per month, or about USD 179 per month on annual billing. Sources: https://www.energytoolbase.com/pricing/, https://www.surgepv.com/reviews/energy-toolbase
- *Weaknesses [V]*: a 4–6 week learning curve, a North America focus, no 3D or layout tools (it is paired with HelioScope or Aurora), and a higher price than entry-level tools.

### 1.6 NREL SAM (System Advisor Model)
- *Target [V]*: detailed performance and finance for RE, storage and fuel-cell projects. It is free desktop software with PySAM for Python. Current version: 2025.4.16 (https://sam.nrel.gov/).
- *Financial models [V]*: Residential, Commercial, Third-Party (host), **Host/Developer**, Single Owner, **Partnership Flip** (with or without debt), Sale-Leaseback, Merchant Plant and Community Solar. Configurations include PV+Battery (host/developer and third-party), FuelCellCommercial, and a PVWatts+Wind+FuelCell+Battery hybrid. Sources: https://sam.nrel.gov/financial-models.html, https://nrel-pysam.readthedocs.io/en/main/sam-configurations.html
- *Uncertainty and UX [V]*: parametric sweeps, **stochastic (Monte Carlo)** runs, **P50/P90**, an LK scripting language and macros, SDK bindings, URDB download of tariffs, and Excel-compatible output tables and graphs (https://sam.nrel.gov/simulation-options.html).
- *Weakness [I]*: a simulator, not a sizing optimiser. It has limited multi-asset co-optimisation and is not built for microgrid or islanded reliability.

### 1.7 RETScreen Expert (Natural Resources Canada)
- *Modules and workflow [V]*: Benchmark, Feasibility, Performance and Portfolio analysis. Worksheets are filled left to right in a guided sequence: Location, Facility, Energy model, Cost, Emissions, Finance, Risk. It has integrated product, project, benchmark, hydrology and climate databases. Sources: https://natural-resources.canada.ca/science-data/science-research/research-centres/video-overview-retscreen-expert-platform, https://openei.org/wiki/RETScreen_Clean_Energy_Project_Analysis_Software
- *Financial [V]*: IRR, simple payback, NPV, debt, taxes, incentives, GHG credits and avoided cost of energy. **Sensitivity** (vary parameters by a percentage) and **Monte Carlo risk analysis** are built in. Version 9 added an ISO 50001 energy-performance report generator. Sources: https://www.saveonenergy.ca/-/media/Files/SaveOnEnergy/training-and-support/ee/Financial-Analysis-with-RETScreen-Expert.pdf, https://www.scribd.com/document/92819703/RETScreen-Help-Sensitivity-and-Risk-Analysis
- *Pricing [V]*: the Viewer is free. Professional mode costs CAD 869 per 12-month subscription (https://www.capterra.com/p/156830/RETScreen/).
- *Weakness [I]*: pre-feasibility depth only. Dispatch is coarse, and there is no storage dispatch optimisation or power flow.

### 1.8 Enact Solar
- *[V]*: cloud SaaS for residential and commercial solar with 3D design, proposals, project management and monitoring. A **Storage Savings** feature sizes storage alongside PV and simulates TOU charge and discharge for any day. Financial comparisons cover ROI, loan, lease and PPA, with payback and cash flow. Sources: https://enact.solar/commercial/, https://enact.solar/providers/solar-proposal-software/
- *[I]*: aimed at installers' sales proposals, not at engineering-grade microgrid or data-centre work.

### 1.9 Aurora Solar (HelioScope)
- *[V]*: on **2026-06-16** Aurora added integrated storage modelling to HelioScope for commercial projects. It sizes storage, models performance and runs the financial case in the same tool, producing a "lender-ready output". HelioScope production estimates are accepted by lenders. Battery cost is added to system cost, and storage incentives are applied separately. Sources: https://aurorasolar.com/news/aurora-solar-adds-integrated-storage-modeling-to-helioscope-uniting-commercial-solar-design-storage-and-financial-analysis-in-one-solution/, https://help.aurorasolar.com/hc/en-us/articles/51752305485715-Financial-analysis-Overview
- *Relevance*: this shows the trend of **design, storage and finance merging into one tool**, with "bankable" or "lender-ready" as the selling point.

### 1.10 ETAP (µGrid, Microgrid EMS)
- *[V]*: a model-driven platform to design, simulate, optimise, test and control microgrids. A **digital twin of the microgrid controller** is used at the design stage to tune settings and to **size DERs, especially BESS, for reliability and economic objectives**. It integrates CHP, PV, storage and absorption chillers. It also offers battery discharge and sizing analysis and a BESS sizing case study (Phu Quy island). Sources: https://etap.com/solutions/microgrid, https://etap.com/docs/default-source/brochures/fact-sheets/microgrid-fact-sheet-2024-web.pdf, https://etap.com/case-study/how-to-optimize-the-sizing-of-the-battery-energy-storage-system-for-the-hybrid-system-in-phu-quy-island
- *[I]*: very strong on electrical engineering (load flow, short circuit, protection, arc flash), much weaker on financial proformas and tariffs. It competes with our pandapower side rather than with the investment-case side.

### 1.11 Schneider Electric
- **EcoStruxure Microgrid Assessment Design Software [V]**: an end-to-end tool for feasibility, simulation, sizing and economic evaluation. It has a feasibility tool with **KPIs for economics, resiliency and sustainability**, and an "Optimal DER Sizing and Operation" tool with **multi-objective selection** of configurations. It covers PV, batteries, gensets and EV charging, on-grid and off-grid, plus tariff and incentive modelling. The workflow runs: project setup, component configuration, tariffs and incentives, simulation, results. Source: https://www.se.com/us/en/product-range/340429607-ecostruxure-microgrid-assessment-design-software/
- **EcoStruxure Microgrid Advisor [V]** (operations): AI forecasting and MPC against the tariff, a **dynamic demand-charge threshold**, day-ahead and hour-ahead price response, and a storm-hardening mode (https://www.se.com/us/en/product-range/65896-ecostruxure-microgrid-advisor/).
- **EcoStruxure Microgrid Flex [V]**: standardised, pre-engineered microgrid architectures for speed. This is a direct analogue of a "reference design" concept (https://facilityexecutive.com/schneider-electric-introduces-ecostruxure-microgrid-flex/).
- **AlphaStruxure [V]**: an Energy-as-a-Service joint venture. It was ranked the #1 microgrid integrator by Guidehouse and has 350+ projects (https://alphastruxure.com/news-press-release/guidehouse-ranks-schneider-electric-alphastruxure-as-the-1-microgrid-integrator/).
- **Data-centre TradeOff Tools [V]**: free web calculators, including a data-centre power sizing calculator for AI/HPC, a capital cost calculator, an AC vs DC efficiency calculator, and DCIM ROI. They quantify ROI, TCO, carbon and efficiency trade-offs (https://www.se.com/us/en/work/solutions/data-centers-and-networks/trade-off-tools/). [I] They do not size on-site generation or storage against tariffs.

### 1.12 Siemens
- **PSS SINCAL [V]**: identifies network connection points, routing alternatives, **hosting capacity** and **grid-code compliance** for DER, EV charging and **new loads**. It combines RMS and EMT studies, and it models **multi-energy networks** (electricity plus gas and heat pipe networks). It integrates with GIS, SCADA, DMS and MDMS. Sources: https://www.siemens.com/en-us/products/pss-software/pss-sincal/, https://www.siemens.com/us/en/products/energy/grid-software/planning/pss-software/pss-sincal/pss-sincal-electricity.html
- **SICAM Microgrid Control [V]**: a controller that scales to deployments of up to 2 GW. Siemens builds an "energy twin simulation" for feasibility before choosing the controller (https://www.siemens.com/en-us/products/microgrids/sicam-microgrid-control/). **Gridscale X [V]** is utility DER visibility software within Siemens Xcelerator.
- *[I]*: Siemens has no public self-serve BTM investment-case tool. Feasibility work is done as a service.

### 1.13 ABB and Hitachi Energy (public information only)
- **ABB Ability OPTIMAX [V]**: an energy management and optimisation platform for industrial sites, microgrids and smart cities. It has AI forecasting of load, generation and prices, and supports market participation. ABB claims up to 10% energy-cost reduction (https://www.abb.com/global/en/areas/automation/solutions/industrial-software/energy-management/energy-optimization-optimax). It is for operations, not investment planning.
- **Hitachi Energy e-mesh [V]**: the e-mesh portfolio came from ABB. It includes e-mesh EMS/Manager (bill reduction through BESS and DER operation, EV and load management) and the grid-forming **PowerStore** BESS, now offered as a modular skid. Hitachi Energy provides dynamic simulation models for grid-code compliance and uses C-HIL with Typhoon HIL. Sources: https://www.hitachienergy.com/news-and-events/press-releases/2021/11/hitachi-energy-releases-global-updates-to-grid-edge-solutions-port-folio-including-new-services, https://www.typhoon-hil.com/blog/hil-the-pillar-of-hitachi-energy-e-mesh-powerstore-bess-product-development/
- **Hitachi Energy Energy Portfolio Management [V]**: PROMOD (production-cost and market simulation for transmission and investment planning), **Capacity Expansion**, Velocity Suite (now "Energy Market Insights": data, analytics, maps), Nostradamus AI (forecasting) and Asset Optimization. Sources: https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/enterprise/promod, https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/enterprise
- **Lumada APM [V]**: health, reliability and optimisation modules that plan asset investments and prioritise interventions by cost, risk and performance (https://www.hitachienergy.com/us/en/news-and-events/press-releases/2023/10/hitachi-energy-launches-the-next-generation-of-its-asset-performance-management-solution-lumada-apm). This is relevant to our FMEA and adequacy angle.
- **Data centres [V]**: in Sept 2026 the Hitachi Energy BESS qualified as **NVIDIA "DSX Ready" BESS** against an AI-factory reference design (https://www.hitachienergy.com/news-and-events/features/2026/09/hitachi-energy-s-battery-energy-storage-system-qualifies-as-nvidia-dsx-ready-bess). Hitachi also offers **Grid-eXpand** modular grid-connection solutions, 800 VDC grid-to-rack power conversion, and a Sept 2026 blog on energy trading for data centres (https://www.hitachienergy.com/news-and-events/blogs/2026/09/how-to-trade-and-manage-energy-for-data-centers-in-complex-power-markets).
- *[I] Gap*: no public, self-serve, site-level investment tool from Hitachi Energy was found. The public portfolio spans system-level planning (PROMOD, Capacity Expansion), operations (e-mesh, Nostradamus) and hardware. A consultant tool for edge investment cases would sit in the missing middle.

### 1.14 Wärtsilä
- **GEMS Digital Energy Platform [V]**: monitors, controls and optimises assets at site and portfolio level, across 100+ projects. GEMS 7 (Aug 2024) adds control of multi-GWh sites, **site partitioning to assign revenue applications**, cell balancing and SoC calibration (https://www.wartsila.com/media/news/06-08-2024-wartsila-s-seventh-generation-gems-digital-energy-platform-supports-rapid-pace-of-scale-and-change-in-global-energy-storage-industry-3480027). It is for operations.
- **Modelling / "Energy Transition Lab" [V]**: an in-house team uses **PLEXOS** and has done 200+ country and power-system analyses with chronological hourly modelling. These are consulting studies, not a product (https://www.wartsila.com/energy/towards-100-renewable-energy/choosing-the-optimal-pathway-for-energy-transition).

### 1.15 Emerson / AspenTech OSI
- *[V]*: OSI DERMS (utility) and the **AspenTech Microgrid Management System (MMS)** for heavy industrial users with on-site generation, load management, storage and multi-site portfolios. Both are operational (https://www.aspentech.com/en/resources/press-releases/aspen-technology-launches-microgrid-management-system). Neither is an investment tool.

### 1.16 Storage value, bidding and revenue tools (market-side)
- **Fluence Mosaic [V]**: AI bidding in the NEM, CAISO and ERCOT, with 13.3 GW under management. Fluence claims +50% against the CAISO average in summer 2025. Fluence Nispera provides APM (https://fluenceenergy.com/mosaic-intelligent-bidding-software/).
- **Stem [V]**: Athena was rebranded PowerTrack Optimizer in Sept 2024. The suite includes PowerBidder Pro (wholesale bidding), PowerTrack APM and PowerCore EMS (https://investors.stem.com/news-events/press-releases/detail/124/...).
- **Enel X DER.OS [V]**: ML forecasting of load and PV, and optimal BESS schedules that stack demand response, reserves and bill savings for C&I customers (https://corporate.enelx.com/en/our-offer/business-solutions/battery-energy-storage).
- **Modo Energy [V]**: bankable BESS revenue forecasts with **high, central and low** curves, the regulated **ME BESS GB and Germany indices**, research, and an API (https://modoenergy.com/product/forecasts, https://modoenergy.com/public-indices).
- **Aurora Energy Research Chronos [V]**: "bankable" battery valuations using Aurora's dispatch engine. It tests degradation cases, co-location with RES and commercial strategies. 200+ users; its methodology supports USD 4bn in transactions (https://auroraer.com/software/chronos).
- **Pexapark [V]**: PPA fair values, hybrid PPA plus BESS co-location premiums, negative-price impact and imbalance cost. PexaQuote handles pricing and PexaOS handles portfolio risk (https://pexapark.com/blog/price-intelligence-for-the-next-wave-of-deals-hybrid-ppas-bess/).
- **European C&I optimisers [V]**: Entrix (C&I storage trading and business case with revenue forecasts, https://entrixenergy.com/en/industrial-storage), enspired, Ampowr (Cosmos EMS and free customised business cases), and RatedPower BESS (sizing, arbitrage dispatch, degradation, CAPEX/OPEX, https://ratedpower.com/platform/bess/).

### 1.17 Directly comparable tools not on the original list (important)
- **Gridcog (AU/UK) [V]**: very close to what we are building, for C&I and large energy users. It supports multi-market, multi-site and multi-asset **simulation, optimisation and tracking**. Scenarios are compared by **cash flows and IRR**, with interval-level energy flows over the project life. Users define **project participants and assign cash flows to each party**, to test commercial structures and see "who wins". It models network charges and wholesale exposure, and it covers data centres, airports, hospitals, grid-constrained sites, backup generation and fleet electrification. It supports both **perfect foresight and uncertainty** in dispatch ("Magic Mode"), produces downloadable investment-case reports, and bundles integrated market data in all plans. Sources: https://www.gridcog.com/large-energy-users, https://www.gridcog.com/large-scale-asset-developers, https://www.gridcog.com/blog/importance-of-uncertainty, https://www.gridcog.com/blog/introduction-to-different-commercial-structures-in-energy-projects, https://www.cefc.com.au/case-studies/gridcog-energy-solutions-attract-global-customers/
- **energyPRO (EMD International, DK) [V]**: multi-energy (electricity, heat, cooling, CO2, fuels) operational and financial simulation. It covers **P2X and electrolysers with waste heat to district heating**, heat pumps and day-ahead market sales (https://www.emd-international.com/software/energypro, https://www.emd-international.com/power-to-methane-in-energypro). [I] It is widely used in the Nordic district-heating sector and produces detailed operational and financial reports.
- **nPro (DE) [V]**: web-based district energy planning. It optimises heat pumps, boilers, CHP, PV and storage as one system, and handles **heat-recovery chillers**, 5GDHC, hydrogen, and pipe routing and sizing (https://www.npro.energy/main/en).
- **Polysun (Vela Solaris, CH) [V]**: released **data-centre waste-heat utilisation templates** covering the regulation (German EnEfG), use cases and planning (https://www.velasolaris.com/en/data-center-heat-reuse/, https://www.openpr.com/news/4282294/...).
- **iHOGA / MHOGA (Univ. Zaragoza) [V]**: genetic-algorithm optimisation of PV, wind, hydro, gensets, batteries and **electrolyser + H2 tank + fuel cell**. It includes multi-period degradation and load growth, **multi-objective** optimisation, 1-minute steps, and sensitivity and probability analysis. PRO+ licences run 6 months, 1 year or perpetual (https://ihoga.unizar.es/en/caracteristicas/, https://ihoga.unizar.es/en/versiones/).

### 1.18 Hydrogen-specific tools
- **HyDesign (DTU) [V-fetched]**: MIT licence. Sizing of wind, PV, BESS and P2H hybrid plants, including turbine selection, a wake surrogate, degradation, EMS operation and a WACC-per-technology financial model. The objective is LCOE or NPV/CAPEX, and a break-even price and PPA notebook is available. Sources: https://github.com/DTUWindEnergy/hydesign, https://wes.copernicus.org/articles/9/759/2024/, https://topfarm.pages.windenergy.dtu.dk/hydesign/notebooks/break_even_price_and_PPA.html
- **NREL H2A / H2FAST [V]**: H2A is a discounted-cash-flow model giving the levelised H2 selling price at a target IRR. H2FAST follows GAAP, with a web version of about 20 inputs (investor cash flow, IRR, break-even H2 price), an Excel version and a business-case (BCS) version (https://docs.nrel.gov/docs/fy20osti/75588.pdf, https://docs.nrel.gov/docs/fy15osti/64307.pdf).
- **H2Integrate (NLR, formerly GreenHEART/HOPP) [V-fetched]**: BSD-3. Models PEM, SMR, H2 storage, ammonia, steel, generation and ProFAST financials, with LCOH (https://github.com/NatLabRockies/H2Integrate).
- **Honeywell Protonium Concept Design Optimizer [V]**: commercial software launched in Apr 2025. It optimises green-H2 plant design "in minutes" from the power profile, CAPEX, OPEX and LCOH (https://process.honeywell.com/us/en/solutions/green-hydrogen/concept-design-optimizer, https://www.honeywell.com/us/en/press/2025/04/honeywell-unveils-ai-assisted-suite).
- **PLEXOS (Energy Exemplar) [V]**: Power-to-X and gas objects and a European Hydrogen Dataset; used to derive optimal electrolyser ratings against PPAs and price forecasts (https://www.energyexemplar.com/product-news/modelling-economic-environmental-impact-hydrogen-europe, https://www.energyexemplar.com/blog/electrolyser-modellings).
- HOMER Pro (Hydrogen module), REopt (hydrogen suite) and iHOGA also cover electrolyser, tank and fuel-cell chains (see above). A DNV hydrogen tool was **not found** in the searches.

### 1.19 Data-centre-specific practice
- **Bloom Energy Value Calculator [V]**: compares fuel cells with conventional options, including **overbuild and time-to-power effects on total economics** (https://www.bloomenergy.com/value-calculator/). Bloom claims delivery in about 90 days and 20–500 MW systems.
- **Vertiv [V]**: an AI Reference Design Selector, Modular Designer, a UPS sizing tool, and a white paper that separates UPS (critical load) from BESS (site energy management, peak shaving, grid services) (https://www.vertiv.com/en-us/insights/articles/white-papers/bess-and-ups-roles-in-large-data-center-power-architecture/).
- **Schneider TradeOff Tools** (see 1.11).
- **Waste-heat business case [V]**: the NZIH heat-reuse calculator (https://www.netzerodatacenters.com/heatreuse). Academic NPV models for data-centre heat to district heating (https://www.sciencedirect.com/science/article/pii/S2210670718314318, https://asmedigitalcollection.asme.org/sustainablebuildings/article/6/1/011002/1210426/). Indicative figures: delivered heat at EUR 12–30/MWh against EUR 35–55/MWh for gas boilers, and heat-recovery CAPEX of about EUR 190–250k/MW (https://energy-solutions.co/articles/sub/data-center-waste-heat-district-heating; low-authority source). Germany's **EnEfG** requires rising energy-reuse shares for new data centres, reaching 20% by 2028.
- **Hyperscaler procurement [V]**: Google targets **24/7 CFE hourly matching** by 2030. It reports 74% hourly CFE and an AES Virginia deal guaranteeing at least 90% of hours (https://sustainability.google/reports/24x7-carbon-free-energy-data-centers/). Microsoft's "100/100/0" hourly goal was reported in May 2026 to be under review (https://techcrunch.com/2026/05/06/microsofts-ai-data-center-push-is-colliding-with-its-clean-power-goals/).
- **Market context [V]**: "speed to power" is the top constraint. Grid connection can take up to three times as long as the 12–24-month build. BTM gas turbines or engines plus BESS for load smoothing are expected to reach 25–35 GW by 2030 (https://www.ess-news.com/2026/03/19/how-on-site-batteries-are-fast-tracking-data-center-grid-connections/, https://www.enverus.com/blog/why-data-centers-are-looking-to-natural-gas-for-behind-the-meter-power/).
- [I] **No commercial tool found co-optimises data-centre on-site power, bridging-to-grid timing, BESS and waste-heat sale in one investment case.** The closest are XENDEE and HOMER Grid on power, and Polysun, nPro and energyPRO on heat. This is a white space.

---

## 2. Workflow and UX patterns seen across products

| Pattern | Who does it | Notes |
|---|---|---|
| **Search space + sensitivity grid, ranked by NPC** | HOMER Pro/Grid [V] | The user sets candidate sizes. Results are a ranked table plus graphical sensitivity (overall vs categorised). |
| **Single MILP "optimise" button with defaults** | REopt, XENDEE DESIGN, DER-CAM [V] | Minimal inputs (location, load, tariff) and good defaults; results come back as recommended sizes plus savings. |
| **Two-tier "quick quote" vs "engineering" mode** | XENDEE PROPOSE vs DESIGN [V]; ETB proposals vs detailed dispatch [V] | Sales staff produce a proposal live with the customer; engineers refine it later. |
| **Guided left-to-right worksheets** | RETScreen [V]; Schneider Microgrid Assessment (setup → components → tariffs and incentives → simulate → results) [V] | Wizard-like. Good for occasional users. |
| **Draw-the-single-line-diagram UI** | DER-CAM+ [V], XENDEE multi-node [V], ETAP [V] | Nodes carry loads and DER; the investment problem includes power flow. |
| **Catalog / equipment library** | XENDEE PROPOSE (DER, EV, tariffs) [V], ETB (ESS vendor selection) [V], RETScreen product DB [V], Enact ("any brand") [V] | Vendor-specific products with prices. |
| **Side-by-side design comparison** | ETB Proposal Summary [V], Gridcog scenario cash-flow and IRR comparison [V] | Several designs or scenarios in one proposal. |
| **Controller-consistent dispatch** | ETB (forecast-based, same as ETB Controller) [V], ETAP (controller digital twin) [V], XENDEE (OPERATE) [V], Gridcog (perfect foresight vs uncertainty) [V] | Avoids overstating savings from perfect foresight. |
| **Multi-party commercial structure** | Gridcog (participants and cash-flow assignment) [V], XENDEE (EaaS/CaaS) [V], SAM (host/developer, partnership flip) [V], ETB (PPA/lease/loan) [V] | "Who pays, who earns" view. |
| **Portfolio screening** | REopt portfolio [V], RETScreen Portfolio [V], Gridcog multi-site [V] | Prioritise sites before detailed study. |
| **Standardised reference designs** | Schneider Microgrid Flex [V], Vertiv AI Reference Design Selector [V], NVIDIA DSX Ready (Hitachi) [V] | Hardware vendors pre-engineer blocks. Close to our "reference design" idea. |

## 3. Reporting patterns
- **Branded, client-facing proposal generation**: HOMER Grid (logo; PDF, HTML, DOCX, RTF) [V]; ETB (template gallery, web proposals) [V]; XENDEE PROPOSE [V]; Enact [V]; Aurora "lender-ready" [V].
- **Engineer detail report vs client report split**: HOMER Pro [V].
- **Downloadable pro-forma / cash-flow Excel**: REopt [V]; Gridcog [V]; SAM [V].
- **Bankability language** ("lender-ready", "bankable", "regulated index"): Aurora, Chronos, Modo, ETB (±2% vs PVsyst claim) [V]. The buyer wants numbers a lender or investment committee accepts.
- [I] Almost none of these tools advertises PowerPoint output. PDF and Word dominate.

---

## 4. Feature matrix

Legend: **Y** = yes, **P** = partial or limited, **N** = no or not found, **?** = unknown. Cells are [V] unless marked [I].

| Capability | HOMER Pro | HOMER Grid | XENDEE | REopt | DER-CAM | ETB Developer | SAM | RETScreen | Gridcog | Schneider MG Assessment | energyPRO |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Capacity / sizing optimisation | Y (search-space grid + optimiser) | Y | Y (MILP) | Y (MILP) | Y (MILP) | P (user sizes; iterate) [I] | N (parametric only) | N | Y ("optimisation") | Y (multi-objective) | P (scenario sim) [I] |
| Dispatch optimisation | P (rule-based + MATLAB link) | Y (demand-limit) | Y | Y (perfect foresight) | Y | Y (forecast-based EMS-like) | P (battery dispatch options) | N | Y (perfect foresight or uncertainty) | Y | Y |
| Demand charges / TOU / complex tariffs | P (Advanced Grid; critique: weak) | Y | Y | Y | Y | **Y (core strength)** | Y | P | Y (incl. network charges) | Y | P [I] |
| Built-in utility rate database | N | Y (US/CA/MX/AU) | Y (catalog) | Y (URDB, US) | N [I] | **Y (70k–120k rates)** | Y (URDB download) | N | Y (integrated market data) | ? | N [I] |
| Export compensation / net billing | P | Y | ? | Y (NEM3) | Y | Y (NEM programs) | Y | P | Y | ? | Y (market sales) |
| Wholesale / ancillary / DR revenue stacking | P | P | Y | P [I] | Y (ancillary) | Y (grid services) | P (merchant) | N | Y (multi-market) | ? | P (day-ahead) |
| Financing structures (loan, lease, PPA, 3rd-party, EaaS) | P | P | Y (EaaS/CaaS) | P (3rd-party) | N [I] | **Y** | **Y (widest)** | Y (debt) | **Y (multi-party)** | ? | P [I] |
| Tax, depreciation, incentives (ITC, MACRS) | P | Y | Y (IRA) | Y | P | Y | Y | Y | ? | Y (incentives) | P [I] |
| NPV / IRR / payback / LCOE | Y (NPC, LCOE) | Y | Y | Y (NPV savings) | P (annual cost) | Y | Y | Y | Y | Y | Y |
| Annual cash-flow pro-forma export | P | P | Y | **Y (Excel)** | N | Y | Y | Y | Y (downloadable) | ? | Y [I] |
| Multi-year degradation / replacement / escalation | Y (Multi-Year module) | Y [I] | Y [I] | P (single-year extrapolated) | P | Y [I] | Y | P | Y [I] | ? | Y [I] |
| Sensitivity analysis UI | **Y (core)** | Y | P [I] | P | P | P [I] | Y (parametric) | Y (+tornado-like) | Y (scenarios) | Y | Y [I] |
| Monte Carlo / stochastic / P50–P90 | N | P (risk) | P (stochastic) [I] | N | P (stochastic EV) | N [I] | **Y** | **Y** | P (uncertainty) | ? | N [I] |
| Resilience / outage survival / critical load | Y (off-grid) | Y (outage comparison) | Y (N-1) | **Y (survival probability)** | Y (targets) | P [I] | N | N | Y (supply interruptions) | Y (resiliency KPI) | N [I] |
| Off-grid / islanded | Y | N | Y | Y (2024) | Y | N | N | P | Y [I] | Y | Y |
| Electrical network / power flow | N | N | **Y (multi-node, voltage)** | N | **Y (LinDistFlow P,Q)** | N | N | N | N [I] | N [I] | N |
| Multi-energy: heat, CHP, waste heat, absorption chillers | P (CHP module) | N [I] | Y [I] | Y (CHP, GHP, absorption) | **Y** | N | N | Y (heating/cooling) | P [I] | N | **Y** |
| Hydrogen chain (electrolyser, H2 storage, FC) | Y (module) | N | ? | Y (2024) | P [I] | N | P (fuel cell) | P [I] | ? | N | Y (P2X) |
| EV charging / fleets | P | Y | Y (MOBILITY) | Y [I] | Y | P [I] | N | N | Y | Y | N |
| Emissions / carbon price | Y | Y | Y | Y (+health costs) | Y (multi-objective) | P | N | Y (GHG credits) | Y [I] | Y (sustainability KPI) | Y (CO2) |
| Portfolio / multi-site | N | N | P | Y | Y (multi-location) | P | N | Y | **Y** | N | N |
| Equipment / cost catalog | Y (component library) | Y | Y (PROPOSE) | Y (defaults) | Y (defaults) | Y (ESS vendors) | Y (library) | Y (product DB) | Y [I] | Y [I] | Y [I] |
| Branded client report / proposal | P (client proposal) | **Y (PDF/DOCX/HTML/RTF + logo)** | Y (PROPOSE) | P (PDF/Excel) | N | **Y (template gallery)** | P (Excel/graphs) | P (reports) | Y (investment case) | ? | Y [I] |
| Web / cloud + sharing | N (desktop) | Y (web + desktop) | Y (web) | Y (web + API) | P (web client) | Y (web) | N (desktop + SDK) | N (desktop) | Y (web) | ? | N (desktop) [I] |
| API / scripting | Y (MATLAB link) | ? | ? | **Y (API, open source)** | N | ? | **Y (PySAM, LK)** | N | ? | ? | ? |
| Controller linkage (design ↔ operation) | N | N | Y (OPERATE) | N | N | **Y (ETB Controller)** | N | N | P (tracking) | Y (Microgrid Advisor) | N |
| Public price | USD 1,575–4,650/yr | ? | annual contracts, price hidden | Free | Free | ~USD 299–333/user/mo | Free | CAD 869/yr | Tiered, hidden | ? | ? |

---

## 5. What a PyPSA + pandapower GUI typically lacks: ranked "table-stakes"

The ranking weighs how many commercial tools have the feature, how central it is to the sales pitch, and how far a PyPSA stack is from having it. The inferences about PyPSA are mine [I], based on knowledge of PyPSA's design.

1. **A retail tariff engine plus a rate library.** This means monthly or ratcheted **demand charges**, TOU energy, fixed and network charges (for example the EU Leistungspreis and capacity-based grid fees), export compensation and net billing, standby charges and levies. Evidence: ETB, HOMER Grid, REopt, Gridcog and XENDEE all lead with this. PyPSA only knows marginal prices, so a demand charge needs custom max-import-per-period variables, and no tariff database exists for the EU. *Highest priority.*
2. **An investor-grade financial pro-forma.** This means annual N-year cash-flow tables, NPV, IRR (project and equity), payback, LCOE and LCOH, escalation, taxes and depreciation, incentives and grants, debt sizing (DSCR), WACC, and replacement and augmentation reserves, exportable to Excel. PyPSA uses annuitised capital cost only. Evidence: REopt pro-forma, SAM, RETScreen, Gridcog, ETB.
3. **An explicit "baseline vs proposed" framing.** The core output is savings against business-as-usual: a bill before-and-after comparison, a savings waterfall by value stream (demand charge, energy, export, ancillary, avoided genset fuel, resilience). This is universal across HOMER Grid, ETB, REopt and XENDEE.
4. **Branded, client-ready report generation.** This means an executive summary, a detailed engineering appendix, a company logo and templates, in PDF and DOCX, plus a shareable web link. Evidence: HOMER Grid, ETB, XENDEE PROPOSE, Enact, Aurora.
5. **Ownership and commercial-structure modelling.** This means cash, loan, lease, PPA, third-party or EaaS, and multi-party cash-flow allocation (host vs developer vs investor). Evidence: SAM, ETB, Gridcog, XENDEE.
6. **A sensitivity and uncertainty UI.** This means sensitivity grids and tornado charts, scenario side-by-side comparison, and Monte Carlo or P50/P90 on prices, load and yield. Evidence: HOMER, RETScreen, SAM, Gridcog, ETB Proposal Summary.
7. **Resilience and outage economics.** This means critical-load definition, outage duration and survival probability, islanding feasibility, N-1, and a monetised value of lost load or resilience. Evidence: REopt, HOMER Grid, XENDEE, Schneider. [I] Our adequacy and FMEA work is a strength here, but it needs to be surfaced as money and as a probability of survival.
8. **Multi-year lifecycle realism.** This means battery and PV degradation, augmentation and replacement schedules, load growth, and price escalation paths. Evidence: HOMER Multi-Year, HOMER Front, SAM, Chronos. PyPSA's multi-period investment exists, but it does not track degradation.
9. **Curated equipment and cost catalogs with defaults.** This means vendor or product-level libraries (BESS, inverters, gensets, electrolysers, chargers) with regionally sensible default CAPEX and OPEX (ATB-style), and the ability for a company to load its own price book. Evidence: XENDEE PROPOSE, ETB, RETScreen, REopt defaults. [I] This is a big opportunity for an equipment company, since its own product catalog becomes the library.
10. **Realistic, controller-consistent dispatch.** This means forecast-driven or rolling-horizon dispatch with imperfect foresight, and demand-limit logic, so savings are not overstated. Ideally it links to the actual EMS. Evidence: ETB (mirrors ETB Controller), Gridcog, ETAP controller twin, XENDEE OPERATE, and Schneider Microgrid Advisor on the operations side.
11. **Revenue stacking with market data.** This means DR, FCR/aFRR and reserves, capacity, and wholesale arbitrage, with price curves bundled or connected (Modo-style high, central and low curves). Evidence: HOMER Front, Gridcog, Enel X, DER-CAM.
12. **A two-speed workflow.** A fast "proposal" mode (minutes, a wizard with defaults) sits alongside a detailed "engineering" mode. Evidence: XENDEE PROPOSE vs DESIGN, ETB, RETScreen worksheets, Schneider workflow.
13. **Portfolio and multi-site screening.** Evidence: REopt, Gridcog, RETScreen.
14. **Hourly emissions, 24/7 CFE matching and carbon pricing**, including data-centre CFE-percentage reporting. Evidence: REopt (health and climate costs), Google 24/7 CFE practice.
15. **Multi-energy site modelling**: heat and cooling, CHP, heat pumps, absorption chillers and **waste-heat sale**. Evidence: DER-CAM, REopt, energyPRO, nPro. [I] PyPSA can model this natively through Links and multi-carrier buses; the gap is the UI and default templates, not the engine.

**Where we already match or beat the commercial set [I]:** a true electrical network (pandapower load flow and short circuit), which only XENDEE, DER-CAM+ and ETAP approach. Transparent, open optimisation, where HOMER is criticised as a black box. Sector coupling (H2, heat) in one LP or MILP. Adequacy and FMEA, which none of the investment tools offer. Data-centre combined power and heat investment cases, which are white space.

## 6. Implications and suggestions [I]
- Build a **tariff and finance layer on top of the PyPSA results** (post-processing plus a few custom constraints for demand charges and export limits), and a **report generator** (DOCX and PDF with templates). These two close most of the gap.
- Make the **company equipment catalog and price book** the default library. Pair it with the "reference design" as a Schneider-Flex or NVIDIA-DSX-style standardised block.
- For **data centres**, create a template that combines: grid-connection timeline and bridging power (gas or engines plus BESS) → phased grid connection → BESS for load smoothing and grid services → waste-heat sale to district heating (a heat price agreement, the EnEfG reuse share) → 24/7 CFE percentage. No competitor packages all of these together.
- For **hydrogen**, report LCOH with an H2A/H2FAST-style cash flow. Offer electrolyser part-load and stack-replacement schedules, and co-location with BESS and RE (HyDesign, H2Integrate and Honeywell CDO show the bar).
- Present resilience as **survival probability plus monetised outage cost**, as REopt does, drawing on the adequacy and FMEA engine.

## 7. Gaps in this research
- No first-hand page reads of vendor sites because of the egress blocking. Search-summary facts may miss nuance.
- Prices not found for XENDEE, HOMER Grid, Gridcog, energyPRO or Schneider Microgrid Assessment.
- The search budget ran out before more hydrogen SaaS vendors (for example electrolyser OEM sizing tools) and the DNV hydrogen tools could be checked.
