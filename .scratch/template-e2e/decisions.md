<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Template workflows: the decision inventory

Step 1 of the template end-to-end package (owner decisions E2E-TEMPLATES-1, DECISION-GAPS-1, EXPERT-GUIDED-1, RAW-NAMES-1, HOLISTIC-1, HOLISTIC-REC-1).
It lists, per template and per flow section, every decision a user must or may make. For each, it records where Guided and Expert let the user make it, whether the running app really lets them, and whether a template default is shown as "assumed". Gaps are issue files under [issues/](issues/). Step 2's browser journeys make each decision through the UI. A journey that meets a gap is marked expected-fail with the issue id.

**Journey fit.** This inventory covers the set-up half of the shared journey: pick template → set up site and assets (sections 1–8) → check readiness (9) → run. It also covers the hand-off into results (10): the verdict must reflect the decisions. Waiting, reading results, comparing and reporting belong to the UX-NEXT-1 packages. The journeys here stop at "the verdict reflects the decisions".

## How this was checked

- **App:** master `882d7114`, API :8000, Vite :5173, signed in as the dev admin, no assistant key (the assistant stays off).
- **Probe:** a Playwright probe (not committed; step 2 turns it into journeys) did the following for each template. Created the project through New project → From template, submitting the template's own form where it has one. Walked all ten Guided sections from the rail ("Project flow sections"). Opened each section's first **Edit ›** / **Open here ›** drawers. Switched to Expert, opened every entry of each flow-section block, and read the property panel of a load, the candidates and the import link.
- **Code:** each decision was traced to its source with file:line maps of the template catalogue (`routers/projects.py:1665-1946`, `project_templates/eh_*.py`), the flow endpoint (`services/flow_service.py:1214-1335`, `flow_sections.py:22-37`) and the run inputs (`services/adequacy/eh_study_runner.py`, `reliability_goal.py`, `run_gates.py`, `flow_scope.py`).
- **Other templates:** staged DC, both H2 hubs, staged microgrid, ammonia, IPP and eh_site (hospital) were walked afterwards; see [app-walk-2026-11-17.md](app-walk-2026-11-17.md). Their findings confirm 01–08. New items there (IPP "sized by the study" but fixed min = max, a 0 €/kW cost default, state mismatches between rail and card, `subsea_tie` listed for outage data but not as an asset) are for the next triage.
- **Status:** "✓" = the decision was made or seen editable in the running app; "code" = taken from code only, not yet seen in the app.

## 1. The real template list

The backend has no template list route. The id → name registry is `routers/projects.py:1665-1688`, the builders `project_templates/eh_templates.py:965-970` plus `eh_staged_hubs.py`, `eh_ammonia.py`, `eh_concentrator.py`, `eh_ipp.py`, `eh_site.py`. The wizard's list is `frontend/src/layout/NewProjectWizard.tsx:68-90`.

| Id | Shown as | In design §2.3 | Creation form | Grid |
|---|---|---|---|---|
| `eh_datacenter` | Data Center Energy Hub | DC | IT load, PUE at design, Backup mode, Power architecture | weak, 40 MW |
| `eh_datacenter_staged` | … (staged growth) | DC | PUE at design, Backup mode, Power architecture (IT phases fixed 12/24/36 MW) | weak, 55 MW energized in steps |
| `eh_site` | "Your own site" card (wizard and hub-design Start card) | generic site | Site type (industrial, commercial, hospital, campus, other); growth stages Year + Peak load (MW); More: Load shape, Must stay on (%), Connection limit (MW), fault level (MVA), existing gensets (count, unit MW, fuel), Solar PV (MW), Battery (MW/MWh), candidates (Gensets, Battery, Solar PV), ride-through | weak |
| `eh_microgrid` | Island Microgrid | microgrid | none | off-grid |
| `eh_microgrid_staged` | … (staged growth) | microgrid | none | off-grid |
| `eh_h2_hub` | Industrial Hydrogen Hub | H2 hub | none | strong |
| `eh_h2_hub_staged` | … (staged growth) | H2 hub | none | strong, 200 MW |
| `eh_ammonia_staged` | Green Ammonia Hub (staged growth) | ammonia | none | strong, 200 MW |
| `eh_concentrator_staged` | Mining Concentrator (staged growth) | **not in §2.3** | none | weak, 25 → 35 MW, non-firm 2027 |
| `eh_ipp_hybrid` | Renewables IPP with battery | **not in §2.3** (generation-only: no loads) | none | strong, 70 MW |

