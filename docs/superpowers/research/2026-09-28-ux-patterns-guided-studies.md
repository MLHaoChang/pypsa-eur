<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# UX research: guided investment studies for an expert energy-system modelling GUI (PyPSA-based)

*Research date: 2026-09-28. Agent D3.*

## 0. Method and how much to trust this

- I ran about 55 web searches covering techno-economic tools, UX guidance, AI copilots, financial-modelling tools and report generation.
- **Limitation:** the sandbox's egress proxy blocked every `WebFetch` I tried. That included nlr.gov/docs.nlr.gov, homerenergy.com, support.ul-renewables.com, nngroup.com and arxiv.org. So I could not read whole primary pages. The evidence below comes from search-engine extracts of the named primary-source URLs: product docs, help centres, NN/g, GOV.UK, ACM/CHI, NREL/NLR and IRENA.
- **Labels used below:**
  - **[V]** means verified: the claim appears in the extract of the cited primary or near-primary source.
  - **[V-2]** means it comes from a secondary source, such as a review site, blog or vendor-marketing page.
  - **[I]** means inference or design judgement by me.
- If someone wants to harden a claim before quoting it externally, they should open the cited URL. The HOMER docs, the REopt user manual PDF and the NN/g articles are the most useful ones to read in full.
- **Naming note [V]:** as of 2026, the lab formerly called NREL appears in its own news as the "National Laboratory of the Rockies (NLR)", with REopt hosted at reopt.nlr.gov. Older URLs are still at nrel.gov.

---

## 1. How the best techno-economic tools guide novices

### 1.1 NREL/NLR REopt Web Tool (the closest analogue to what we want)

