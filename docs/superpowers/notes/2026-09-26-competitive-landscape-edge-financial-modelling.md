# Competitive landscape — financial modelling of energy projects at the grid edge

**Written:** 2026-09-26 · **Companion to:** `2026-09-26-edge-client-feature-benchmark.md`
**Question:** which tools already do "financial modelling at the edge with advanced flexibility",
what exactly do they do, and does that change the plan in the companion note?

Vendor sites are blocked from this environment, so feature claims below come from search-indexed
page text, vendor blogs, reviews and academic comparisons (sources at the end). Treat marketing
claims as claims.

---

## 1. The field in one table

| Tool | Owner / origin | Engine | Edge focus | Flexibility modelling | Commercial layer | Financial outputs | Reliability | Scale-up to large systems |
|---|---|---|---|---|---|---|---|---|
| **Gridcog** | Perth/London start-up; ABB-led $10m round (Jul 2026), DNV Ventures | Cloud; parametric asset models + "billing-grade" tariff/rating engine + optimiser that *represents the control system*, tunable forecast uncertainty; 5-min settlement | **Yes — core** (C&I, multi-site, hubs, EV, data centres) | Demand flexibility & load shifting, EV fleets (probabilistic on-demand chargers), BESS incl. degradation/warranty, gensets, dynamic connection limits / operating envelopes, FCAs, economic curtailment | **Strongest in class**: network tariffs, retail supply, ToU, PPAs (incl. BTM PPA), wholesale exposure, ancillary services, V2X, DR contracts, subsidies; market packs (AU NEM, GB; DE content) | Cashflows and IRR per **participant** ("Participants & Value Flows"), scenario compare, cashflow export by participant × value stream × tariff item; **debt/tax/depreciation are left to the client's own model** (their blog shows how to build one from the export) | None probabilistic | No (site/portfolio scope) |
| **HOMER Pro / Grid / Front** | UL Solutions | Proprietary simulation + enumerative optimisation, hourly | Yes (off-grid → C&I → utility-scale hybrids) | Dispatch strategies (cycle-charging, load-following, predictive), demand-charge reduction, TOU arbitrage | Genability tariff DB (35k+ C&I tariffs, US/CA/MX), tariff builder, ITC/MACRS/state incentives; Front: wholesale/PPA revenue, multi-year dispatch | NPC objective; LCOE, IRR/payback **relative to a base case**, ROI; "investor-grade" reports | Capacity shortage penalty only | No |
| **Xendee** | San Diego; DER-CAM lineage (LBNL) | MILP (design + dispatch), multi-node OPF | Yes (campuses, DC, EV, microgrids; SMR-DC work with U. Illinois) | 25+ technologies, 8 energy domains (elec, heat, cool, DHW, gas, biogas, biomass, H₂), "14 value streams" | Global tariff DB via API, incentives, carbon price, EaaS/CaaS structures | NPV, IRR, LCOE, full financials per optimisation, sensitivity | Resilience/outage scenarios, deterministic | No |
| **NREL REopt** (open, Julia) | NREL | MILP | Yes (buildings, campuses) | Controllable loads, storage, outage survivability | US tariffs (URDB), ITC, MACRS, escalation | NPV, LCC, payback; resilience value | Deterministic outage duration | No |
| **DER-CAM** | LBNL | MILP, multi-objective (cost/CO₂ Pareto) | Yes | DER + building loads | Tariffs, incentives | Annual cost, capacity | Deterministic | No |
| **Energy Toolbase** | Pason | Simulation | C&I solar+storage | Battery dispatch vs tariff, grid-services | 70k+ tariffs, incentives | Savings, payback, IRR | None | No |
| **Ascend PowerVAL / BatterySIMM; Modo Energy; Pexapark** | US / GB-DE / EU | Stochastic price + dispatch | FTM assets | BESS revenue stacking | Market-price forecasts, PPA benchmarks | "Bankable" P50/P90 revenue | None | Portfolio |
| **PLEXOS / Aurora / Antares / PyPSA-Eur** | Energy Exemplar / RTE / open | LP/MILP system models | No | System-level flexibility | Market simulation | System cost, prices | Adequacy (Antares MC) | **Yes — core** |
| **This tool (PyPSA Studio fork)** | Hitachi Energy Power Consulting | PyPSA LP/MILP, perfect foresight, hourly, sector-coupled | Partly (EH archetypes) | DSR resource, storage duration lever, sector-coupling links; **no EV/DC/thermal-process flex archetypes** | **None** (LP duals) | LCOE/LCOS/LCOH, system cost, merchant "profit" | **Strongest in class**: COPT, sequential MC LOLE/EUE, ELCC, FMECA, N-1 redundancy, SCR gate | **Yes** (PyPSA-Eur underneath) |