`3bus`, `ieee14`, `belgium` and `ieee39` are network examples with no workflow pre-fill and are out of scope. The concentrator and IPP templates are in the catalogue but not in §2.3. This inventory covers them; their columns follow the nearest §2.3 template (concentrator ≈ DC staged with process carriers, IPP ≈ generic site without loads). See Q4.

## 2. Findings that hold for every template

1. **Location (R everywhere) is never asked, and Run does not wait for it.** Section 1 offers it (Open here › → Latitude/Longitude drawer ✓). No creation form asks for it, and Readiness says "Ready to run" with a note. → [01](issues/01-location-required-not-asked.md), Q1.
2. **Section 2 has no Guided editing.** Every Edit jumps to Expert's Carriers table, and "Add a process unit" is Expert-only. → [03](issues/03-carriers-processes-jump.md).
3. **The connection limit is read-only in Guided** for template projects. The ramp limit and oscillation band are editable in both modes ✓. "Import efficiency (assumed)" opens an empty new-Link form. → [02](issues/02-connection-limit-in-guided.md).
4. **The load editor opens on the generator's defaults** ("industrial", empty Peak MW), not on the load, and gives non-electric loads MW. The DC-specific load decisions are creation-only. → [04](issues/04-load-editor-shows-defaults.md).
5. **Raw names** in Expert panels and in the Guided drawers that reuse them. → [05](issues/05-raw-names-in-ui.md), Q2.
6. **The goal shows two values at once** ("3 h/yr (assumed)" and an empty "No goal yet"). Backup and control-mode inputs sit only in Expert Scenarios → Grid study. → [06](issues/06-goal-and-backup-consistency.md).
7. **Assumed defaults** come from the template ledger (`flow_service.py:855-899`). Each shows as "(assumed)" with **Keep all defaults** / **Edit ›** in its section ✓ (DC: 4 in section 3, 1 in section 8; microgrid: 2 in section 1, 7 in section 5, 2 in section 7, 1 in section 8). A value without a ledger row is pre-filled silently, with no "assumed" mark. That covers loads, candidate bounds, costs, tariff rates, VOLL and draws, so "set by the user" vs "accepted default" (§2.3, §3.2) is visible only for ledger values.
8. **Silent fallbacks in the run** (code; [08](issues/08-silent-run-fallbacks.md)): `mc_draws` 500, `site_archetype` strong_grid, efficiency 1.0, VOLL 0.0 on the backend (`copt_endpoint.py:54`), carrier outage defaults. None is shown as assumed.

## 3. Decisions per section

Key, after §2.3: **R** required, **P** pre-filled, **opt** optional, **–** not needed. "G" = Guided, "X" = Expert. Columns: DC = `eh_datacenter` / `_staged`, Site = `eh_site`, MG = `eh_microgrid*`, H2 = `eh_h2_hub*`, NH3 = `eh_ammonia_staged`, Conc = `eh_concentrator_staged`, IPP = `eh_ipp_hybrid`.

### 1 Project & site

| Decision | Templates (key) | Guided | Expert | Settable | Assumed shown | Gap |
|---|---|---|---|---|---|---|
| Location (lat/lon) | all **R** | Section 1 note → Open here › → Latitude (°), Longitude (°) | Model Horizon panel, the same fields; bus table lon/lat | ✓ G, ✓ X | no ("no location" note) | 01 |
| Site type | Site **R** (form); others P | Site form "Site type" radios; section 3 "Grid connection" select | Grid connection → Site type | ✓ | no | – |
| Horizon: periods / stages | DC staged, MG staged, H2 staged, NH3, Conc **P** | Site form growth stages (Site) only; templates: Open here › drawer shows the Model Horizon read-only cards | Model Horizon: Mode, Snapshot window, Representative weeks, Weightings | ✓ X; G Site only | MG: "Single-year horizon … (assumed)" ✓ | 07 |
| Resolution / representative weeks vs chronological year | all **P**; NH3 chronological **opt** (GTP-09) | – | Model Horizon | ✓ X | MG ✓ | 07 |

