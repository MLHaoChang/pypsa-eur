<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# Competitive research: commercial grid-planning & asset-investment software (investment decision-making and study packaging)

*Prepared 2026-09-28 for a PyPSA + pandapower consultant tool (capacity expansion, UC, AC load flow, N-1 screening, adequacy with COPT / sequential Monte Carlo / LOLE / ENS / ELCC, solution-FMEA worksheet, "energy hub reference designs" with strong-grid / weak-grid / off-grid archetypes and cost-vs-availability frontiers, PSS/E .raw handoff to PowerFactory).*

---

## 0. Method and evidence quality (read first)

- **Searches:** about 50 web searches were run before the session's shared web-search budget (200 calls) ran out. Vendor pages were the main sources.
- **Most vendor pages could not be fetched.** The egress proxy blocked WebFetch for energyexemplar.com, auroraer.com, siemens.com, digsilent.de, etap.com, docs.encoord.com, storagewiki.epri.com, niraenergy.com and paces.com. For these vendors the "verified" facts come from search-engine extracts of the vendor page at the cited URL. They were not read in full.
- **Checked first-hand:**
  - **DER-VET:** I shallow-cloned `github.com/epri-dev/DER-VET` and inspected the code.
  - **PRAS:** I fetched `github.com/NREL/PRAS`.
- **Tags used in this report:**
  - **[V]:** verified. The statement is taken from the vendor or primary source at the cited URL, through a search extract or a direct read.
  - **[V-code]:** verified by reading the source code.
  - **[BK]:** background knowledge from before June 2026. It was not re-verified in this session, so treat it as likely but check it.
  - **[INF]:** my inference or judgement.
- **Not researched (budget exhausted):**
  - Bentley OpenUtilities
  - Trimble
  - Vertiv and Schneider data-centre power planning tools, beyond ETAP
  - Resource Innovations (formerly Nexant)
  - Enerdata
  - EPRI US-REGEN

  These are covered only briefly from [BK] or flagged as gaps in this research.

---

## 1. Product-by-product findings

### 1.1 Energy Exemplar: PLEXOS (plus Aurora, PLEXOS Cloud, PLEXOS Intelligence and Pulse)

**Target users [V/BK]**
- Utilities, TSOs and ISOs, IPPs, regulators, traders and consultancies.
- Free academic licences are offered through the Global University Program [V] https://www.energyexemplar.com/global-university-program