## 2. Gridcog in depth (the closest positioning match)

Gridcog is the tool that most exactly occupies "financial modelling on the edge with advanced
flexibility". Verified design concepts:

- **Designer / Library / Simulations** product structure. The Library holds reusable inputs:
  interval meter data, network tariff definitions, retail supply arrangements, forward wholesale
  price curves, project costing assumptions, PPA and DR contracts, battery degradation schedules,
  EV charging utilisation assumptions.
- **Participants & Value Flows**: the user declares stakeholders (site owner, developer, DSO,
  tenant, landlord, retailer) and assigns every inflow/outflow to a party; results are cashflows
  and IRR *per participant*, so "who wins" under each commercial structure is a first-class output.
  Their DSO-vs-developer battery post shows co-optimising for the combined NPV of two parties.
- **Billing-grade tariff engine**: utility, regulated-network and retail rate structures, down to
  5-minute settlement and individual tariff items; wholesale energy, network support, reserve
  capacity, ancillary services, environmental certificates.
- **Optimiser as a control-system proxy with tunable forecast uncertainty**: they explicitly
  market avoiding the "perfect-foresight trap"; load and price uncertainty are parameters.
- **Grid constraints as contracts**: static and dynamic import/export limits, active network
  management, dynamic operating envelopes, German FCAs; economic curtailment.
- **Assets**: solar, wind, thermal generation, BESS (AC/DC-coupled), gensets, demand
  flexibility and load shifting, EV chargers and fleets (probabilistic utilisation, scaled into
  the future), V2X.
- **Finance boundary**: cashflows and IRR in-tool; the Cashflow Export Report (participant ×
  value stream × tariff item) feeds the client's own project-finance model for debt, tax and
  depreciation. That boundary is a deliberate product choice, and it is the seam where this
  tool can go further.
- **Customers**: Ampol, Origin, AusNet, Synergy, JET Charge, RACV (AU); Connected Energy,
  Invinity (UK/EU); Energy Pool (NL flexibility); positioning pages for data centres, airports,
  hospitals, multi-site C&I, asset developers, energy majors, distribution networks.
- **Pricing**: subscription by number of modelled sites per month, per jurisdiction; not public.
- **Not there**: probabilistic adequacy, N-1/redundancy design, grid-strength/dynamics gates,
  sector-coupled H₂/heat networks, system-scale models, open engine.

## 3. What the others add to the picture

- **HOMER** remains the reference for off-grid and hybrid sizing and now sells "HOMER Front" for
  FTM revenue (wholesale, structured PPAs) and a data-centre solution page. Its IRR is defined
  relative to a base-case system, and its objective is NPC only — a known limitation. Windows
  desktop, single-user.
- **Xendee** is the most complete *techno-economic* edge optimiser (MILP, multi-node OPF, eight
  energy domains, NPV/IRR/LCOE, incentives, EaaS/CaaS) and is actively courting data centres
  (SMR-microgrid platform). Cloud, tariff API.
- **REopt / DER-CAM** are the open MILP references; REopt.jl's financial and incentive model
  (ITC, MACRS, escalation, resilience value) is a usable open oracle for a US pack, as SAM is for
  project finance.
- **Ascend / Modo / Pexapark** own "bankable revenue" for FTM assets; they are data suppliers to
  this tool, not competitors.
- The **open PyPSA ecosystem** now has PyPSA Labs (2026, Berlin) and PyPSA-Distribution, but no
  commercial edge-finance product; the fork would be the first PyPSA-native one.

## 4. Consistency with the companion note

The companion note's P0 (commercial layer → finance layer → data-centre archetype) is
**confirmed** by the landscape: every credible edge tool has a tariff/contract layer and a
cashflow/IRR layer, and none has this tool's reliability depth. Three things the note
under-weighted and that Gridcog proves matter to buyers:

1. **Multi-participant value flows.** Energy hubs, landlord/tenant, DSO/developer and
   BTM-PPA structures are inherently multi-party. The finance schema should carry a
   `participant` on every cashflow line from day one, even if the v1 UX is single-owner.