### 2 Carriers & processes

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Carriers (+ unit) | all **P** | list; Edit jumps to X | Carriers table | X only | no | 03 |
| Process recipe (electrolyser, Haber-Bosch, mill lines) | H2, NH3, Conc **P** | "Add a process unit in Expert ›" | Link property panel (efficiency per port) | X only | no | 03 |
| Heat carrier | Site **opt** | – | Carriers | X only | no | 03 |

### 3 Connection & grid

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Grid strength (strong / weak / none) | all **P** | "Grid connection" select ✓ | Grid connection → Site type ✓ | ✓ both | no | – |
| Import limit (MW) | DC **R** confirm; Site **R** (form "Connection limit (MW)"); H2, NH3, Conc, IPP **P**; MG – | read-only header "grid_import, 40 MW" | `grid_import` Link → Capacity | X only for templates | no | 02 |
| Energization per period | DC staged, Conc **P** (assumed) | – | Link per-period capacity (code) | code | ledger "assumed" (code `eh_templates.py:411`) | 02 |
| Ramp limit (MW/min) | DC **R** confirm | "Connection ramp limit: 4 MW/min (assumed)" → Edit › value/source/note ✓ | Grid connection → Connection limits ✓ | ✓ both, same labels | ✓ | – |
| Oscillation band, fault level, import efficiency | DC **P** assumed | Edit › ✓ (efficiency: broken) | Grid connection ✓ / Link | ✓ / efficiency ✗ in G | ✓ | 02 |
| Grid code profile | DC **opt** (generic assumed) | – | Grid connection → Grid code select (`eu_rfg_dcc_ce` default), Grid-code document upload | X only | no | 07 |
| Point of connection / export link / time zone | all with grid **P** | – | Grid connection → Site connection | X only | no | 07 |
| Islanded: no fence | MG **–** | section reads "not needed · Off-grid" ✓ | Grid connection shows Site type off-grid | ✓ | n/a | – |

### 4 Loads & demand

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Load peaks / stages | all **P**; Site **R** (form stages) | list "Peak load: 34.49 MW"; Edit › opens profile generator at defaults | Load panel: Demand, Profile (archetype, Peak MW, advanced) | ✓ both, but the editor does not show the current load | no | 04 |
| IT load, PUE | DC **P** (creation form) | creation form only | not found after creation | at creation only | no | 04 |
| Critical share / tier | DC, Site, NH3 **P** | Site form "Must stay on (%)"; else – | not found in the load panel | Site at creation only | no | 04 |
| GPU / AI-training profile | DC **opt** (DC-01) | archetype "ai training dc" in the load editor | same | ✓ (raw label) | no | 04, 05 |
| Non-electric demand (H2 offtake, NH3 t/d, concentrate t/h) | H2, NH3, Conc **P** | listed with own unit ✓ ("485.85 t/h") | editor asks "Peak MW" | value ✓, unit ✗ | no | 04 |
| Flexibility (curtailable / deferrable) | all **opt** | – | not found | not found | no | 07 |
| Loads | IPP **–** (generation-only) | – | – | n/a | n/a | – |

### 5 Assets & sizing

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Candidate set (which assets may be built) | all **P**; Site form candidates | list ("Capacity: sized by the study"); Edit › → capacity-bounds table (min, max, Single/Per-period) ✓ | Generator/Store panel: Extendable ✓, Maximum capacity ✓; Capacity Bounds panel | ✓ both | MG: "Existing island … overnight cost 0 (assumed)" ✓ | – |
| Overnight cost, lifetime, FOM | all **P** | via Edit › (asset form) | property panel ✓ (Default tag) | ✓ X; G via drawer | "Default" tag, not "assumed" | 07 |
| Outage data (FOR, MTTR) | all **P** | readiness note when missing ("subsea_tie" on MG) → Open here › | property panel ✓ | ✓ X | no | – |
| Backup mode | DC **opt** (DC-03), Site, hospital | DC creation form only | Scenarios → Grid study (ride-through, UPS bridge, fuel autonomy, genset N+k) | at creation / X under section 8 | ✓ (source select "assumed") | 06 |
| Control presets | DC **opt** (DC-04); any converter asset | – | Scenarios → Grid study "Control mode of …" (raw options) | X under section 8 | no | 05, 06 |
| Power architecture | DC only **opt** (DC-05) | DC creation form only | not found after creation | at creation only | no | 04 |
| Unit commitment (min load, min up/down, restart) | NH3 Haber-Bosch **P**; gensets | – | Grid study: "Linearised unit commitment for the fixed-size gensets"; per-asset fields not seen | code | no | 07 |