**Investment decision outputs**
- **LT Plan capacity expansion [V]:** "automated long-term capacity expansion… on an annual (or user defined) basis." https://www.energyexemplar.com/plexos
- **2026 capacity expansion additions [V]** (https://www.energyexemplar.com/product-news/whats-new-march-2026):
  - LOLP-target improvements and a new "LT LOLP Target algorithm"
  - seasonal reserve-margin targets
  - partial builds
  - **ELCC / EFC accreditation and decomposition**
  - solver and matrix-density speed-ups
- **2026 roadmap items [V]:** energy-only-market long-term revenue modelling, improved nodal modelling, **SDDP**, and LT LOLP logic (same source).
- **Stochastic and Monte Carlo [V]:**
  - Stochastic optimisation over load, inflow and fuel price.
  - Monte Carlo over renewable profiles, giving distributions of production cost, emissions and reliability metrics.
  - "Stochastic dashboards summarize results across hundreds of runs."
  - Sources: https://www.energyexemplar.com/plexos and https://www.energyexemplar.com/blog/monte-carlo-simulation-gas-risk
- **Financial and risk outputs [V]:** new Settlements, FTR and Counterparty Exposure dashboards (March 2026). This is trading and risk functionality moving into the platform. The Adapt2 brand also appears in the August 2026 update title, a trading/ETRM line. https://www.energyexemplar.com/product-news/whats-new-august-2026
- **Other capabilities [BK]:**
  - Phases: LT, MT, ST and PASA
  - Integrated gas, water and hydrogen modelling
  - Build/retire decisions with NPV-style annualised cost
  - Risk-constrained expansion

**Study packaging [V/BK]**
- **Datasets:** "Simulation-ready power datasets for PLEXOS and Aurora" for Europe, North America and other regions [V]. https://www.energyexemplar.com/power-datasets and https://www.energyexemplar.com/blog/plexos-datasets-europe
- **PLEXOS Cloud** [V] (https://www.energyexemplar.com/cloud and the March 2026 notes):
  - collaboration
  - run prioritisation
  - "enhanced Excel integration"
- **Other packaging [BK]:**
  - Scenario/object/membership database with "Scenarios" and "Models" as first-class objects
  - PLEXOS Connect for shared databases
  - A formal Certified PLEXOS Professional programme [V: https://www.energyexemplar.com/learn/certification]

**Results and reporting: AI [V]**
- **PLEXOS Intelligence** is "a suite of AI agents":
  - **Support Agent**: answers modelling questions.
  - **Digital Analyst**: automates visualisations, comparisons and solution summaries.
  - **Automation Agent**: writes Python scripts.
- It also offers:
  - automated diagnostics that "highlight infeasibilities, compare scenarios, and audit key assumptions"
  - "natural-language explanations of model results for stakeholder updates and board review"
- Data stays in the customer tenant and is not used for training.
- Sources: https://www.energyexemplar.com/plexos/intelligence, https://www.energyexemplar.com/product-news/plexos-intelligence-for-faster-smarter-decisionmaking, and the AEP case at https://www.energyexemplar.com/blog/smarter-resource-planning-with-plexos-intelligence
- **PLEXOS Pulse [V]:** daily-refreshed short-term market model behind "an intuitive, AI Agent chat interface… generate bespoke analytics and visualizations — no code required." https://www.energyexemplar.com/plexos/insights

**Aurora (formerly EPIS) [V]**
- Aurora 15 runs desktop cases on PLEXOS Cloud and views cloud results with the same tools as local output. https://www.energyexemplar.com/product-news/aurora-15-plexos-cloud
- The EPIS acquisition date is uncertain: a search extract says 2017, while my [BK] says 2021.

**Integration [BK]**
- Python / .NET API
- CSV/Excel input
- Solution files readable through an API
- No native AC power-flow or dynamics handoff; PLEXOS is a market and production-cost tool.

**Licensing [V/BK]**
- Commercial price is not public [V].
- Industry hearsay puts it at tens of thousands of USD per seat per year [BK/INF].

**Strengths and weaknesses [INF]**
- *Strengths:* the de-facto standard for IRP and market studies, broad feature set, datasets, cloud, AI, and certification.
- *Weaknesses:*
  - Steep learning curve
  - Expensive
  - DC/transport network only, with no AC N-1
  - A black-box feel for clients

### 1.2 Aurora Energy Research: Origin, Amun, Chronos, Lumus, Nodal Explorer, EOS

This is a different model from the others: **software + proprietary forecasts + advisory** from one house.

**Target users [V]**
- Developers, investors, lenders, utilities and TSOs.
- Aurora serves more than 1,000 clients [V].
- Origin has more than 80 companies [V].
- https://auroraer.com/software/origin

**Origin [V]**
- Forecasts "prices, plant dispatch, capacity investments, network flows, and transmission capacity up to 2070."
- Co-optimises dispatch, transmission, balancing and ancillary services, and capacity investment.
- Includes balancing revenues and "weather and technology risk."
- "Simulate and compare custom power market scenarios in minutes, using trusted Aurora data… both deep modelling and easy onboarding."
- Sources: https://auroraer.com/software/origin and https://auroraer.com/eos

**Chronos, battery valuation [V]**
- "Consulting-grade software designed to instantly deliver **bankable** battery valuations."
- Runs Aurora's battery dispatch engine with site specs, degradation cases and balancing-market performance.
- Outputs **gross-margin forecasts and revenue stacks, "delivered within two hours in a consultancy-standard report."**
- Addresses "how much can be borrowed against it", downside scenarios and cash-flow impacts.
- More than 200 users; covers Europe, Australia, the US and Japan.
- Sources: https://auroraer.com/software/chronos and https://auroraer.com/resources/aurora-insights/articles/chronos-bankable-battery-valuations-at-the-click-of-a-button

**Other products [V]**
- **Amun:** site-specific wind valuation combining price, revenue and curtailment. More than 100 subscribers in 17 markets. https://auroraer.com/software/amun
- **Lumus:** PPA pricing and risk. https://auroraer.com/software/lumus
- **Nodal Explorer:** nodal forecasting. https://auroraer.com/software/nodalexplorer

**Reporting and AI [V]**
- The **EOS** platform is the hub for software, data, reports and insights.
- **EOS AI** assistant gives "fast, source-linked answers grounded in Aurora's analysis." https://auroraer.com/eos

**Licensing [INF]**
- Annual subscription per market/product, bundled with research subscriptions. Prices are not public.

**Strengths and weaknesses [INF]**
- *Strengths:*
  - Bankability: lenders accept Aurora curves.
  - Speed and click-button UX.
  - A standardised report is the product.
- *Weaknesses:*
  - Market/price view only, with no physical grid analysis (no AC power flow, no N-1).
  - Closed assumptions.
  - Tied to Aurora's house view.

### 1.3 Hitachi Energy: Velocity Suite, PROMOD, GridView, Capacity Expansion, Asset Modeling, e-mesh; EnCompass clarification

**Target users [V]**
- US utilities, ISOs, IPPs and investors. A European reference case is also offered.

**Capacity Expansion [V]**
- "Resource planning, capacity expansion, and emissions compliance planning… 20 to 30-year comprehensive resource investment plans… RPS and emissions regulations."
- https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/enterprise/capacity-expansion

**PROMOD, GridView and Asset Modeling [V]**
- Nodal/zonal market simulation paired with "investment-grade nodal and zonal data."
- **Asset Modeling** puts PROMOD in the cloud: "run one or thousands of simulations in parallel directly from a browser."
- Sources:
  - https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/enterprise/promod
  - https://www.hitachienergy.com/news-and-events/blogs/2025/11/cloud-powered-asset-modeling-a-game-changer

**Data [V]**
- **Velocity Suite:** energy data, analytics and geospatial maps.
- **Simulation-Ready Data.**
- **Power Reference Cases**, which Hitachi calls "bankable electricity market forecasts"; the European case is updated quarterly.
- Sources:
  - https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/market-intelligence-services/velocity-suite
  - https://www.hitachienergy.com/us/en/products-and-solutions/energy-portfolio-management/energy-advisory-services/power-reference-case/european-power-reference-case

**Risk [V, extract]**
- Stochastic valuations: VaR, CFaR, EaR, PFE, CVA and DVA, in the ETRM / Asset Optimization line. https://www.hitachienergy.com/products-and-solutions/energy-portfolio-management/energy-commercial-enablement-software-solutions/asset-optimization-software-solution

**Data centres [V]**
- Asset Modeling combined with long-term price forecasts helps stakeholders "evaluate siting options, understand where data centers may interconnect sooner, and assess operational exposure."
- In July 2026 Hitachi won a dedicated **110 kV grid-connection** order for the Kauri CAB data centre in Frankfurt.
- Sources:
  - https://www.hitachienergy.com/markets/data-centers
  - https://www.hitachienergy.com/news-and-events/press-releases/2026/07/hitachi-energy-to-power-kauri-cab-digital-infrastructure-data-center-in-frankfurt-with-advanced-grid-connection-solution

**e-mesh [V]**
- e-mesh EMS and PowerStore grid-forming BESS for grid-connected and off-grid use; more than 225 microgrids installed.
- These are operational and hardware products, not planning tools. https://www.hitachienergy.com/news-and-events/press-releases/2021/11/hitachi-energy-releases-global-updates-to-grid-edge-solutions-port-folio-including-new-services

**EnCompass correction [V]**
- The brief lists EnCompass as a Hitachi acquisition. It is not.
- The **EnCompass** resource-planning software from **Anchor Power Solutions was acquired by Yes Energy (4 Dec 2023)**. https://www.yesenergy.com/blog/yes-energy-acquires-anchor-power
- Hitachi Energy's "EnCompass™" is an unrelated **service-agreement** brand. https://www.hitachienergy.com/news-and-events/features/2023/07/hitachi-energy-launches-encompass-agreements
- Yes Energy's EnCompass covers long-term resource plans with transmission constraints, environmental obligations, demand response and storage [V].

**Strengths and weaknesses [INF]**
- *Strengths:*
  - Data plus engine bundle
  - A strong US nodal franchise
  - Cloud parallelism
- *Weakness:* US-centric, and the legacy desktop tools are complex.

### 1.4 Siemens: PSS®E / Gridscale X PSS E, PSS®SINCAL, PSS®ODMS, Gridscale X

**Target users [V]**
- TSO/DSO planners, consultants and industrial network operators.
- PSS SINCAL is used in more than 100 countries [V].

**PSS®E and Gridscale X PSS E [V]**
- Power flow, contingency, PV/QV and transmission reliability.
- **More than 2,000 open Python APIs.**
- The **May 2026** release adds "AI-powered, agentic capabilities" and domain-specific automation.
- **Data-centre / large-load interconnection scenarios**: a redesigned cloud-native UX for connection studies that cuts response times by "up to 50 percent."
- Sources:
  - https://press.siemens.com/global/en/pressrelease/siemens-gridscale-x-redefines-system-operations-and-agentic-transmission-planning
  - https://www.siemens.com/en-us/products/pss-software/gridscale-x-pss-e/agentic-transmission-planning/
  - https://www.engineering.com/siemens-updates-gridscale-x-and-pss-e-software/

**PSS®SINCAL investment modules [V]**
- **Economic Efficiency Calculation (CC):**
  - investment, operation and shutdown costs over planning periods
  - CAPEX/OPEX
  - compares planned variants
  - **NPV (capital value method)**
- **Network Development (LD):** calculates the network at selected future years for target-network planning.
- **Probabilistic Reliability:** analytic and stochastic methods, including restoration strategies.
- Other optimisation and planning modules, such as Compensation Optimization.
- Sources:
  - https://www.siemens.com/en-us/products/pss-software/pss-sincal-electricity/
  - https://www.reliservsolution.net/product/economic-efficiency-calculation-siemens-psssincal-extended-analysis-modules/

**PSS®ODMS [V]**
- Centralised network model management and CIM exchange between planning, EMS/DMS, TSOs and ISOs. This is Siemens' answer to "version control of the network model." https://www.siemens.com/en-us/products/pss-software/pss-odms/

**Gridscale X [V]**
- A T&D "digital twin – a single source of truth for planning, operations, and maintenance." In 2026 it was opened to developers.
- Alliander migrated 85 applications to it.
- **Flexibility Manager** claims up to 20% more grid capacity and up to 40% lower grid investment.
- https://press.siemens.com/global/en/pressrelease/siemens-unveils-flexibility-software-increase-electricity-grid-capacity-moving-towards

**Licensing [V, extract]**
- PSS E monthly subscriptions are sold through an online store. https://www.siemens.com/en-us/campaigns/pss-e-monthly-subscriptions/
- A search extract quoted "PSS E Version 36… 11,660 € / 13,070 $ per month" and "V33… 3,060 €/month." This is unexpectedly high and may be a bundle, so verify it on the page.
- Licences are node-locked with no VMs [V extract]. https://www.siemens.com/en-us/products/pss-software/psse-version-36/

**Strengths and weaknesses [INF]**
- *Strengths:*
  - Industry-standard .raw/.dyr files
  - Regulator acceptance
  - The largest API surface
  - SINCAL has built-in NPV variant comparison
- *Weaknesses:*
  - No market or capacity-expansion optimisation in PSS E
  - Investment economics live in SINCAL (distribution), not in PSS E

### 1.5 DIgSILENT PowerFactory

**Target users**
- TSOs, DSOs, consultants, OEMs and renewable developers. PowerFactory is the dominant tool in Europe, Australia and the Middle East [BK].

**Investment and economics [V]**
- **Economic Analysis Tools / Techno-Economic Calculation (TechEco):**
  - NPV of network-expansion strategies
  - investment costs, cost of losses and interruption costs
  - economic impact of project schedules
  - an "**efficiency ratio evaluation to determine optimal year of investment**"
- **Power Park Energy Analysis** for the economic assessment of renewable parks.
- https://www.digsilent.de/en/economic-analysis-tools.html

**Reliability [V]**
- Probabilistic contingency evaluation giving expected interruption frequencies, **annual interruption costs** and standard indices. It uses protection and restoration modelling, including optimal power restoration.
- **Generation Adequacy Analysis** (Monte Carlo) and Loss of Grid Assessment.
- https://www.digsilent.de/en/reliability-analysis.html

**PowerFactory 2026 [V]**
- Performance work for large networks
- Impedance-based stability for IBRs
- EMT parallelisation
- Modelica
- **Contingency Analysis validity periods for fault cases and remedial action schemes**
- https://www.digsilent.de/en/newsreader/digsilent-releases-powerfactory-2026.html

**Other features [BK]**
- Python API and engine mode
- Quasi-dynamic simulation (time series)
- Unit Commitment and Dispatch Optimisation module
- OPF
- Hosting capacity
- Variations/expansion stages with activation dates: a built-in *version-controlled network development* concept

**"PowerFactory Monitor" [BK/V]**
- This is DIgSILENT's grid-monitoring and fault-recorder hardware line (PFM series), not a planning feature. https://old.digsilent.com.au/pages/products/powerfactory_monitor
- **StationWare** is a web-based protection-settings and asset database that exchanges data both ways with PowerFactory [V]. https://www.pacw.org/digsilent-stationware-and-powerfactory

**Licensing [BK]**
- Perpetual or annual, priced per module plus a maintenance fee. Price lists are not public.

**Strengths and weaknesses [INF]**
- *Strengths:*
  - One model for load flow, N-1, reliability, TechEco, RMS and EMT
  - "Variations and expansion stages" is a natural scenario/version system
- *Weaknesses:*
  - No market-based capacity expansion
  - Economics are simple NPV, with no revenue stacking
  - Desktop-centric
  - Reporting relies on templates, DPL or Python

### 1.6 ETAP (Schneider Electric since 2021)

**Target users [V/BK]**
- Industrial and mission-critical sites (data centres), utilities (ETAP Grid / ADMS), microgrids and BESS.

**ETAP 2026 (May 2026) [V]**
- A "private, high-performance **AI Copilot** and embedded **AI Auto-Complete**."
- Physics-based transient DC arc flash for data centres and BESS.
- https://etap.com/product-releases/etap-2026-release
- https://etap.com/company/news/product-release-news/2026/05/21/etap-announces-etap-2026-powering-continuous-energy-intelligence

**Reliability Assessment [V]**
- EENS and **ECOST (expected interruption cost)** with an **interruption-cost library** and a component parameter library.
- Indices: SAIFI, SAIDI and CAIDI.
- https://etap.com/product/distribution-reliability-assessment

**Digital twin [V]**
- Schneider and ETAP launched a physics-based digital twin (Feb 2026), integrated with the One Digital Grid Platform and ArcFM Web GIS.
- It covers contingency, protection and arc flash, and simulation of switching actions.
- Claims up to "40% faster DER interconnection."
- https://www.se.com/us/en/about-us/newsroom/news/press-releases/Schneider-Electric-and-ETAP-Launch-PhysicsBased-Digital-Twin-to-Bridge-Design-and-Operations-for-Utilities-and-Critical-Infrastructure-69655b6d86c928ffc30e9cab/

**Data centres [V]**
- Design-to-operations digital twin for data-centre power systems.
- An **AI-factory digital twin with NVIDIA Omniverse** (electrical, thermal and mechanical).
- https://etap.com/industries/data-center
- https://www.arcweb.com/blog/etap-schneider-electric-unveil-digital-twin-simulate-ai-factory-power-requirements-using

**Other features [BK]**
- eMT (EMT)
- Microgrid controller and sizing
- Library-heavy model building

**Strengths and weaknesses [INF]**
- *Strengths:*
  - Facility and data-centre design
  - Safety and compliance (arc flash)
  - Design-to-operations continuity
  - Interruption-cost library
- *Weaknesses:*
  - Weak on bulk-system market economics, capacity expansion and adequacy LOLE

### 1.7 GE Vernova: GridOS (and the Concorda planning suite [BK])

- **GridOS for Transmission (June 2026) [V]:**
  - unified operations intelligence: AEMS, DDLR, WAMS and forecasting
  - a whitepaper, "AI in Grid Planning," covering long-range planning, interconnection analysis and risk management "anchored by a living digital grid twin"
  - https://www.gevernova.com/news/press-releases/ge-vernova-introduces-gridosr-transmission-new-ai
- **Planning tools [BK]:** GE's classic planning tools are PSLF (load flow and dynamics), **MARS** (Multi-Area Reliability Simulation, used by NYISO and others for LOLE) and MAPS (production cost), sold as the "Concorda" suite. MARS is a direct commercial comparator to our adequacy module.
- **Weakness [INF]:** GridOS is operations-first; planning is more whitepaper than product.

### 1.8 encoord SAInt

- **Scope [V]:** integrated electricity, gas, heat and cooling on one data structure. It covers:
  - **Capacity Expansion Modeling** of generation, storage and transmission investments and retirements
  - operations and markets
  - physical simulation of electric and gas networks
  - gas-electric coupling
  - stranded-asset analysis for gas networks under electrification
  - https://www.encoord.com/saint and https://www.encoord.com/resources/blog/saint-3.5
- **Target users [INF]:** US utilities, ISOs and gas pipeline operators; consultants on gas-electric coordination.
- **Strengths and weaknesses [INF]:**
  - *Closest overall analogue to our tool:* capacity expansion, unit commitment and AC physics in one platform.
  - *Weaknesses:* smaller ecosystem, and investment economics are cost-minimisation only (no project finance).

### 1.9 Artelys Crystal Super Grid

- **Scope [V]:**
  - A **web-based** platform to "assess costs and benefits of infrastructure projects and optimize investments in generation assets, grids and flexibility."
  - Automatic optimal investment.
  - **Monte Carlo adequacy** across climate years and outage draws.
  - Multi-energy: electricity, gas, H2 and heat.
  - Used by Swissgrid, the JRC and Litgrid (NRAA and flexibility-needs assessment following **ENTSO-E ERAA** methodology).
  - Sources:
    - https://www.artelys.com/crystal/super-grid/
    - https://www.artelys.com/news/lithuania-energy-security-nraa-fna/
    - https://www.artelys.com/news/swissgrid-artelys-crystal-super-grid/
- **2026 release [V]:**
  - extensive logs for diagnosing infeasible runs
  - an expanded asset library
  - stochastic simulations over VRE, outages and policy
  - https://www.artelys.com/news/artelys-crystal-super-grid-new-release-2026/
- **Deployment [V]:** a cloud version and a sovereign HPC platform.
- **Strengths and weaknesses [INF]:**
  - *Strengths:* European TSO-grade CBA and adequacy in a browser.
  - *Weaknesses:* no AC load flow or N-1, and aimed at TSOs and ministries rather than project finance.

### 1.10 Antares Simulator, Antares-Xpansion and Antares Web (RTE; open source)

- **Antares Simulator [V]:**
  - MPL-2.0
  - Sequential Monte Carlo, hourly, for large interconnected systems
  - https://antares-simulator.org/pages/software-presentation/1/
- **Antares-Xpansion [V]:**
  - optimises investment in new capacity and transmission lines (Benders)
  - has an experimental GUI
  - https://github.com/AntaresSimulatorTeam/antares-xpansion
- **Antares Web (AntaREST) [V]:**
  - a web app with **user accounts and permissions**
  - a **variant manager** built on an "edition event store that tracks changes… explicit diff change comparisons between studies"
  - a REST API
  - This is the best open-source example of study version control.
  - https://antares-web.readthedocs.io/en/latest/user-guide/0-introduction/
- **Strengths and weaknesses [INF]:**
  - *Strengths:* free, and a TSO-grade adequacy engine used in European adequacy work [BK].
  - *Weaknesses:* no project-level finance, and no AC or N-1.

### 1.11 NREL PRAS (open source)

- **[V]** Julia package; sequential Monte Carlo; EUE and shortfall metrics; **capacity-credit module (PRASCapacityCredits.jl)**. https://github.com/NREL/PRAS
- **[INF]** Our adequacy stack already matches PRAS's scope.

### 1.12 EPRI DER-VET (open source, with an LBNL/CEC lineage)

Checked by reading the code (`epri-dev/DER-VET`, v1.3.0, last commit 18 Dec 2024).

**Value streams [V-code]**
- Implemented in `storagevet/ValueStreams/`:
  - day-ahead energy time shift and energy time shift
  - frequency regulation
  - spinning and non-spinning reserve
  - load following
  - demand response
  - **resource adequacy**
  - **T&D deferral**
  - volt-var
  - backup
  - demand-charge reduction
  - generic market services (up, and up and down)
  - user constraints
- `dervet/MicrogridValueStreams/Reliability.py` performs **reliability sizing** for a target outage duration. It picks the worst critical-load windows, runs an iterative outage simulation, and sizes storage to cover them.

**Finance [V-code]**
- `dervet/CBA.py` builds a **pro forma** that includes:
  - replacement costs
  - end-of-life value
  - construction-year CAPEX
  - taxes (federal, state and property, with **MACRS depreciation**)
- It reports **NPV, payback and discounted payback, MIRR, and levelised cost of H2**.

**Other points [V]**
- It co-optimises DER sizing with dispatch.
- The GUI is a separate Electron/Vue application. https://github.com/epri-dev/DER-VET
- CEC report: https://www.energy.ca.gov/publications/2024/validated-transparent-and-accessible-microgrid-valuation-and-optimization-tool
- **Licence [V-code]:** BSD-3.

**Strengths and weaknesses [INF]**
- *Strengths:* the reference open implementation of **storage/DER value stacking plus a pro forma**.
- *Weaknesses:* site-level only (no network), US-tariff/market-centric, and slow development.

### 1.13 Other valuation and microgrid tools: NREL REopt and UL HOMER

- **REopt [V]:**
  - optimal mix for savings or resilience
  - an **outage simulator** that runs an outage starting at each of the 8,760 hours and reports **probability of survival versus outage duration** (by month and hour of day)
  - compares a system "sized to maximize savings" with one "sized for resilience" to expose the **cost gap**
  - a public API
  - Sources: https://docs.nrel.gov/docs/fy20osti/76678.pdf and https://developer.nrel.gov/docs/energy-optimization/reopt/
- **HOMER Pro and HOMER Front (UL Solutions) [V]:**
  - hybrid microgrid simulation and optimisation from 1-minute to 1-hour steps
  - **sensitivity analysis on almost any variable**
  - NPC and LCOE
  - HOMER Front for utility-scale hybrid / front-of-meter projects
  - https://www.ul.com/software/homer-microgrid-and-hybrid-power-modeling-software
  - **Pricing [BK]:** annual subscription with public price tiers.
- **[INF]** These are the direct comparators for our **off-grid and weak-grid archetypes**. Both output survival curves or NPC-vs-renewable-fraction trade-offs. HOMER's "optimisation results table + sensitivity plots" is the UX consultants already know.

### 1.14 Ascend Analytics: PowerSIMM, BatterySIMM, SmartBidder

- **PowerSIMM Planner [V]:**
  - capacity expansion and resource selection
  - reliability of renewables and storage
  - sub-hourly battery value
  - https://www.ascendanalytics.com/solutions/powersimm
- **BatterySIMM [V]:** storage valuation. https://www.ascendanalytics.com/solutions/batterysimm-suite
- **SmartBidder [V]:** probabilistic and AI-assisted bid optimisation. https://www.ascendanalytics.com/solutions/smartbidder
- **Differentiator [BK/INF]:** stochastic **price and weather simulations** with risk-adjusted valuation (P-values, CFaR), linking planning through procurement to operations.

### 1.15 Resource-adequacy and expansion specialists

- **PowerGEM, which acquired Astrapé (Apr 2024) [V]:**
  - **SERVM** is a Monte Carlo SCED production-cost and adequacy tool, and the "primary resource adequacy tool for a majority of ISOs in North America."
  - It does ELCC and full economic modelling, so users can "select expansion plans that properly weight reliability and economic attributes."
  - **TARA** covers transmission adequacy.
  - Sources: https://www.businesswire.com/news/home/20240430015028/en/PowerGEM-LLC-Acquires-Astrap-Consulting and https://www.astrape.com/servm/
- **E3 RECAP [V]:** LOLP model for portfolio and marginal ELCC. https://www.ethree.com/tools/recap-renewable-energy-capacity-planning-model/
- **PSR OptGen and SDDP [V]:**
  - least-cost expansion with **mutually exclusive projects, associative constraints and precedence between investments**
  - iterates with SDDP stochastic hydro-thermal dispatch on a shared database
  - https://www.psr-inc.com/en/software/optgen/
- **Yes Energy EnCompass [V]:** see §1.3.

### 1.16 Interconnection and grid-connection feasibility SaaS (including large loads and data centres)

| Product | What it does | Evidence |
|---|---|---|
| **Nira Energy** (US) | Pulls ISO power-flow models, runs **injection studies at every substation**, replicates ISO study methods. Offers map-based **Prospecting** with **upgrade-cost estimates**. Now also serves **large-load teams** finding POIs, constraints, timelines and costs. Demo only, no public pricing. | [V] https://www.niraenergy.com/company ; https://www.latitudemedia.com/news/how-nira-energy-is-using-software-to-unclog-the-interconnection-queue/ |
| **Paces** (US) | "AI-powered software and services" for siting and interconnection. **Data Center Score** rates sites 1–5 on 8 factors, with interconnection weighted highest. Transmission heatmaps of available MW. Automates "power studies, permitting analysis, infrastructure due diligence." Sold as **Self-Service and Managed** tiers. | [V] https://www.paces.com/data-center-developers ; https://www.paces.com/products/self-service ; https://www.paces.com/products/managed |
| **GridUnity** (US) | GridInterConnect manages the interconnection lifecycle for utilities and ISOs, pushes applications into power-flow models, and automates **cluster studies**. Customers include CAISO, Southern Company and Xcel. | [V] https://www.gridunity.com/gridinterconnect-for-transmission |
| **Pearl Street** (SUGAR), acquired by **Enverus in March 2025** | Automates interconnection studies. Used by MISO. | [V] https://pearlstreettechnologies.com/ ; https://www.rtoinsider.com/131286-miso-sets-up-study-sugar-automation-later-phases-interconnection-queue/ |
| **envelio IGP** (DE) | DSO platform processing about 10,000 connection requests a month. **Online Connection Check** for customers and a **Grid Connection Navigator** (MV/HV capacity). Requests that took 3 hours now take 15 minutes. More than 60 utilities, including E.ON. | [V] https://us.envelio.com/igp ; https://envelio.com/use-cases/grid-connection-navigator |
| **Kevala** (US) | Grid assessment platform: hosting capacity, queue data, bottom-up DER and electrification forecasts, integrated grid planning. | [V] https://www.kevala.com/platform ; https://www.kevala.com/solutions/integrated-grid-planning |
| **Camus Energy FlexConnect** (US) | Planning analytics plus real-time controls for **flexible (non-firm) connections**. Claims "up to 3x more data center capacity in two years." | [V] https://www.camus.energy/flexconnect |
| **Feasibly** and **Keen AI / SP Energy Networks** (UK) | AI site-viability tools. Feasibly unifies data from all UK DNOs (982,087 assets). | [V] https://feasibly.co.uk/ ; https://www.solarpowerportal.co.uk/solar-projects/sp-energy-keen-ai-announce-ai-powered-grid-connection-tool |
| **ENIAN** (UK) | Project Manager platform for solar, wind and storage development. Uses AI for grid connection and nearest-substation insight. | [V] https://www.cbinsights.com/company/enian |

Also relevant for data centres [V]:
- Siemens PSS E large-load workflows (§1.4)
- Hitachi data-centre grid connections and siting analytics (§1.3)
- ETAP / NVIDIA AI-factory twin (§1.6)

### 1.17 Not researched in depth (budget exhausted), or out of scope

- **ABB Ability OPTIMAX [V]:** an operational EMS for industrial sites, microgrids and H2 (forecasting, predictive control, claims up to 20% cost reduction). It is *not* a planning tool. https://www.abb.com/global/en/areas/automation/solutions/industrial-software/energy-management/energy-optimization-optimax
- **Bentley OpenUtilities, Trimble, Vertiv and Schneider data-centre power planning, Resource Innovations (Nexant), Enerdata, EPRI US-REGEN:** not verified this session. From [BK]:
  - Bentley and Trimble are GIS and design digital twins with no investment analytics.
  - US-REGEN is an EPRI research model, not a product.
  - Resource Innovations sells DER / IRP consulting and grid-planning tools.

---

## 2. Capability matrix

**Legend:** ● strong/native · ◐ partial or via add-on/service · ○ absent · ? unknown.
**Us** = our PyPSA/pandapower tool as described in the brief.

| Capability | PLEXOS | Aurora ER (Origin/Chronos) | Hitachi (PROMOD/CE/Asset Modeling) | Siemens PSS E + SINCAL | PowerFactory | ETAP | SAInt | Artelys CSG | Antares (+Xpansion/Web) | DER-VET / REopt / HOMER | **Us** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Capacity expansion (least-cost) | ● | ● | ● | ○ | ○ | ○ | ● | ● | ● | ◐ (site sizing) | ● |
| Unit commitment / production cost | ● | ● | ● | ○ | ◐ (UC module [BK]) | ○ | ● | ● | ● | ◐ | ● |
| AC load flow | ○ | ○ | ○ (GridView DC) | ● | ● | ● | ● | ○ | ○ | ○ | ● |
| N-1 / contingency | ◐ (DC security) | ○ | ◐ (nodal DC) | ● | ● | ● | ◐ | ○ | ○ | ○ | ● |
| Dynamics / EMT | ○ | ○ | ○ | ● | ● | ● | ○ | ○ | ○ | ○ | ○ (handoff via .raw) |
| Adequacy: LOLE/EUE by sequential Monte Carlo | ● (PASA, LOLP) | ◐ | ◐ | ◐ (transmission reliability) | ● (gen adequacy) | ◐ | ? | ● | ● | ◐ (REopt survival) | ● |
| ELCC / EFC accreditation | ● (2026) | ◐ | ? | ○ | ○ | ○ | ? | ◐ | ◐ | ○ | ● |
| Network reliability indices with interruption cost (ECOST / VoLL) | ○ | ○ | ○ | ● (SINCAL) | ● | ● (cost library) | ? | ◐ | ◐ | ◐ (REopt VoLL) | ◐ (ENS without monetisation [INF]) |
| NPV of network variants / investment timing | ◐ | ○ | ◐ | ● (SINCAL CC) | ● (TechEco, optimal year) | ◐ | ◐ | ● (CBA) | ◐ | ● | ◐ ([INF]: frontier, but no NPV/timing) |
| Project pro forma (tax, depreciation, IRR, payback, debt) | ○ | ● (Chronos, bankable) | ◐ | ○ | ○ | ○ | ○ | ○ | ○ | ● (DER-VET code) | ○ |
| Revenue / value stacking (energy, AS, capacity, deferral) | ◐ | ● | ◐ | ○ | ○ | ○ | ◐ | ◐ | ○ | ● | ○/◐ |
| Stochastic price/weather risk on economics (P50/P90, VaR, CFaR) | ● | ● | ● (ETRM line) | ○ | ○ | ○ | ◐ | ● | ● (MC years) | ◐ (HOMER sensitivity) | ◐ (MC on outages only [INF]) |
| Resilience valuation (outage survival, value of resilience) | ○ | ○ | ○ | ◐ | ◐ | ◐ | ◐ | ○ | ○ | ● (REopt, DER-VET) | ◐ (FMEA + availability) |
| Curated assumption / market datasets | ● | ● | ● | ○ | ○ (libraries) | ● (equipment library) | ? | ◐ | ◐ | ◐ (URDB tariffs) | ○ |
| Scenario / variant management with diff | ● | ◐ | ◐ | ● (ODMS) | ● (variations) | ◐ | ● | ● | ● (event-store diff) | ○ | ? |
| Cloud / parallel batch | ● | ● (SaaS) | ● | ● (2026) | ○/◐ | ◐ | ◐ | ● | ◐ (Antares Web) | ● (REopt web) | ? |
| Report builder / consultancy-grade report | ◐ (dashboards) | ● (auto report) | ◐ | ◐ | ◐ | ◐ | ? | ◐ | ○ | ◐ | ? |
| AI copilot / natural-language summaries | ● (Intelligence, Pulse) | ● (EOS AI) | ○/? | ● (agentic, 2026) | ○ | ● (Copilot 2026) | ? | ○ | ○ | ○ | ○ |
| Large-load / data-centre connection workflow | ◐ | ○ | ● | ● | ◐ | ● (facility side) | ○ | ○ | ○ | ○ | ◐ (hub archetypes) |
| Openness / price | $$$$ | subscription $$$ | $$$$ | $$$ (monthly store) | $$$ perpetual | $$$ | $$ ? | $$$ | free | free / $ | open |

---

## 3. What these vendors do for investment decisions and study packaging that we still lack (ranked)

The ranking is by value to a consultant writing an investment recommendation, weighted by how often the capability recurs across vendors [INF].

1. **A project-finance layer: pro forma, then NPV, IRR/MIRR, payback, LCOE/LCOS and DSCR.**
   - Who has it: Chronos ("how much can be borrowed against it"), DER-VET (tax, MACRS, replacement, end-of-life, MIRR, discounted payback), HOMER (NPC, LCOE), SINCAL CC and PowerFactory TechEco (NPV of variants).
   - What we have: cost-vs-availability frontiers, which are engineering cost, not an investable case.
   - What to add: CAPEX phasing, replacement cycles (battery augmentation), degradation, escalation, WACC, tax and depreciation, salvage, and financing (gearing, DSCR). Output a standard pro-forma sheet per hub design.
2. **Monetised reliability on the same axis as cost.**
   - Who has it:
     - ETAP ECOST with an interruption-cost library
     - PowerFactory's annual interruption costs in TechEco
     - SINCAL probabilistic reliability
     - REopt's savings-sized versus resilience-sized cost gap
   - What to add: VoLL and customer-damage-function libraries by sector (data centre, hospital, industry). Converting our ENS/LOLE into €/yr turns the cost-vs-availability frontier into a **total-cost-of-ownership optimum**, which is what a client decides on.
3. **Value stacking and revenue modelling for storage and hubs.**
   - Who has it: DER-VET (about 15 value streams, including RA, T&D deferral and backup), Chronos (balancing markets), Ascend BatterySIMM, and PLEXOS energy-only-market revenue modelling (2026 roadmap).
   - What to add: stack arbitrage, ancillary services, capacity payments, deferral and backup value, and show a revenue waterfall.
4. **Uncertainty on the economics, not just on outages.**
   - Who has it:
     - stochastic price, weather and fuel scenarios (PLEXOS, Aurora, Ascend, Artelys)
     - distributions of NPV/cost, P50/P90, VaR/CFaR (Hitachi, Ascend)
     - tornado / sensitivity sweeps (HOMER's hallmark)
     - downside cases (Chronos)
   - What to add: a scenario-sweep runner that produces NPV distributions and a sensitivity/tornado view per archetype.
5. **Curated assumption libraries with provenance.**
   - Who has it: every commercial leader sells data with the engine:
     - PLEXOS datasets
     - Hitachi Velocity Suite and quarterly **bankable** reference cases
     - Aurora house-view curves
     - ETAP equipment and cost libraries
   - What to add: a versioned cost catalogue (for example Danish Energy Agency or NREL ATB-style, with vintage and source), VoLL tables, failure-rate / MTTR libraries for the FMEA and COPT, and price-scenario sets. Each needs citations so a study is auditable.
6. **Study-level scenario and variant management with diff and lineage.**
   - Who has it:
     - Antares Web event-store variants with explicit diffs
     - PowerFactory variations and expansion stages
     - Siemens PSS ODMS / Gridscale X "single source of truth"
     - PLEXOS Scenarios and Cloud collaboration
   - What to add: named cases, parent/child variants, assumption diffs shown in the report, and locked "issued" versions.
7. **A consultancy-standard report generator plus an executive summary.**
   - Who has it:
     - Chronos: "consultancy-standard report within two hours"
     - PLEXOS stochastic dashboards and Excel integration
     - Paces "Managed" deliverables
   - What to add: a one-click PDF/Word/PowerPoint and Excel pack per study (assumptions, method, results, frontier chart, N-1 table, FMEA register, recommendation).
8. **An AI assistant for diagnostics and narrative.**
   - Who has it: PLEXOS Intelligence (infeasibility diagnosis, assumption audit, scenario comparison, board-ready natural-language summaries, script generation), ETAP 2026 Copilot, Siemens agentic PSS E, and Aurora EOS AI (source-linked answers). Artelys added rich infeasibility logs for the same pain point.
   - What to add: an LLM layer grounded in our result files that explains why a design wins and what drives the result. It must be private or on-prem, which all of these vendors emphasise.
9. **Large-load and interconnection screening as a packaged study type.**
   - Who has it:
     - Nira (injection studies at every substation, plus upgrade cost)
     - Paces (Data Center Score, MW heatmaps)
     - Siemens PSS E large-load connection-study UX (50% faster)
     - envelio Online Connection Check
     - Camus FlexConnect (non-firm/flexible connection sizing)
   - What to add: a "POI headroom map" and upgrade-cost estimate, plus a **flexible-connection option** (curtailable MW-hours versus connection date) as an archetype dimension alongside strong-grid, weak-grid and off-grid.
10. **Accreditation coupled to expansion.**
    - Who has it: PLEXOS 2026 **ELCC/EFC decomposition inside LT Plan with LOLP targets**; SERVM "weighting reliability and economics" in plan selection.
    - What to add: we compute ELCC after the fact. Adding an iterative CEM ↔ adequacy loop (a reliability-constrained expansion) would close this gap.
11. **Investment timing, staging and project logic.**
    - Who has it: PowerFactory TechEco's optimal year of investment and efficiency ratio; OptGen's mutually exclusive projects, precedence and associative constraints.
    - What to add: multi-period staging with real-option style "build now vs defer" comparisons.
12. **Cloud scale-out for thousands of runs.**
    - Who has it: Hitachi Asset Modeling ("thousands in parallel from a browser"), PLEXOS Cloud, Artelys HPC, REopt API.
    - What to add: we need a batch/queue back-end for sweeps and Monte Carlo.
13. **Ecosystem packaging** (lower priority, but a real differentiator):
    - certification and training (PLEXOS)
    - free academic licences
    - open APIs (Siemens: more than 2,000)
    - a "software + advisory" bundle (Aurora, Hitachi, Paces Managed)
14. **Multi-stage stochastic hydro (SDDP) and gas coupling** (PSR, PLEXOS, SAInt). This matters only for hydro- or gas-dominated clients.

**Where we already lead or match [INF]:**
- **One engine chain for expansion → UC → AC load flow → N-1 → adequacy/ELCC.** Only SAInt comes close. Market tools (PLEXOS, Aurora, Hitachi) lack AC N-1, and grid tools (PSS E, PowerFactory, ETAP) lack capacity expansion.
- **The FMEA worksheet.** No vendor found advertises one.
- **Archetype reference designs with cost-vs-availability frontiers.** These are closest to HOMER's optimisation table, but grid-aware.
- **Open, auditable assumptions.**

---

## 4. "Guided workflow / for non-experts" UX that vendors advertise

- **Aurora Chronos:** "bankable battery valuations at the click of a button." Site specs in, report out within two hours. [V]
- **Aurora Origin:** "simulate and compare custom… scenarios in minutes… easy onboarding," with pre-loaded house data. [V]
- **PLEXOS Pulse:** "AI Agent chat… no code required." PLEXOS Intelligence Digital Analyst auto-builds charts and comparisons; the Automation Agent writes Python. [V]
- **ETAP 2026:** AI Copilot plus **AI Auto-Complete** while building models. [V]
- **Siemens Gridscale X PSS E:** "redesigned, cloud-native user experience" for connection studies, and agentic automation. [V]
- **envelio:** a public **Online Connection Check** that lets end customers self-screen before applying, and a Grid Connection Navigator. [V]
- **Paces:** **Self-Service vs Managed** tiers, a map, search, a pipeline manager, and a 1–5 site score. [V]
- **Nira:** map-based Prospecting. **Feasibly:** "viability… in seconds rather than weeks." [V]
- **REopt:** a web tool with a public API, simple site inputs and survival-probability charts. **HOMER:** an optimiser plus sensitivity tables that hide the search. **DER-VET:** an Electron GUI over a CSV-parameter engine. [V]
- **Antares-Xpansion:** an experimental GUI. Antares Web adds browser editing and variants for non-programmers. [V]

**Pattern [INF]:** the market has split in two.
- **Engines for experts** (PLEXOS, PSS E, PowerFactory) are adding AI copilots on top.
- **Answer products for decision-makers** (Chronos, Paces, Nira, envelio check) standardise the inputs, hide the model and sell a report or a score.

A consultant tool can straddle both. It could offer guided study templates (for example "data-centre hub connection", "off-grid hub sizing", "storage value case") that generate a fixed report, with an expert mode underneath.

---

## 5. Licensing and price signals (summary)

| Product | Price signal |
|---|---|
| PLEXOS | Not public. Academic licences free [V]. Tens of thousands of USD per seat per year [BK/INF]. |
| Siemens PSS E | Monthly subscription through the online store. A search extract quotes about €11.7k/month for v36 and €3.1k/month for v33 [V-extract; verify]. Node-locked. |
| PowerFactory, ETAP, SINCAL | Perpetual or annual, priced per module; not public [BK]. |
| Aurora ER | Subscription per market and product, bundled with research; not public [INF]. |
| Nira | Demo only [V]. |
| Paces | Self-service and managed tiers [V]. |
| DER-VET, REopt, Antares, PRAS | Free and open source: DER-VET BSD-3 [V-code], Antares MPL-2.0 [V]. |
| HOMER | Paid annual subscription with public tiers [BK]. |

---

## 6. Corrections to the brief

- **EnCompass** belongs to **Yes Energy**, which acquired Anchor Power Solutions in December 2023. Hitachi's "EnCompass" is a service-agreement brand. [V]
- **"PLEXOS Copilot"** is marketed as **PLEXOS Intelligence**, with Support Agent, Digital Analyst and Automation Agent, alongside **PLEXOS Pulse**. [V]
- **"PowerFactory Monitor"** is DIgSILENT's grid-monitoring hardware, not a planning module [BK]. The relevant planning modules are **Reliability & Restoration**, **Generation Adequacy** and **Techno-Economic Calculation**. [V]
- **Pearl Street (SUGAR)** is now **Enverus** (March 2025). **Astrapé (SERVM)** is now **PowerGEM** (April 2024). [V]