2. **Dispatch realism / forecast uncertainty.** PyPSA is a perfect-foresight LP. Gridcog sells
   the opposite. For BESS arbitrage and flexibility revenue, a "realistic controller" mode
   (rolling horizon with forecast error, or a disclosed haircut) is what makes revenue numbers
   defensible to lenders. Not a P0 blocker, but a named phase.
3. **Edge flexibility archetypes beyond DSR%.** EV fleets/chargers, thermal processes and heat
   pumps with storage, and workload-shifting data-centre loads are what "advanced flexibility at
   the edge" means commercially. The companion note covered the data-centre case; EV and thermal
   flex were not in its roadmap.

One boundary decision the landscape does **not** settle and that only the product owner can:
whether to stop where Gridcog stops (per-participant cashflows + IRR + export) or to carry
debt, tax, depreciation and DSCR in-tool. Gridcog's export-to-your-own-model stance is a
legitimate product choice; going further is a differentiator but adds jurisdiction packs and
maintenance.

## 5. Sources

- Gridcog: https://www.gridcog.com/planning-tour ; https://www.gridcog.com/planning-tour/library ; https://www.gridcog.com/planning-tour/simulations ; https://www.gridcog.com/planning-tour/designer ; https://www.gridcog.com/large-energy-users ; https://www.gridcog.com/large-scale-asset-developers ; https://www.gridcog.com/emobility ; https://www.gridcog.com/industries/networks ; https://www.gridcog.com/pricing ; https://www.gridcog.com/market/uk-gb ; https://www.gridcog.com/market/australia-nem
- Gridcog blogs: financial model — https://www.gridcog.com/blog/financial-model ; commercial structures — https://www.gridcog.com/blog/introduction-to-different-commercial-structures-in-energy-projects ; BTM PPA — https://www.gridcog.com/blog/modelling-a-behind-the-meter-power-purchase-agreement-in-gridcog ; battery vs network tariffs — https://www.gridcog.com/blog/optimising-battery-behaviour-in-grid-connected-energy-projects ; perfect foresight — https://www.gridcog.com/blog/importance-of-uncertainty ; uncertainty — https://www.gridcog.com/blog/nobodys-fool-weaving-uncertainty-into-the-fabric-of-our-modelling ; German FCAs — https://www.gridcog.com/blog/how-flexible-connection-agreements-fca-transform-grid-access-for-german-battery-projects ; data centres — https://www.gridcog.com/blog/whats-the-deal-with-data-centres
- Gridcog funding/customers: https://www.ess-news.com/2026/07/14/gridcog-raises-10-million-to-model-storage-and-hybrid-energy-projects/ ; https://www.dnv.com/news/2026/dnvventures-gridcog/ ; https://www.cefc.com.au/case-studies/gridcog-energy-solutions-attract-global-customers/ ; https://www.gridcog.com/reports
- HOMER: https://homerenergy.com/docs/knowledgebase/homer-model/economics/ ; https://www.ul.com/software/homer-grid ; https://homerenergy.com/homer-grid ; https://homerenergy.com/solutions/data-centers ; https://www.capterra.com/p/182590/HOMER-Pro/reviews/
- Xendee: https://xendee.com/design ; https://xendee.com/faq ; https://xendee.com/data-centers ; https://www.datacenterdynamics.com/en/news/xendee-and-university-of-chicago-develop-platform-to-help-data-centers-integrate-microgrids-and-smrs/
- REopt / DER-CAM: https://nrel.github.io/REopt.jl/dev/reopt/outputs/ ; https://docs.nlr.gov/docs/fy26osti/97413.pdf ; https://gridintegration.lbl.gov/der-cam
- Comparisons: https://www.mayfield.energy/technical-articles/microgrids-part-3-microgrid-modeling-software/ ; https://pubsonline.informs.org/doi/10.1287/ijoc.2023.0336
- Ascend / Modo / Fluence / Entrix: https://www.ascendanalytics.com/solutions/powerval ; https://modoenergy.com/product/forecasts ; https://fluenceenergy.com/mosaic-intelligent-bidding-software/ ; https://www.ess-news.com/2026/03/26/entrix-secures-e43-million-investment-eyes-growth-for-flexibility-services-in-iberian-market/
- PyPSA ecosystem: https://pypsalabs.org/ ; https://docs.pypsa.org/latest/home/models/