### 6 Time series & profiles

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Load profiles | all **P** | list; Edit › opens the Time Series table | Time Series panel ✓ | ✓ | provenance column (code) | 04 |
| Renewable profiles | DC **opt**; MG **R** if PV/wind added | "From the network" rows; Year select ✓ | Time Series, Profiles | ✓ | no | 01 (needs location) |
| Availability (maintenance, must-run, derating) | all **P** | asset Edit › → Availability block | asset panel → Availability ✓ | ✓ | no | – |

### 7 Commercial & parties

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Tariff (import price, items, periods) | DC, Site, H2, NH3, Conc **P**; MG – | Commercial card: Import tariff editor ✓, "Fill values from a PDF…", "Save as the project tariff" | Economics panel ✓ | ✓ both | no | – |
| Discount rate, inflation | all **P** | "More settings" → Discount rate (%) ✓ | Economics → Discount rate (%) ✓; per-asset "Discount rate" in the asset panel | ✓ both | MG: "Finance inputs … (assumed)" ✓ | – |
| Currency, money year, price basis, financial close, analysis years, COD per asset | staged **P** | MG: Commercial shows them ✓ | Economics ✓ | ✓ | MG ✓ | – |
| Contracts (PPA, swap, DR) | DC **opt**; IPP **P** | "New contract type" select ✓ | Economics | ✓ | no | – |
| Investment case baseline | MG **P** | "Investment case: compared with the island as it stands today (assumed)" ✓ | Economics | ✓ | ✓ | – |
| Grey-NH3 reference purchase | NH3 **P** (GTP-06) | not yet checked in the app (template walk follow-up) | not yet checked | code | code | – |
| Parties, flex market | DC **opt** (C8, DC-06) | – | not yet checked in the app | code | no | 07 |

### 8 Scenarios & optimisation

| Decision | Templates | Guided | Expert | Settable | Assumed | Gap |
|---|---|---|---|---|---|---|
| Reliability goal (LOLE h/yr) | DC **R**; others P; H2 "the run asks" | Goal card "Allowed shortfall (hours per year)" ✓ (shown empty while the assumption row says 3 h/yr) | Solver Settings → reliability goal ✓ | ✓ both | ✓ but contradicted | 06 |
| ENS cap, simulation runs per check, VOLL, stress conditions | all **P** | Goal → More settings (ENS strictness, Simulation runs, Price of undelivered energy, Stress) | Solver Settings (MC draws, ENS target) | ✓ both | no | 06, 07 |
| What the run computes (scope) | all **P** | "The Run computes: … · Change" → scope checkboxes ✓ (Load flow, SCLOPF, Process detail, Island and Dynamics "Not yet available") | Solver Settings, the same checkboxes | ✓ both | n/a | – |
| Variants / scenarios to compare | all **opt** | – | Scenarios → "Compare <project>" checkboxes | X only | no | 07 |

### 9 Readiness → run and 10 Results → reports

Nothing to decide: Readiness lists each section's state and offers **Run study**, and Results shows the verdict, investment, grid plan and reliability. The checks for step 2: Run starts from section 9 in Guided (`hub-run-start`) and from the header in Expert. The verdict (`hub-results-verdict`) names the goal. The decisions above move the result: a lower LOLE target, or a smaller candidate maximum, changes the verdict or the sized capacity.

## 4. Expert: what each run function reads, and where Expert sets it

"Run study" runs the reliability goal study (`POST /results/eh_study`, `eh_study_runner.py:347`). The scope options select its stages (`flow_scope.py:70-131`).