**Step sequence and inputs**
- **[V]** The first choice is the goal: a *Financial* or a *Resilience* evaluation.
  - A Financial evaluation "will identify the system that minimizes your cost of energy".
  - A Resilience evaluation does the same "but with an added constraint that the system must be able to sustain your site's critical load during an outage".
  - Source: [REopt user guides](https://reopt.nrel.gov/user-guides.html), [REopt Lite User Manual (OSTI)](https://www.osti.gov/biblio/1770888), [REopt tutorial: Resilience Inputs](https://docs.nlr.gov/docs/fy20osti/76676.pdf).
- **[V]** The mandatory inputs are deliberately few:
  - site location and electricity rate (the "Site and Utility" section);
  - either interval load data, or a building type plus annual consumption (the "Load Profile" section).
  - Everything else has defaults. Source: [REopt user manual PDF](https://reopt.nlr.gov/tool/reopt-user-manual.pdf), [Tutorial: Inputs Overview](https://www.nlr.gov/reopt/curriculum/videos/reopt-lite-tutorial-module-1-text).
- **[V]** The user manual documents "data inputs, results, default values, and links to additional technical references". This gives the tool a single place where its default and assumption library is written down.
- **[V]** In 2026 the web tool added air-source and ground-source heat pumps. NLR describes it as a "holistic, technology-neutral evaluation… weigh the benefits and trade-offs, and identify the synergies between technologies" ([NLR news, Mar 2026](https://www.nlr.gov/news/detail/program/2026/reopt-expands-options-to-help-energy-managers-cut-costs)).

**Results and verdict**
- **[V]** The central results artefact is a **Results Comparison table**. It sets the **business-as-usual case** ("what you would expect to pay if you didn't install these technologies") against the **optimal case**, with a difference column. **NPV sits at the bottom** as "the savings over the 25-year analysis period" ([Tutorial: Financial Outputs](https://docs.nlr.gov/docs/fy20osti/76677.pdf)).
  - The verdict is therefore implicit and relative: "the optimal system saves X versus doing nothing".
- **[V]** For resilience, an **outage simulator** runs 8,760 outages (one starting at every hour of the year). It plots **the probability of surviving an outage of length X**, also broken down by start month and hour. This is a clean way to show that "it depends when it happens" as a distribution rather than a single number ([REopt Lite framework paper](https://arxiv.org/pdf/2008.05873), [Tutorial: Resilience Outputs](https://docs.nlr.gov/docs/fy20osti/76678.pdf)).

**Reports**
- **[V]** There is a "Download PDF" button for the results page, plus a **pro-forma Excel spreadsheet** with the detailed financials ([Tutorial: Financial Outputs](https://docs.nlr.gov/docs/fy20osti/76677.pdf)).
- **[V]** Newer versions let users run many evaluations in parallel, for portfolio screening or sensitivity. Users can select several evaluations and export them to Excel to build comparison tables ([REopt software updates](https://www.nlr.gov/reopt/curriculum/software-updates), [2024 news](https://www.nlr.gov/news/detail/program/2024/reopt-web-tool-offers-enhanced-energy-solutions)).

**Takeaways [I]**
- Ask for the goal first.
- Keep mandatory inputs to what only the user can know.
- Default everything else and document those defaults.
- Always compare against BAU.
- Give the resilience answer as a probability curve.
- Pair a PDF for humans with Excel for auditors.

### 1.2 HOMER Pro / HOMER Grid (UL Solutions)

**Flow**
- **[V]** The main flow is **Design → Calculate → Results** ([Navigating HOMER](https://homerenergy.com/products/pro/docs/latest/navigating_homer.html), [ESMAP training](https://www.esmap.org/sites/default/files/Presentations/HOMER%20Pro%20Foundations%20Presentation.pdf)).
  - **Design** has tabs for Load, Components, Resources and Project (economics, constraints, sensitivity).
  - **Calculate** simulates every configuration.
  - **Results** lists feasible systems sorted by NPC.
- **[V]** A modal **Setup Assistant** wizard walks users through the sections with Next/Back and ends on a **Summary review step** ([Using the Setup Assistant](https://www.homerenergy.com/products/pro/docs/3.15/using_the_setup_assistant.html)).
  - This means an expert tool with a wizard as an optional front door.
- **[V]** Two sizing modes are offered: an explicit **search space** (the user lists sizes) or the **HOMER Optimizer** (the user gives lower and upper bounds only).
  - HOMER advises doing a single-year analysis first and multi-year only afterwards, on a narrowed set ([Optimization](https://homerenergy.com/products/pro/docs/latest/optimization.html)).
  - This is a staged-fidelity pattern.

**Verdict**
- **[V]** **Summary Mode** is the verdict screen. It shows "an overview of the **winning system** (lowest NPC) and the economic metrics in comparison to the **base system**", where the base system is the lowest-capital-cost system and can be changed ([Summary Mode](https://homerenergy.com/products/pro/docs/latest/summary_mode.html), [Summary with base and winning case](https://www.homerenergy.com/products/pro/docs/3.15/summary_with_a_base_case_and_winning_case.html)).
  - Sensitivity drop-downs choose which "world" the winner is shown for.
- **[V]** The **Optimization Results** table has two views:
  - **Overall** ranks all systems by NPC, and is typically "dominated by two or three system types".
  - **Categorized** shows the cheapest system *of each architecture type*.
  - Source: [Optimization Results](https://homerenergy.com/products/pro/docs/latest/optimization_results.html).
  - **[I]** The Categorized view directly answers "what's the best system *with* hydrogen vs. *without*?"
- **[V]** **Compare Economics** gives IRR, simple payback, discounted payback and ROI, all *relative to a base case* ([Compare Economics](https://homerenergy.com/products/pro/docs/latest/compare_economics.html), [Calculating payback, IRR…](https://homerenergy.com/products/pro/docs/latest/calculating_payback_irr_and_other_economic_metrics.html)). The definitions are:
  - IRR: the discount rate at which the two NPCs are equal.
  - Simple payback: the year cumulative difference cash flow turns positive.
  - ROI: average yearly nominal cash-flow difference divided by the capital-cost difference.
- **[V]** The **Optimal System Type plot** shows which architecture wins across two sensitivity variables (x, y), coloured regions with diamonds at simulated points. HOMER also has surface, line and spider plots ([Optimal system type plot](https://www.homerenergy.com/products/pro/docs/3.11/optimal-system-type-plot.html), [Graphical mode](https://homerenergy.com/products/pro/docs/latest/graphical_view.html)).
  - **[I]** This is the single best existing visual for "under which conditions do I need a BESS / H2?"
- **[V]** The **break-even grid extension distance** is the distance at which grid-extension NPC equals stand-alone NPC. It is a threshold-style answer ([Break-even grid extension distance](https://homerenergy.com/products/pro/docs/latest/breakeven_grid_extension_distance.html)).

**Reports**
- **[V]** HOMER Pro 3.9 added a configurable report builder. Users pick sections from "a broad library of standard report sections… from cash flow to fuel and emissions summaries" and export to **PDF, HTML, DOCX, RTF** ([Generating Reports](https://homerenergy.com/products/pro/docs/latest/generate-reports-in-homer.html), [HOMER 3.9 news](https://microgridnews.com/homer-pro-3-9-powerful-reporting-furthers-homers-clean-power-everywhere-mission/)).
- **[V]** HOMER Pro also has a separate **Input Summary Report**, effectively an assumptions appendix ([Input Summary Report](https://homerenergy.com/products/pro/docs/latest/input_summary_report.html)).
- **[V]** HOMER Grid 1.7 added a **proposal generator**: "graphics-driven proposals that communicate… to both technical and non-technical audiences". It includes cost comparisons for "normal utility operations and during outages" ([Solar Power World](https://www.solarpowerworldonline.com/2020/02/homer-grid-solar-software-update-includes-proposal-generation/), [microgridnews](https://microgridnews.com/homer-tip-the-new-proposal-report-in-homer-grid/)).

### 1.3 XENDEE

- **[V]** XENDEE has two main products:
  - **DESIGN** covers 25+ technologies across 8 energy domains, including hydrogen, heating, cooling and DHW, with financial constraints. XENDEE says "90% of optimizations run in under two minutes" to enable scenario and sensitivity work ([xendee.com/design](https://xendee.com/design), [home](https://xendee.com/)).
  - **DISCOVER** is a portfolio-screening API that returns "site-specific CAPEX, OPEX savings, revenue potential, and carbon savings" in minutes ([DISCOVER](https://xendee.com/discover)).
- **[V]** A **Load Builder** lets users drag and scale typical load shapes when no data exists ([Load Builder help PDF](https://tools.xendee.com/Documents/LoadBuilderHelp.pdf)). XENDEE also ships default datasets for many regions and offers a UtilityAPI import ([FAQ](https://xendee.com/faq), [UtilityAPI news](https://xendee.com/insights/utilityapi-precise-energy-load-modeling)).
- **[V]** There is a public **"Microgrid Configurator" questionnaire** (questionnaire.xendee.com), a lead-in intake form ([link](https://questionnaire.xendee.com/)).
- **Not verified:** I could not confirm a specific "project wizard", report-generation UI or a "XENDEE for non-experts" product from primary sources. Treat those as unknown.
  - **[I]** XENDEE's design lessons for us are the *screen → design* two-tier split (DISCOVER, then DESIGN) and the sketch-a-load-profile input.

### 1.4 Energy Toolbase (ETB Developer)

- **[V]** ETB is aimed at developers and sales teams. It combines rate modelling, storage dispatch simulation (Acumen EMS) and **white-labelled proposal templates** ([ETB Developer](https://www.energytoolbase.com/solutions/etb-developer/)).
- **[V]** A **Proposal Summary page** compares the designs in a proposal side by side. It covers project cost and incentives, PV/ESS details, bill savings and transaction information ([ETB blog](https://www.energytoolbase.com/blog/project-development/new-etb-developer-proposal-features-proposal-summary-page-and-document-templates/)).
- **[V]** A **template gallery** includes a storage template that breaks savings into **energy vs demand savings** and shows 20-year bill cost and savings ([template gallery](https://www.energytoolbase.com/blog/project-development/proposal-document-template-gallery/)).
- **Takeaway [I]:** decompose the value into streams. For a BESS these are arbitrage, peak or demand reduction, ancillary services and resilience.

### 1.5 RETScreen Expert (Natural Resources Canada)

- **[V]** RETScreen has four top-level modules: **Benchmark → Feasibility → Performance → Portfolio** ([CTCN](https://www.ctc-n.org/resources/retscreen-clean-energy-management-software), [OpenEI](https://openei.org/wiki/RETScreen_Clean_Energy_Project_Analysis_Software)).
  - Performance compares actual against predicted, using NASA satellite weather data.
  - Portfolio aggregates many facilities into a dashboard.
- **[V]** The **Virtual Energy Analyzer** estimates production and savings "for any location… employing a **five-star benchmark ranking** system and without requiring a site visit" ([CTCN](https://www.ctc-n.org/resources/retscreen-clean-energy-management-software)).
  - **[I]** It is a pre-screen: "is this site worth studying?"
- **[V]** The feasibility module follows a **fixed 5-step standard analysis**: energy, cost, emissions, financial, then sensitivity and risk ([ResearchGate flow chart](https://www.researchgate.net/figure/RETScreen-model-flow-chart-showing-the-five-step-standard-analysis-and-design-parameters_fig1_325290620), [ScienceDirect method paper](https://www.sciencedirect.com/science/article/pii/S2215016118300761)).
  - The Financial Analysis model covers debt, pre- and after-tax cash flows, depreciation, tax, and **NPV, IRR, simple and equity payback**, with a **cumulative cash-flow graph**.
  - The Sensitivity & Risk model includes **Monte Carlo, an "impact graph" (tornado), median and confidence interval**.
- **[V]** The 2015 free "Viewer" mode vs paid "Professional" mode split means anyone can open and read a study, and only licensees can edit it ([CTCN](https://www.ctc-n.org/resources/retscreen-clean-energy-management-software)).
  - **[I]** This is a model for sharing studies with clients as read-only interactive reports.

### 1.6 Aurora Solar (sales proposals)

- **[V]** Aurora has two explicit modes:
  - **Sales Mode** has "guided workflows and simple swipe interactions", and reps "design live with the homeowner, **with guardrails** in place to make sure the design always stays accurate".
  - **Design Mode** gives full technical control.
  - Source: [Sales Mode](https://aurorasolar.com/sales-mode/), [Design Mode](https://aurorasolar.com/design-mode/).
- **[V]** Proposal **templates with placeholders** "automatically populate with relevant information about your design", including images and financing scenarios ([Aurora blog](https://aurorasolar.com/blog/how-to-optimize-your-solar-sales-with-proposal-templates/), [Help: Generate web proposal or PDF](https://help.aurorasolar.com/hc/en-us/articles/14883209518995-Generate-and-Send-a-Web-Proposal-or-PDF-in-Sales-Mode), [Sales Mode Variables](https://help.aurorasolar.com/hc/en-us/articles/23721994007699-Sales-Mode-Variables)).
  - Proposals are interactive web pages or PDFs, and they are trackable.
  - Financing options (cash, loan, lease, PPA) are shown side by side.
  - **[I]** These placeholders are exactly the "no hand-typed numbers" mechanism we want.

### 1.7 Tesla, Enphase and SolarEdge designers; Google Project Sunroof; EnergySage

- **[V]** Tesla's design tool starts from **address + average monthly bill** and then proposes a system ([tesla.com/energy/design](https://www.tesla.com/energy/design)).
- **[V]** The Enphase estimator lets users give consumption three ways: from bills, from home size, or as actual values. Users then pick **backup duration**, **partial vs whole-home backup**, and the appliances to include ([estimator.enphase.com](https://estimator.enphase.com/), [Soligent overview](https://www.soligent.net/event/enphase-storage-solar-estimator-tool-new-advanced-version/)).
  - **[I]** The tool asks about goals and consequences ("how long do you need backup?") rather than technical parameters (kWh).
- **[V]** **Project Sunroof** models the roof from imagery and uses NREL cost data. It "evaluates one or more possible installation sizes and **recommends the size that provides the most savings**", and compares **loan / lease / buy** ([Google methodology PDF](https://www.google.com/get/sunroof/assets/cost-savings-methodology.pdf?hl=ja), [EnergySage explainer](https://www.energysage.com/solar/google-project-sunroof-overview/)).
  - **[V-2]** Field comparisons say the solar-potential estimates are good but the prices and savings are weaker ([MREA](https://midwestrenew.org/comparing-sunroof/)).
  - **[I]** This is a reminder to label economic outputs as indicative.
- **[V]** EnergySage's calculator publishes its **assumptions list** openly: state-average rates from EIA, 2.8% electricity inflation, cash purchase, 25-year life ([EnergySage calculator](https://www.energysage.com/solar/calculator/)).
- SolarEdge Designer: not researched in depth.

### 1.8 PVsyst vs PVGIS (expert vs guided)

- **[V]** PVGIS (EU JRC) is a one-form estimator. Its **default system loss is 14%**, a single lumped number for cables, inverter, soiling and so on, and users are advised to "leave… at 14% unless you have a reason to change it" ([JRC PVGIS grid-connected](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-tools/grid-connected-pv_en), [pvgis.com](https://pvgis.com/en/pvgis-5-3)).
- **[V]** PVsyst decomposes the same losses into a detailed **loss diagram**, "always present in the Simulation report" ([PVsyst loss diagram](https://www.pvsyst.com/help/project-design/results/loss-diagram.html)). Its 3-D near-shading model is part of the same expert depth.
- **[I]** The pattern is a **lumped default that can be expanded into its components**. The novice sees "losses 14%"; the expert expands it into the waterfall.

### 1.9 NREL SAM (financial transparency benchmark)

- **[V]** SAM's financial metrics (LCOE, NPV, payback) follow Short, Packey and Holt (1995), *A Manual for the Economic Evaluation of Energy Efficiency and Renewable Energy Technologies*, NREL/TP-462-5173 ([SAM financial models](https://sam.nrel.gov/financial-models.html)).
- **[V]** **"Send to Excel with Equations"** exports a workbook that *replicates the financial model with live formulas* ([SAM forum](https://sam.nrel.gov/forum/forum-general/2045-financial-model-documentation.html)).
  - **[I]** This is the gold standard for "where did this number come from" when the audience is financial.

### 1.10 Cross-tool comparison

| Tool | Entry point | Defaults | Uncertainty shown as | Verdict form | Report |
|---|---|---|---|---|---|
| REopt | Goal choice (financial / resilience), then site, tariff, load | Documented default library; only site, tariff and load are mandatory | Outage-survival probability curve; parallel sensitivity runs | BAU vs optimal table, NPV at the bottom | PDF + Excel pro forma |
| HOMER Pro/Grid | Design tabs or Setup Assistant wizard | Component library; optimizer bounds | Sensitivity cases, optimal-system-type map, spider/surface | "Winning system" vs base case; categorized best-per-architecture | Section-picker report (PDF/DOCX/HTML); Grid proposal |
| XENDEE | DISCOVER screen → DESIGN | Regional default datasets, Load Builder | Fast re-runs for scenarios | CAPEX/OPEX savings, revenue, carbon | Export (details unverified) |
| ETB | Proposal of designs | Rate DB | Multiple designs compared | Savings by value stream | White-label templates |
| RETScreen | Benchmark → Feasibility (5 fixed steps) | Climate/product DBs; 5-star benchmark | Monte Carlo, impact (tornado), confidence interval | NPV/IRR/payback + cumulative cash-flow graph | Worksheets; free Viewer |
| Aurora | Sales Mode (guided) vs Design Mode | Auto-design | Financing scenarios | Savings over time, bill comparison | Placeholder templates, web + PDF |
| Sunroof / EnergySage / Tesla / Enphase | Address + bill (+ backup goal) | Heavy, published assumptions | Minimal ("estimate") | Recommended size + savings | Lead capture |
| PVGIS vs PVsyst | One form vs full project | 14% lumped loss | — / loss diagram | Yield | CSV/PDF vs full report |
| SAM | Technology + financial model pick | Defaults per model | Parametrics, stochastic | LCOE/NPV | "Send to Excel with Equations" |

---

## 2. General design patterns for guiding non-experts

### 2.1 Wizard vs hub-and-spoke vs progressive disclosure

- **[V]** NN/g defines a wizard as a "step-by-step process that allows users to input information in a prescribed order and in which subsequent steps may depend on information entered in previous ones".
  - Wizards suit **occasional** tasks.
  - Constraining freedom "can be liberating in cases where people don't… know enough to make a decision".
  - Source: [NN/g Wizards](https://www.nngroup.com/articles/wizards/).
- **[V]** In NN/g's framing, **progressive disclosure** means deferring "advanced or rarely used features to a secondary screen". It improves learnability, efficiency and error rate. **Staged disclosure** (a wizard) is the linear variant.
  - The key risks are **information scent**: users must know what they will get when they go deeper. The advanced layer must also stay *visibly* reachable.
  - Source: [NN/g Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/).
- **[V]** GOV.UK recommends **"one thing per page"**. It is easier for low-confidence users and better for "errors, branches, loops and saving progress" ([Question pages](https://design-system.service.gov.uk/patterns/question-pages/), [GDS blog](https://designnotes.blog.gov.uk/2015/07/03/one-thing-per-page/)).
  - GOV.UK also offers **"Complete multiple tasks"** (task list) for long services where tasks can be done in any order with status tags. Research found that users tried to click the status tag itself, so the whole row is now clickable ([task list pages](https://design-system.service.gov.uk/patterns/task-list-pages), [GDS 2023 iteration](https://designnotes.blog.gov.uk/2023/12/15/working-as-a-community-to-iterate-the-task-list-pattern/)).
  - There is also a **"Check answers"** summary pattern.
- **[V]** Material distinguishes linear from non-linear steppers ([MUI Stepper](https://mui.com/material-ui/react-stepper/), [M1 Steppers](https://m1.material.io/components/steppers.html)). Steppers are no longer documented in M3.
- **[I] Recommendation:** use a **hybrid**.
  - Start with a short **linear wizard** that frames the question (one thing per page).
  - Land on a **hub / task list** of study sections with status (Site, Demand, Technologies, Prices, Finance, Run, Results, Report).
  - Keep **progressive disclosure** inside each section. The "Advanced" layer reveals raw PyPSA attributes.
  - Aim for at most two layers visible to novices, plus an explicit "Expert view" route.
  - This is effectively HOMER's (Setup Assistant plus Design tabs) combined with GOV.UK's (task list plus check answers).

### 2.2 Defaults, assumptions and provenance

- **[V]** In NN/g's framing, most users stick with defaults, so the defaults must be good. Pre-fill with the most common value. The risk is that users accept defaults without review, so defaults should read as suggestions with brief explanations ([NN/g Power of Defaults](https://www.nngroup.com/articles/the-power-of-defaults/)).
- **[V]** The HM Treasury **Aqua Book** makes these points ([Aqua Book](https://analysisfunction.civilservice.gov.uk/policy-store/the-aqua-book-guidance-on-producing-quality-analysis-for-government/), [GOV.UK QA guidance](https://docs.data-community.publishing.service.gov.uk/analysis/best-practice/analytical-quality-assurance)):
  - QA should be proportionate to the risk of the intended use.
  - QA runs throughout the lifecycle.
  - Uncertainty must be documented and communicated to decision-makers.
  - **[I]** In practice this means an **assumptions log**, which the Aqua Book and UK practice treat as a core artefact.
- **[V]** Google PAIR: explain the data sources, and "point to third-party sources that users already trust" to jump-start trust ([PAIR Explainability + Trust](https://pair.withgoogle.com/chapter/explainability-trust/)).
- **[V]** AACE 18R-97 **estimate classes** tie accuracy to project-definition maturity ([AACE 18R-97 TOC](https://web.aacei.org/docs/default-source/toc/toc_18r-97.pdf)). The ranges below are indicative and vary by industry:
  - Class 5 (concept screening): about −50%/+100%.
  - Class 4 (feasibility): about −30%/+50%.
  - Class 3 (budget): about −20%/+30%.
  - **[I]** A study-maturity badge on every result ("Screening — Class 5-ish accuracy") is a cheap, credible way to show confidence to finance audiences.

### 2.3 Explainable results ("what drove this")

- **[V]** In Tableau Pulse, the insight engine detects **drivers, trends, contributors, outliers** and period-over-period change, then summarises them with an LLM ([Tableau Pulse insight types](https://help.tableau.com/current/online/en-us/pulse_insights_platform_insight_types.htm)).
  - **[I]** The detection is deterministic and the LLM only phrases it. That is the right split.
- **[V]** NN/g on dashboards: use preattentive attributes and length/2-D position for quantities. Analytical dashboards (decision-making) differ from operational ones ([NN/g Dashboards](https://www.nngroup.com/articles/dashboards-preattentive/)).
- **[V]** In **MGA with stakeholders (PyPSA)**, researchers pre-computed 56,050 near-optimal PyPSA configurations. Participants used an interactive interface to "select any feasible combination of system components and immediately see the implications" ([iScience 2026 / arXiv 2501.05280](https://arxiv.org/pdf/2501.05280)).
  - A **human-in-the-loop MGA** follow-up performs a "guided search" toward elicited preferences ([PLOS Climate](https://journals.plos.org/climate/article?id=10.1371%2Fjournal.pclm.0000560), [arXiv 2407.14353](https://arxiv.org/pdf/2407.14353)).
  - **[I]** This is directly reusable for "how much does it cost us to *not* build hydrogen?". Show the near-optimal space, not just the single optimum.
- **[V]** Bret Victor's "reactive documents" let "the reader… play with the author's assumptions and analyses, and see the consequences" ([Explorable Explanations](https://worrydream.com/ExplorableExplanations/)).

### 2.4 Uncertainty and sensitivity UIs

- **[V]** RETScreen's impact graph, HOMER's spider and optimal-system-type plots, and REopt's parallel sensitivity runs are covered in section 1.
- **[V]** In a tornado chart, each bar is a one-way sensitivity. Sort by swing with the largest at the top and keep a consistent scale ([Decision Frameworks](https://decisionframeworks.com/blog/how-to-build-and-interpret-tornado-diagrams-for-sensitivity-analysis), [TreeAge](https://www.treeage.com/tornado-diagram-sensitivity-analysis/)). The classic INFORMS paper compares spiderplots with tornado diagrams ([Eschenbach 1992](https://pubsonline.informs.org/doi/10.1287/inte.22.6.40)).
- **[V]** HCI evidence shows that **quantile dotplots and CDFs improve decisions** under uncertainty for lay users ([Fernandes et al., CHI 2018](https://dl.acm.org/doi/10.1145/3173574.3173718)). **Hypothetical outcome plots** help untrained viewers ([Padilla, Kay & Hullman review](http://space.ucmerced.edu/Downloads/publications/Uncertainty_Visualization_Padilla_Kay_Hullman_2022.pdf)).
  - **[I]** For "probability NPV > 0", a quantile dotplot ("in 17 of 20 futures, the BESS pays off") beats a mean ± SD.

### 2.5 Scenario comparison

- **[V]** SAP Fiori's comparison pattern and W&B's baseline-pinned comparison let users pin one object as the baseline and diff everything against it ([SAP Fiori comparison pattern](https://www.sap.com/design-system/fiori-design-web/v1-120/ui-elements/comparison-pattern), [W&B compare](https://docs.wandb.ai/weave/guides/tools/comparison)).
- **[V]** HOMER Categorized, ETB Proposal Summary and REopt BAU vs optimal all use the same comparison idea (section 1).

### 2.6 AI copilots that interpret results

| Product | What it does well [V] | Pitfall / limit [V] |
|---|---|---|
| **Tableau Pulse** | Deterministic insight detection (drivers, outliers) + LLM summary; digest across metrics ([help](https://help.tableau.com/current/online/en-us/pulse_insights_platform_insight_types.htm)) | Summary only covers the insight types it detects |
| **Power BI Copilot** narrative visual | Summarises report pages; footnotes link to visuals ([MS Learn](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-create-narrative)) | Only sees data *visible on the page*; row limits (30k per visual, 150k total); footnote links can break when moved; output format not guaranteed ([Chris Webb](https://blog.crossjoin.co.uk/2025/01/05/text-analysis-with-power-bi-copilot/), [Fabric community](https://community.fabric.microsoft.com/t5/Service/Smart-Narrative-copilot-preview-footnote-not-directing-to-the/td-p/4709815)) |
| **ThoughtSpot Spotter** | Maps questions to visible **search tokens** that business users can read back, plus inspectable SQL; "if tokens are correct, SQL is 100% accurate" ([ThoughtSpot](https://www.thoughtspot.com/blog/spotter-for-industries)) | Depends on a curated semantic model |
| **Hex Threads / Magic** | Each Thread is backed by a notebook that the data team can open to audit; answers prioritise endorsed semantic models ([Hex Threads](https://hex.tech/product/threads/)) | Needs a data team to endorse models |
| **Julius** | Shows generated Python and which tables and columns produced each result ([Julius](https://julius.ai/articles/13-powerful-features-that-make-julius-ai-the-top-data-analysis-tool)) | Vendor claims (V-2) |
| **PLEXOS Intelligence (Digital Analyst)** | Plain-language summaries and side-by-side comparisons of runs; "every response is based in your models, with clear traceability to structure, assumptions, and outputs"; diagnoses infeasibility and congestion ([Energy Exemplar](https://www.energyexemplar.com/plexos/intelligence), [launch](https://www.energyexemplar.com/product-news/plexos-intelligence-ai-built-in-to-accelerate-modeling-workflows)) | Vendor claims; details of the grounding mechanism are not public |

**Guidance and research on AI trust**
- **[V]** Amershi et al., "Guidelines for Human-AI Interaction" (CHI 2019), gives 18 guidelines, including "make clear what the system can do" and "how well" ([ACM](https://dl.acm.org/doi/10.1145/3290605.3300233), [PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf)).
- **[V]** Apple HIG for ML covers:
  - Don't show low-confidence results.
  - Translate confidence into familiar concepts.
  - Use attributions.
  - Explain limitations.
  - Never rely on corrections to make up for poor results.
  - Source: [Apple HIG ML](https://developers.apple.com/design/human-interface-guidelines/technologies/machine-learning/introduction).
- **[V]** IBM **Carbon for AI** requires an **AI label** on every AI-generated element. The label opens a layered **explainability popover** ([Carbon AI label](https://carbondesignsystem.com/components/ai-label/usage/)).
- **[V]** NN/g on hallucinations recommends confidence indicators, hedged language, source links and drill-down sources. Users read **inconsistency** as a hallucination signal ([NN/g AI hallucinations](https://www.nngroup.com/articles/ai-hallucinations/)).
- **[V]** Research on overreliance:
  - Users overestimate LLM accuracy when they are given default explanations ([arXiv 2605.10930](https://arxiv.org/pdf/2605.10930)).
  - **Structured and visual representations can *increase* overreliance.** Users trusted sensible-looking query visualisations even when results were wrong ([arXiv 2505.21512](https://arxiv.org/pdf/2505.21512)).
  - **[I]** An LLM that draws charts is riskier than one that only narrates model-computed charts.
- **[V]** In data-to-text generation, templates "provide strict guarantees against hallucinations" but sound robotic. Hybrids separate *what to say* (from the data) from *how to say it* ([arXiv 1910.08684](https://arxiv.org/pdf/1910.08684), [Symbolic references, arXiv 2311.09188](https://arxiv.org/pdf/2311.09188)).

---

## 3. Financial-modelling UX for non-financiers

- **[V] Causal:**
  - Models are built from named variables linked by "simple **plain-English** formulae".
  - Uncertain inputs are typed as ranges ("$1500 to $2500"), and Causal simulates thousands of scenarios to show the likely range.
  - Base/best/worst scenarios can be compared side by side.
  - Source: [Causal blog: Flaw of Averages](https://causal.app/blog/forecasting-with-uncertainty), [What is financial modelling](https://www.causal.app/blog/what-is-financial-modeling).
- **[V] Runway:**
  - Formulas are written in plain English, "every number traces back easily to its source", and the approach is driver-based.
  - Scenarios are "parallel versions where you test assumptions without touching your main forecast".
  - Source: [Runway modelling](https://runway.com/product/modeling), [What-if scenarios](https://runway.com/blog/what-if-scenarios-in-finance-and-how-to-use-them-right).
- **[V] "What would have to be true"** (Roger Martin, Strategic Choice Structuring) reverse-engineers the conditions under which an option wins ([Roger Martin](https://rogermartin.medium.com/the-strategic-choice-structuring-process-5e116b12ae1f)).
  - **[I]** Applied here, it becomes a **break-even or threshold** readout. Example: "Hydrogen pays off if electrolyser capex < €X/kW *and* H2 offtake price > €Y/kg". HOMER's break-even distance is the same idea.
- **[V] Standards:**
  - SAM/NREL uses Short/Packey/Holt definitions. SAM's residential and commercial payback is "years for cumulative after-tax cash flow to cover the initial equity investment" ([SAM residential/commercial](https://sam.nrel.gov/financial-models/residential-and-commercial)).
  - HOMER defines IRR, payback and ROI *relative to a base case* (section 1.2).
  - IRENA LCOE uses **real** terms, **excludes financial support**, and assumes a fixed real WACC. It was historically 7.5% in the OECD and China and 10% elsewhere, falling to 5% and 7.5% by 2020 ([IRENA 2018](https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2019/May/IRENA_Renewable-Power-Generations-Costs-in-2018.pdf), [IRENA 2020 summary](https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2021/Jun/IRENA_Power_Generation_Costs_2020_Summary.pdf), [2025 Annex methodology](https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2026/Jul/IRENA_TEC_RPGC_in_2025_Annex_2026.pdf)).
  - RETScreen's financial model includes debt, tax, depreciation, equity payback and a cumulative cash-flow graph (section 1.5).
- **Mosaic, Finmark:** not researched; no verified claims.
- **[I] Synthesis for non-financiers:**
  1. Lead with **one sentence in money and time**: "Pays back in ~7 years; adds €1.2 M value over 20 years at 8%."
  2. Show the **cumulative cash-flow chart** with the payback crossing marked. It is the most intuitive finance chart and is used by RETScreen, HOMER and Aurora.
  3. Put **plain-language labels** first and give the technical term as a subtitle, e.g. "Value today (NPV)", "Effective annual return (IRR)", "Years to break even (payback)".
  4. Show each input with **its unit, its default, its source and a plausible range**, and make the range feed sensitivity automatically (the Causal pattern).
  5. Present **real vs nominal, pre- vs post-tax, and with or without subsidy** as explicit, labelled toggles. IRENA and SAM conventions differ, and mixing them is the commonest novice error.
  6. Always frame results **relative to BAU**, as REopt and HOMER do.

---

## 4. Report drafting

- **[V] Structure.** Minto's Pyramid Principle has three layers ([ModelThinkers SCQA](https://modelthinkers.com/mental-model/minto-pyramid-scqa), [think-cell](https://www.think-cell.com/en/blog/using-the-pyramid-principle-to-build-better-powerpoint-presentations)):
  - answer first;
  - SCQA (situation, complication, question, answer) for the exec summary;
  - grouped supporting arguments, with evidence at the base.
- **[I]** Recommended skeleton:
  1. Executive summary and recommendation (verdict, 3 KPIs, 1 chart)
  2. The question and the options considered (including BAU)
  3. Recommended system and how it operates
  4. Economics (cash flow, NPV, IRR, payback, LCOE/LCOS)
  5. What drives the result (tornado, value-stream waterfall)
  6. Robustness (scenarios, break-evens, probability of positive NPV)
  7. Assumptions (auto-generated ledger with sources)
  8. Limitations and next steps (study-maturity class, what to firm up)
  9. Appendix: model description, input summary, full tables, solver log, version hash
- **[V] Traceable numbers.** Existing tools do this in several ways:
  - Aurora placeholders auto-populate from the design.
  - HOMER builds reports from a section library and has an input summary report.
  - REopt produces a PDF plus an Excel pro forma.
  - SAM's "Send to Excel with Equations" replicates the model with formulas.
  - Quarto parameterised reports render a single source (notebook plus parameters) to **HTML, PDF, DOCX and PPTX**, with inline computed values ([Quarto blog](https://quarto.org/docs/blog/posts/2025-07-24-parameterized-reports-python/index.html), [Posit](https://posit.co/blog/parameterized-quarto)).
- **[V] Export formats seen:**
  - HOMER: PDF, HTML, DOCX, RTF.
  - REopt: PDF + XLSX.
  - Aurora: web + PDF.
  - Quarto: DOCX, PPTX, PDF, HTML.
- **[I] Templated vs LLM-drafted narrative.** Use a **hybrid** in which each layer has a different job:
  - **Deterministic "facts" layer:** every number, comparison ("higher than"), rank ("largest driver") and verdict class is computed by code from the solved model.
  - **Template layer:** section headings, standard caveats and assumption tables.
  - **LLM layer:** only phrasing and connective prose, working over the fact bundle. Every numeric token in LLM text must be a **reference** (e.g. `{{npv.value}}`) that is resolved and **validated** after generation. Unresolved or free-typed numbers are rejected.
  - Mark AI-written paragraphs with an AI label (Carbon), keep them editable, and preserve human edits across re-runs, as Aurora and ETB templates do.

---

## 5. Synthesis

### 5(a) Pattern catalogue (22 patterns)

1. **Question-first entry ("What are you trying to decide?")**
   - What it is: the study starts from a decision question rather than a blank model. Each question pre-selects technologies, value streams, KPIs and a report template.
   - Source: REopt's Financial vs Resilience choice; Enphase's "how long do you need backup?".
   - Application: offer cards for "Do I need a BESS?", "Do I need hydrogen?", "Recover data-centre waste heat?" and "Value a specific project". Each card maps to a PyPSA template network with the right extendable components (`StorageUnit`/`Store`+`Link`, electrolyser `Link` + H2 `Store`, heat-pump `Link` + heat bus) and the right BAU counterfactual.

2. **Minimum mandatory inputs + documented defaults**
   - What it is: only ask for what only the user can know. Everything else gets a default, a source and a range.
   - Source: REopt (site, tariff, load mandatory); PVGIS 14% default; EnergySage published assumptions; NN/g Power of Defaults.
   - Application: for a BESS study the mandatory inputs are site/bidding zone, load or consumption, tariff/market and grid-connection limit. Capex, efficiency, degradation, WACC and lifetime come from a versioned assumptions library (e.g. PyPSA technology-data), each shown with its source year.

3. **One thing per page intake wizard**
   - What it is: a short linear wizard (5–8 screens) that frames the study, with a final "check your answers" summary.
   - Source: GOV.UK question pages and check answers; HOMER Setup Assistant summary step; NN/g Wizards.
   - Application: ask for location, question, data availability, goal, horizon and the capex/budget envelope. The last screen summarises everything, with change links.

4. **Hub / task list with status**
   - What it is: after intake, a study overview lists the sections with status (Not started, Using defaults, Customised, Needs attention). Sections can be done in any order.
   - Source: GOV.UK Complete multiple tasks; HOMER Design tabs.
   - Application: sections are Site & grid, Demand, Candidate technologies, Prices & tariffs, Finance, Run, Results, Report. "Using defaults" is a status in its own right and signals where the user has not engaged.

5. **Two-layer progressive disclosure + explicit Expert view**
   - What it is: a plain-language layer by default and an "Advanced" expander with technical parameters. A separate Expert view exposes the raw model.
   - Source: NN/g progressive disclosure; Aurora Sales vs Design Mode; PVGIS vs PVsyst.
   - Application: the novice sees "Solar availability profile" and the expander shows `p_max_pu`. The Expert view is the existing canvas and parameter tables, and it stays in sync.

6. **Plain-language labels with the technical term as a subtitle**
   - What it is: every field has a human label, a unit, a one-line help text and the canonical name in small type.
   - Source: Runway and Causal plain-English formulas; NN/g information scent.
   - Application: "Battery hours of storage (h) — `max_hours`"; "Minimum stable output (% of capacity) — `p_min_pu`". This is a mapping table maintained once.

7. **Assumptions ledger with provenance**
   - What it is: a single auto-generated table of every input that affects the result. Columns are value, unit, source (default library / user / imported file), who changed it and when, a plausible range, and a sensitivity flag.
   - Source: Aqua Book assumptions log; HOMER Input Summary Report; EnergySage assumptions list; PAIR "explain data sources".
   - Application: it doubles as the report's assumptions chapter and as the input to sensitivity. Rows edited by the user are visually distinguished from defaults.

8. **"Where did this number come from?" drill-through**
   - What it is: any KPI can be clicked to show its formula, its component values and links back to model objects and inputs.
   - Source: Runway ("every number traces back"); SAM "Send to Excel with Equations"; ThoughtSpot tokens; Julius showing source columns.
   - Application: NPV expands into discounted annual cash flows, which break into capex, opex, market revenues and avoided grid costs. Each of those links to the PyPSA component and the time-series results. An "Export model to Excel with formulas" option serves finance reviewers.

9. **BAU-relative verdict card**
   - What it is: the headline is always a difference against a named counterfactual, with a verdict class, three KPIs and a one-sentence reason.
   - Source: REopt BAU vs optimal; HOMER winning vs base case; Minto answer-first.
   - Application: "Yes — a 20 MW / 40 MWh battery is worth building. It adds €4.1 M of value (NPV at 7%), pays back in 6.5 years, and mostly earns from peak shaving (62%)." The verdict classes are Recommended, Marginal (depends on assumptions), or Not recommended.

10. **Categorized best-per-option comparison**
    - What it is: show the best system *within each architecture*, not just the global optimum, so users see the cost of each choice.
    - Source: HOMER Categorized view; ETB Proposal Summary.
    - Application: rows are "No new assets (BAU)", "Best with BESS", "Best with H2", "Best with BESS+H2" and "Best with waste-heat recovery". Solve once per architecture by fixing extendability, then show the delta NPV of each against BAU.

11. **Value-stream waterfall**
    - What it is: break the total benefit into named streams so users see *why* the project pays.
    - Source: ETB energy vs demand savings; REopt cost-line comparison.
    - Application: for a BESS the streams are arbitrage, peak/capacity charge reduction, curtailment avoided, ancillary services and resilience. For waste heat they are heat sales, avoided gas boiler fuel, avoided cooling electricity and CO2 cost. The streams are computed from PyPSA dual prices and flows.

12. **Cumulative cash-flow chart with the payback marker**
    - What it is: the single most intuitive finance chart, shown with the break-even point annotated.
    - Source: RETScreen cumulative cash-flow graph; HOMER simple payback definition.
    - Application: annotate the discounted and undiscounted payback years, plus the replacement capex years (e.g. battery replacement in year 12).

13. **Tornado and driver ranking**
    - What it is: a one-way sensitivity on the ledger's flagged inputs, sorted by NPV swing, with plain labels.
    - Source: RETScreen impact graph; decision-analysis practice.
    - Application: generate the tornado automatically from the ledger ranges (Causal-style ranges). Label the top three drivers in the verdict card: "Most sensitive to battery capex and peak tariff."

14. **Break-even / "what would have to be true" readout**
    - What it is: express robustness as thresholds rather than point estimates.
    - Source: HOMER break-even grid extension distance; Roger Martin WWHTBT.
    - Application: "Hydrogen becomes worthwhile if electrolyser capex falls below €620/kW or the H2 price exceeds €5.8/kg." Compute this by bisection over re-solves or by parametric sweep.

15. **Optimal-option map across two uncertainties**
    - What it is: a 2-D map coloured by which option wins, with the base case marked.
    - Source: HOMER Optimal System Type plot.
    - Application: gas price on the x axis and BESS capex on the y axis, with regions labelled "BAU", "BESS" and "BESS+H2". The map tells the client where they are and how far from the boundary.

16. **Probability of success as frequency (quantile dotplot)**
    - What it is: show uncertain outcomes as countable dots ("17 of 20 futures") rather than error bars.
    - Source: Fernandes et al. CHI 2018; Padilla/Kay/Hullman; RETScreen Monte Carlo; REopt outage survival probability.
    - Application: sample weather years or price scenarios, re-solve (or re-dispatch with fixed capacities), and show the NPV dotplot with a line at 0. Show resilience as a REopt-style survival curve.

17. **Near-optimal alternatives explorer (MGA)**
    - What it is: show that several designs are almost as cheap, and let users trade cost for other preferences.
    - Source: PyPSA MGA stakeholder interface (iScience 2026); human-in-the-loop MGA (PLOS Climate).
    - Application: "Within 3% of the cheapest cost you could build 0–15 MW of electrolysis." Sliders move along the near-optimal space. This is valuable for clients with strategic or ESG preferences.

18. **Pinned-baseline scenario comparison**
    - What it is: scenarios are parallel copies, and one is pinned as the baseline. Every other scenario shows deltas and highlights changed inputs.
    - Source: Runway parallel scenarios; SAP Fiori and W&B baseline comparison; REopt multi-evaluation export.
    - Application: comparison columns show KPIs as deltas, and an "inputs that differ" diff list shows exactly what changed. This also prevents apples-to-oranges comparisons.

19. **Study maturity / confidence badge**
    - What it is: every result carries a maturity class and an accuracy band derived from data quality, e.g. default vs measured load, generic vs quoted capex.
    - Source: AACE 18R-97 estimate classes; Apple HIG "translate confidence into familiar concepts"; Sunroof field-accuracy caveats.
    - Application: "Screening study (typical accuracy −30%/+50%). 4 key inputs are defaults. Upload metered load to improve." The badge links to the ledger items that would tighten it.

20. **Staged fidelity: screen → design → detail**
    - What it is: a fast coarse run first, then deeper runs on a narrowed set.
    - Source: HOMER single-year then multi-year; XENDEE DISCOVER → DESIGN; RETScreen benchmark → feasibility; REopt portfolio screening.
    - Application: the screening run uses a representative-days / time-clustered PyPSA model (seconds to minutes). The design run uses full 8,760 h and multiple weather years only for the shortlisted options, and the UI says which fidelity produced each number.

21. **Grounded AI interpreter (facts bundle + citations + AI label)**
    - What it is: the copilot never computes or recalls numbers. It receives a structured facts bundle (KPIs, drivers, sensitivities, ledger) from the model and must cite a fact ID for every figure. The UI renders the figures from the IDs and marks the text as AI-generated, with an explainability popover.
    - Source: PLEXOS Intelligence traceability; ThoughtSpot tokens; Power BI Copilot footnotes; Tableau Pulse (deterministic detection plus LLM phrasing); Carbon AI label; NN/g hallucinations; Amershi G1/G2.
    - Application: the copilot explains "why no hydrogen?" using the categorized comparison and break-evens. It proposes (but does not silently apply) input changes, and answers "what does p_max_pu mean?" in context.

22. **Live, template-driven report builder (no hand-typed numbers)**
    - What it is: the report is a document bound to model outputs through placeholders. Sections come from a library; the narrative is template text plus optional LLM prose with validated references. It re-renders when the model re-runs and exports to DOCX/PPTX/PDF.
    - Source: HOMER report section library; Aurora placeholders; ETB templates; REopt PDF + Excel; Quarto parameterised reports; Minto structure.
    - Application: the default template follows the section-4 skeleton, and the charts are the same objects as in the Results screens. A "stale" flag appears if inputs change after the report was drafted. An Excel pro forma and the input summary are appendices.

23. **Read-only shareable study viewer**
    - What it is: clients can open an interactive but locked version of the study and change only the sliders the consultant exposes.
    - Source: RETScreen Viewer mode; Aurora web proposals (trackable); Bret Victor reactive documents.
    - Application: the consultant publishes a study link with 3–5 client-adjustable assumptions (e.g. electricity price outlook, discount rate). Other inputs stay locked, so clients explore without breaking the study.

24. **Guardrails and sanity checks inline**
    - What it is: flag implausible inputs and outputs in context before and after the run, without blocking experts.
    - Source: Aurora "guardrails to make sure the design always stays accurate"; GOV.UK error handling; PLEXOS infeasibility diagnosis.
    - Application: "Battery round-trip efficiency 98% is above typical (85–92%)"; "Load profile peak exceeds grid connection"; "Solver returned infeasible — likely cause: heat demand exceeds max heat-pump size". Infeasibility and unit issues are explained in plain language, with a link to the offending object.

### 5(b) Recommended end-to-end guided-study flow

The design principle **[I]** is a linear wizard to *frame*, a hub to *build*, answer-first results to *decide*, and a bound report to *communicate*. The Expert view is available at every step and edits the same underlying PyPSA network.

| # | Screen | What it shows | Novice needs | Expert unlocks |
|---|---|---|---|---|
| 0 | **Start** | Question cards (BESS / H2 / waste heat / value my project / custom) plus recent studies | Plain-language question, an example output ("you'll get a yes/no, the size, the value and a report") | "Open blank model" goes straight to the canvas |
| 1 | **Intake wizard** (one thing per page, 5–8 steps) | Location/zone → what you have today (grid connection, existing assets) → consumption (upload, typical profile by sector, or sketch) → goal (minimise cost / maximise value / resilience hours / CO2) → horizon and financial perspective (owner, discount rate preset) → check answers | Presets ("typical data-centre 10 MW IT load, PUE 1.3"); choose-by-consequence questions (backup hours, not kWh) | Direct entry of snapshots, weights and carrier setup |
| 2 | **Study hub** (task list) | Sections with statuses (Using defaults / Customised / Needs attention) and a study-maturity badge | Which sections matter most for this question (ranked) | Jump to any section's advanced layer or the canvas |
| 3 | **Candidate technologies** | Cards per candidate (e.g. BESS, electrolyser + H2 storage, heat pump + DH connection) with default costs and ranges, "include / exclude / force size" | Friendly cost presets (low / central / high) with source year | Raw component attributes (`capital_cost`, `efficiency`, `p_nom_max`, `max_hours`, `standing_loss`, ...), custom components, links |
| 4 | **Prices & finance** | Tariff/market price selection, WACC, lifetime, inflation, tax/subsidy toggles (real/nominal, with/without support) | Explanations of each toggle; defaults from a named convention (e.g. "IRENA-style real WACC") | Full cash-flow settings, debt/equity split, depreciation, replacement schedules |
| 5 | **Assumptions review** | The ledger, filtered to key drivers; defaults vs user-edited; ranges | "These 6 assumptions matter most; please check them" | All rows, bulk edit, import/export CSV, pin versions of the assumptions library |
| 6 | **Run** | Staged fidelity choice: Quick screen (clustered) vs Full study (8,760 h, multi-year); progress; plain-language solver status | ETA, what's being computed, friendly failure explanations | Solver selection and options, clustering settings, logs, MGA settings |
| 7 | **Verdict** (answer first) | Verdict card (Recommended / Marginal / Not recommended), recommended sizes, 3 KPIs vs BAU, top 3 drivers, maturity badge, key caveat | One sentence and one chart; "why?" link | Full KPI set, objective breakdown, duals/shadow prices |
| 8 | **Why & how** | Value-stream waterfall, cumulative cash-flow chart, typical-week dispatch (how the asset runs), categorized option comparison (BAU / BESS / H2 / both) | Annotated charts with captions written for the reader | All time series, per-component results, network map results |
| 9 | **How robust?** | Tornado, break-even thresholds, optimal-option map, probability dotplot, near-optimal alternatives | "What would have to be true" statements; "in 17 of 20 futures…" | Custom sweeps, Monte Carlo settings, MGA slack, raw sensitivity tables |
| 10 | **Scenarios** | Pinned baseline + scenario deltas + input diffs | "Try a high-price future" presets | Arbitrary scenario trees, batch runs |
| 11 | **Ask the copilot** (docked on every screen) | Grounded explanations with fact citations and an AI label | "Why no hydrogen?", "What is IRR?" | "Show me the constraint that binds", "Generate a sweep over X" (proposal needing confirmation) |
| 12 | **Report** | Template-driven draft (section-4 skeleton), live-bound numbers and charts, AI-drafted prose marked for review, stale-flag, export DOCX/PPTX/PDF + Excel pro forma + assumptions appendix | Good default template; tone presets (client / board / technical) | Template editing, custom sections, methodology appendix, model file + version hash |
| 13 | **Share** | Read-only interactive study link with selected client-adjustable sliders | — | Choose exposed assumptions, access control |

### 5(c) Anti-patterns to avoid

1. **Raw model vocabulary as the primary UI.** `p_max_pu`, `marginal_cost` and `e_cyclic` as first-level labels fail the NN/g information-scent test. Keep them as subtitles, not headings.
2. **A mandatory 30-step wizard that hides the model.** Wizards are for occasional, dependent-input tasks (NN/g). Experts need the exit, and everyone needs the hub to revisit sections. Never trap state inside a modal flow.
3. **More than two disclosure layers, or advanced settings that are hard to find.** NN/g warns that the secondary layer must stay visibly reachable.
4. **Silent defaults.** Defaults with no source, no year and no "you haven't checked this" status give a false sense of precision, and users over-accept defaults (NN/g).
5. **Absolute results without a counterfactual.** "System cost €12.3 M" means nothing to a client. Always show the delta against BAU (REopt, HOMER).
6. **Single-point verdicts presented as certain.** No maturity class, no sensitivity and no break-even leads to the Sunroof problem: good physics, overconfident economics.
7. **Mixing financial conventions.** Real vs nominal, pre- vs post-tax and with or without subsidy mixed silently across screens (compare IRENA and SAM conventions).
8. **LLM-generated numbers or charts.** Letting the copilot compute, remember or re-type figures is a problem because visual and structured output *raises* overreliance (arXiv 2505.21512). Numbers must come from model-computed fact IDs.
9. **Copilot scope that users cannot see.** Power BI Copilot only sees what is on the page, which leads to confident but partial answers. Show what context the copilot has, and say "I don't have that result" instead of guessing (Apple HIG: don't show low-confidence results).
10. **Unmarked AI prose in client reports.** Every AI paragraph should carry a label and a review state (Carbon AI label). Nothing should be exported without a human "reviewed" tick on AI sections. **[I]**
11. **Hand-typed or copy-pasted figures in reports.** These go stale the moment the model re-runs. Bind every figure and flag stale reports.
12. **Dashboards that show everything.** Twenty equal-weight charts and no answer-first hierarchy (NN/g dashboards; Minto). Put the verdict, drivers and caveats first, and the details on demand.
13. **Only the global optimum.** Hiding the near-optimal alternatives and the per-architecture bests (HOMER Categorized, MGA) removes the client's ability to weigh non-cost preferences, and invites "the model says" authority.
14. **Infeasible or failed runs reported as solver jargon.** Translate infeasibility into a likely cause and a fix (PLEXOS Intelligence does diagnostics). **[V]** for PLEXOS; the rest is **[I]**.
15. **Error bars and standard deviations for lay audiences.** Use frequency framing (dotplots), survival curves or thresholds instead (CHI 2018).
16. **Scenario sprawl without a baseline.** Many copies with no pinned reference and no input diff lead to unexplainable differences.

---

## 6. Open questions and suggested follow-ups

- Verify these by opening the primary pages once network access allows:
  - the REopt user manual (full default table, results-page layout, current PDF contents);
  - HOMER Grid's proposal sections;
  - XENDEE's in-app workflow and reports;
  - Tesla's and SolarEdge's designer flows;
  - Mosaic and Finmark.
- Run a small usability test of the verdict card and tornado wording with 5 consultants and 5 clients. NN/g-style qualitative testing would be enough.
- Decide the counterfactual definition per question template (e.g. for waste heat, whether BAU is "gas boiler DH" or "existing heat pump DH"). It drives every KPI.

## Sources (primary unless noted)

- REopt: https://reopt.nrel.gov/user-guides.html · https://reopt.nlr.gov/tool/reopt-user-manual.pdf · https://www.osti.gov/biblio/1770888 · https://docs.nlr.gov/docs/fy20osti/76677.pdf · https://docs.nlr.gov/docs/fy20osti/76678.pdf · https://docs.nlr.gov/docs/fy20osti/76676.pdf · https://arxiv.org/pdf/2008.05873 · https://www.nlr.gov/reopt/curriculum/software-updates · https://www.nlr.gov/news/detail/program/2026/reopt-expands-options-to-help-energy-managers-cut-costs
- HOMER: https://homerenergy.com/products/pro/docs/latest/summary_mode.html · https://homerenergy.com/products/pro/docs/latest/optimization_results.html · https://homerenergy.com/products/pro/docs/latest/compare_economics.html · https://homerenergy.com/products/pro/docs/latest/calculating_payback_irr_and_other_economic_metrics.html · https://www.homerenergy.com/products/pro/docs/3.11/optimal-system-type-plot.html · https://homerenergy.com/products/pro/docs/latest/breakeven_grid_extension_distance.html · https://homerenergy.com/products/pro/docs/latest/generate-reports-in-homer.html · https://homerenergy.com/products/pro/docs/latest/input_summary_report.html · https://www.homerenergy.com/products/pro/docs/3.15/using_the_setup_assistant.html · https://homerenergy.com/products/pro/docs/latest/optimization.html · https://www.solarpowerworldonline.com/2020/02/homer-grid-solar-software-update-includes-proposal-generation/ (secondary)
- XENDEE: https://xendee.com/design · https://xendee.com/discover · https://tools.xendee.com/Documents/LoadBuilderHelp.pdf · https://xendee.com/faq
- Energy Toolbase: https://www.energytoolbase.com/solutions/etb-developer/ · https://www.energytoolbase.com/blog/project-development/new-etb-developer-proposal-features-proposal-summary-page-and-document-templates/ · https://www.energytoolbase.com/blog/project-development/proposal-document-template-gallery/
- RETScreen: https://www.ctc-n.org/resources/retscreen-clean-energy-management-software · https://openei.org/wiki/RETScreen_Clean_Energy_Project_Analysis_Software · https://www.sciencedirect.com/science/article/pii/S2215016118300761
- Aurora: https://aurorasolar.com/sales-mode/ · https://aurorasolar.com/design-mode/ · https://help.aurorasolar.com/hc/en-us/articles/14883209518995 · https://aurorasolar.com/blog/how-to-optimize-your-solar-sales-with-proposal-templates/
- Consumer: https://www.google.com/get/sunroof/assets/cost-savings-methodology.pdf · https://www.energysage.com/solar/calculator/ · https://estimator.enphase.com/ · https://www.tesla.com/energy/design · https://midwestrenew.org/comparing-sunroof/ (secondary)
- PV: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-tools/grid-connected-pv_en · https://www.pvsyst.com/help/project-design/results/loss-diagram.html
- SAM: https://sam.nrel.gov/financial-models.html · https://sam.nrel.gov/financial-models/residential-and-commercial
- IRENA: https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2019/May/IRENA_Renewable-Power-Generations-Costs-in-2018.pdf · https://www.irena.org/-/media/Files/IRENA/Agency/Publication/2026/Jul/IRENA_TEC_RPGC_in_2025_Annex_2026.pdf
- UX guidance: https://www.nngroup.com/articles/wizards/ · https://www.nngroup.com/articles/progressive-disclosure/ · https://www.nngroup.com/articles/the-power-of-defaults/ · https://www.nngroup.com/articles/dashboards-preattentive/ · https://www.nngroup.com/articles/ai-hallucinations/ · https://design-system.service.gov.uk/patterns/question-pages/ · https://design-system.service.gov.uk/patterns/task-list-pages · https://designnotes.blog.gov.uk/2015/07/03/one-thing-per-page/ · https://m1.material.io/components/steppers.html · https://carbondesignsystem.com/components/ai-label/usage/ · https://developers.apple.com/design/human-interface-guidelines/technologies/machine-learning/introduction · https://pair.withgoogle.com/chapter/explainability-trust/ · https://dl.acm.org/doi/10.1145/3290605.3300233
- Analytics copilots: https://help.tableau.com/current/online/en-us/pulse_insights_platform_insight_types.htm · https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-create-narrative · https://blog.crossjoin.co.uk/2025/01/05/text-analysis-with-power-bi-copilot/ (secondary) · https://www.thoughtspot.com/blog/spotter-for-industries · https://hex.tech/product/threads/ · https://www.energyexemplar.com/plexos/intelligence · https://julius.ai/articles/13-powerful-features-that-make-julius-ai-the-top-data-analysis-tool (vendor)
- HCI / research: https://dl.acm.org/doi/10.1145/3173574.3173718 · http://space.ucmerced.edu/Downloads/publications/Uncertainty_Visualization_Padilla_Kay_Hullman_2022.pdf · https://arxiv.org/pdf/2501.05280 · https://journals.plos.org/climate/article?id=10.1371%2Fjournal.pclm.0000560 · https://arxiv.org/pdf/2505.21512 · https://arxiv.org/pdf/2605.10930 · https://arxiv.org/pdf/1910.08684 · https://worrydream.com/ExplorableExplanations/ · https://pubsonline.informs.org/doi/10.1287/inte.22.6.40
- Finance / structure: https://causal.app/blog/forecasting-with-uncertainty · https://runway.com/product/modeling · https://rogermartin.medium.com/the-strategic-choice-structuring-process-5e116b12ae1f · https://modelthinkers.com/mental-model/minto-pyramid-scqa · https://web.aacei.org/docs/default-source/toc/toc_18r-97.pdf · https://analysisfunction.civilservice.gov.uk/policy-store/the-aqua-book-guidance-on-producing-quality-analysis-for-government/
- Reporting: https://quarto.org/docs/blog/posts/2025-07-24-parameterized-reports-python/index.html · https://posit.co/blog/parameterized-quarto