| Function | Reads (code) | Where Expert sets it | Findable |
|---|---|---|---|
| Capacity expansion / asset sizing | `p_nom_extendable`, `p_nom_max`, `capital_cost` (overnight cost, lifetime, discount rate), efficiency | asset property panel ✓; Capacity Bounds panel ✓ | yes; costs show "Default", not "assumed" |
| Dispatch & economics | `marginal_cost`, efficiency, tariff, import price | asset panel ✓; Economics ✓ | yes |
| Reliability | `target_lole_h`, ENS cap, `mc_draws`, VOLL, FOR/MTTR per unit, energization / import `p_nom` | Solver Settings ✓; asset panels ✓; import Link ✓ | yes, but the goal value conflicts (06) |
| Grid code & electrical design | grid-code profile, fault level, power factor, PCC ownership | Grid connection ✓ | yes; Campus file is raw YAML (05) |
| Control / converter presets | ramp limit, latency, control mode per converter | Grid connection (ramp) ✓; Scenarios → Grid study (modes) | modes under section 8, raw names (05, 06) |
| Commercial & finance | discount rate, tariff, contracts, COD, currency | Economics ✓ | yes |
| Load flow, N-1 (SCLOPF), process detail, island and dynamics | – | – | "Not yet available": journeys assert the label and skip these functions |

## 5. Guided and Expert differences (EXPERT-GUIDED-1)

| Parameter | Guided | Expert | Difference | Gap |
|---|---|---|---|---|
| Connection limit | read-only "40 MW" | `grid_import` Capacity, editable | editability | 02 |
| Load peak | "Peak load: 7.5 MW" | Load panel: Demand 0 MW, Profile → Peak MW empty | value | 04 |
| Concentrate demand unit | t/h | Peak **MW** | unit | 04 |
| Reliability goal | "3 h/yr (assumed)" and an empty field | Solver Settings value | value | 06 |
| Backup mode, control presets | creation form only | Scenarios → Grid study | grouping (section 5 vs 8) | 06 |
| Carriers, recipes | jump to Expert | Carriers / Link panels | editability | 03 |
| Ramp limit, oscillation band, discount rate, LOLE label | same editor | same editor | none ✓ | – |

## 6. Owner questions

<a id="owner-questions"></a>
Sent to the coordinator as options (HOLISTIC-1). **Answered 2026-11-17 (TEMPLATE-GAPS-1): Q1–Q4 option (a).** Q5 answered (a) (GAP03-ADD-1). Q4 (a) also asks for design §2.3 to gain concentrator and IPP columns; that edit belongs to the design doc's owner and is not made here.

- **Q1 Location.** Should a missing location block Run? (a) Block Run until set, as §2.3's R says (recommended: profiles and market bundles need it); (b) keep it a note and make location opt.
- **Q2 Component names.** Are template component ids (`it_load`, `genset_new`, `grid_import`) raw names under RAW-NAMES-1? (a) Yes: templates ship plain display names ("IT load", "New genset") and ids stay internal (recommended); (b) no: they are the user's own names.
- **Q3 DC load and backup choices after creation.** (a) Editable later in sections 4 and 5, in both modes (recommended: §2.3 lists them there); (b) creation-only, with "start again" to change them.
- **Q5 Adding carriers and process units in Guided** (issue 03). (a) Guided edits recipe values only; adding stays an Expert action behind a `withReturn` link (recommended, UX); (b) Guided can also add.
- **Q4 Concentrator and IPP.** (a) Add both to design §2.3 and to the journeys (recommended: they are in the catalogue users see); (b) journeys for the §2.3 templates only.

## 7. Gap index

| Issue | Sections | Templates | Status |
|---|---|---|---|
| [01](issues/01-location-required-not-asked.md) location | 1, 6, 9 | all | ready-for-agent (Q1 a) |
| [02](issues/02-connection-limit-in-guided.md) connection limit | 3 | DC, Site, H2, NH3, Conc, IPP | ready-for-agent |
| [03](issues/03-carriers-processes-jump.md) carriers, recipes | 2 | all; H2, NH3, Conc recipes | ready-for-agent |
| [04](issues/04-load-editor-shows-defaults.md) load editor | 4, 5 | all | ready-for-agent |
| [05](issues/05-raw-names-in-ui.md) raw names | all | all | ready-for-agent (Q2 a) |
| [06](issues/06-goal-and-backup-consistency.md) goal, backup grouping | 5, 8 | DC, MG, Conc | ready-for-agent |
| [08](issues/08-silent-run-fallbacks.md) silent run fallbacks | 5, 8, 10 | all | ready-for-agent |
| [07](issues/07-expert-only-decisions.md) decisions in neither mode / Expert-only and unnamed | 1, 3, 4, 5, 7, 8, 10 | all | ready-for-agent |
