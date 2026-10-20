# Workspace reconciliation: GridSpine (#436) on the project flow

Status: draft for UX review. Owner choices Q1–Q5 (§12) are drafted on the recommended option and marked *pending owner* until the coordinator relays them.
Date: 2026-10-20
Author: workspace-reconciliation session (commissioned through the coordinator)
Base: master `ef0856a`, with #436 (`claude/gridspine-workspace-specs`, files under `.scratch/workspace-redesign/`) and B1 #434 (`claude/flow-backbone-b1`) read at their heads.
Builds on: [design.md](design.md) (approved 2026-10-20: W1–W4, the Run gate R1–R4, the instantiation rule). Nothing here reopens those decisions.

## The owner's words

2026-10-20, on #436:

> "I like mostly the front end, and the kind of the design, it's modern, it's very sharp, in terms of fonts, in terms of design that it's used. And the color is okay. I like also the graphics of the different examples. ... in terms of workflow, this is roughly the workflow that I want to set up with. That you can compile the different examples, or you can build your own example. And then you basically go from there. But in terms of details, I think this is much too high level ... even in guided mode, I think there are more parameters to be set, which here would be missing. So that needs to be adapted. In terms of grouping, I think the grouping is fine. We wanted to capacity expansion, wanted to grid design, or grid code compliance, and then we want to do the control strategy and make sure that this can eventually be handed off to others. What is also interesting is have the ability that you switch between guided mode and expert mode. Expert mode, you need to regroup the different settings that we currently have in the UX ... in the expert mode, you have all the current designs ... grouped into coherent groups, the different functionalities. But in guided mode, you're more following this coherent set of instructions, and you only get showcased the necessary requirements or necessary parts to parametrization ... That does not complicate your workflow. ... I also like the current design of the chatbot. In this 3D chat, and you hover, you change things, it gives you options to do the three things as discussed. So that one we can keep. Assess the handoff, integrate it into your current design, as main part, make sure that if something doesn't match, then don't flow into it. ... You have a guided mode, you have an expert mode, and you can switch between them. ... In guided mode, it should be reduced in terms of complication. In expert mode, you can do everything everywhere you want. But then it should be grouped ... so that you still find the necessary things. There's no duplicate."

2026-10-20, about 18:00 CEST:

> "What's important in the UX design is the ability to always switch between expert mode and guided mode. If you're in guided mode, that you keep and your prompted in expert mode with all the details that you want, right? From that section. But then you have from expert mode, the ability to navigate into different parts. And parameterize the different parts. Or change different parts if needed. On top of that, you should also make sure that currently it's missing, but in the examples, you should have the ability to also enter coordinates within the examples. So that you have kind of the mapping of it. And then you should be able to place kind of these currently, I think we have kind of an overview of the site as a plan, that should somehow be kept. Also, you should have an overview of that, but then in the based on the real mapping, And I like the real map example, you have this canvas that you do the design, but then you have the real map canvas. Also think about how do you actually integrate converter setups or parameterized converters in expert mode and guided mode?"

## 0. The reconciliation in one paragraph

#436 is the **look and the entry**: GridSpine visuals, Home with image-led example cards, "build your own", the Builder, the Guided/Expert switch and the companion's three modes. The approved project flow is the **spine**: ten flow sections in a fixed order, one completion source (B1's `GET /flow`), one Run whose gate is the server's verdict. They meet on **two axes**:
- **Work groups** (the owner's grouping: Capacity expansion, Grid design & grid-code compliance, Control strategy, Hand-off) say *what the project should answer*. They are a per-project **scope**.
- **Input sections** (1–8) say *what gets parameterised*. Each value has one home section and one editor.

A group declares which blocks of which sections it needs. Switching a group on shows those blocks and adds its computations to the one Run. It never adds, removes or reorders a section; it behaves exactly like a template's instantiation (design §2.3). #436's three-section, nine-checkbox composition, its per-stage execution graph and its own rail are not adopted (§11).

---

## 1. Facts checked against master

#436 carries owner-supplied facts. Checked on `ef0856a`:

| #436 says | Master | Use |
|---|---|---|
| Staged periods 2027 / 2030 / 2035 | Yes, all three staged templates and the generic site's defaults (`project_templates/eh_templates.py:219`, `eh_staged_hubs.py:45`, `eh_site.py:98`); horizon end 2045 | as is |
| DC IT 12 / 24 / 36 MW, distinct from the 50 MW single-period campus peak | Yes. Staged: `DC_PHASES = ((2027, 12, 1.3), (2030, 24, 1.3), (2035, 36, 1.3))` (IT MW, PUE). Single period: 36 MW IT + 8–14 MW cooling + offices, "50 MW campus" in the description, 40 MW import | as is; note the staged campus has a **55 MW** connection energized 15 MW until 2030 (`eh_templates.py:223–228`), not 40 MW |
| Hydrogen hub 120 MW wind, 80 MW PV, 60 MW electrolyser, 40 MW process, 25 MW H₂ | Yes (`eh_h2_hub/eh_template.json`); 200 MW connection; staged offtake 10 / 18 / 25 MW | as is |
| Microgrid without its 20 MW subsea tie | Yes; tie normally open; archetype `off_grid` | as is |
| Ammonia (#418) unmerged | Yes: #418 open; no `eh_ammonia*` on master | card shows "In development", not creatable, until #418 merges |
| Mining after ammonia | No template, no issue on master | card shows "Planned"; no ID |
| `concept_*` IDs are preview-only | No such IDs on master | never sent to the API |
| "No distinct generic bundled template" | **Wrong**: `eh_site` (Generic Site Energy Hub: industrial, commercial, hospital, campus, other) exists | "Build your own" uses it (§3) |
| Stress weeks heatwave / dunkelflaute / low-wind | Exist per template (`adequacy_stress_scenarios.json`; `eh_templates.py:967–1004`, `eh_site.py:710–730`); edited only in Results › Reliability › FMEA today | home moves to section 8 (§4, design §2.4 J13) |
| Synthetic office / two-shift shapes | Load shapes exist in the generic site: industrial, commercial, hospital, campus, flat (`eh_site.py:156`) | §4 section 4, under TSG-01 |
| DC opt-in AI-training load (DC-01, #421) | Backend only: `dc_load` create body, `eh_datacenter*` only (`routers/projects.py:1478–1632`); no UI | §4 section 4 |
| "Generate defaults for all" | Not built (planned, RP/TSG) | §4 section 6, marked planned |
| Companion is "3D / dimensional" | Today a flat SVG (`components/CompanionLauncher.tsx`); #437 adds the dimensional character and the three modes | §9 |

---

## 2. Visual system (from #436, mapped onto the app)

The owner liked the fonts, the sharpness, the colour and the example artwork. They map onto the existing token layer; there is no parallel app.

| #436 | Today | Change | Where |
|---|---|---|---|
| **GridSpine** wordmark and document title | "PyPSA Studio" (`layout/Sidebar.tsx` wordmark, `AppHeader.tsx` / `uiStore.ts` document title, admin header) | User-facing wordmark and title become GridSpine; mark from `.scratch/workspace-redesign/reference-assets/gridspine-mark.svg` | `public/brand.css`, wordmark component |
| Manrope (variable, SIL OFL 1.1; licence `reference-assets/fonts/OFL-Manrope.txt`) | Inter | `--font-family-sans: 'Manrope', system-ui, sans-serif`. Ship the font file and its OFL text under `frontend/public/fonts/`, self-hosted (no CDN). JetBrains Mono stays for code/numbers | `index.css` `@theme`, `public/brand.css` |
| Graphite / navy dark theme as default | Dark default, red on near-black (`#151112`) | Dark surfaces become graphite/navy; the dark default stays | `index.css` dark block, `brand.css` |
| Cool neutral light theme | Light theme exists (`@theme`) | Cool steel/white surfaces | `index.css` `@theme` |
| Bright red actions; dark text on the bright fill; light red links in dark, darker red links in light | Red accent `#ff5252` / `#b3261e` | Same family; contrast per #436. Semantic success/warning/error and scientific colour scales keep their own colours and gain a label or icon wherever red could be read as danger | tokens only |
| Image-led example cards with original dioramas | No example gallery on Home | Five original SVGs (`.scratch/workspace-redesign/media/*.svg`, created by `create-industry-art.mjs`) move to `frontend/public/examples/` | Home (§3) |
| Thin borders, large type, sharp radius | Tailwind tokens | Radius, border and type-scale tokens per `styles.css` | `index.css` |

The one test that pins brand and tokens together (`frontend/brand.theme.test.ts`) is updated in the same PR.

**Not renamed:** the backend `APP_NAME` ("PyPSA Studio", `backend/app_paths.py:18`). It names the app-data folder, and renaming it would orphan every user's projects. The engine package `gridspine/` and the "Planning → dynamics" study keep their names. In the UI that study is labelled "Planning → dynamics", never "GridSpine", so the product name and the study name never collide.

---

## 3. Entry: Home and Builder

### 3.1 Home (#436 screen 1, adopted)

Sign-in lands on Home (`/projects`; `auth/resume.ts:getPostLoginPath` keeps deep links working). From top to bottom:

1. **Resume.** The last accessible project, opening at the section and mode it was left in (§7). Hidden when that project is gone or inaccessible (`pages/projectsHome.ts:shouldShowResume`, as today).
2. **Examples**, one card per industry, image-led:
   - Data centre (`eh_datacenter` / `eh_datacenter_staged`)
   - Island microgrid (`eh_microgrid` / `eh_microgrid_staged`)
   - Hydrogen hub (`eh_h2_hub` / `eh_h2_hub_staged`)
   - Ammonia: "In development" until #418 merges, then `eh_ammonia_staged`
   - Mining: "Planned", not clickable
3. **Build your own**:
   - your own site: the generic-site form (`eh_site`), with its site types;
   - start blank;
   - import a file;
   - clone a project.
4. **My projects**: the existing list.

Clicking a card (pointer, right-click on the card, Enter or Space) goes straight to the Builder. There is no variant chooser.

The legacy IEEE, Belgium and tutorial networks leave Home. They stay under New project › Templates, and the Decision study stays under New project › Decision (desktop-only, as today). Admin, folder import and legacy claiming keep their current places.

### 3.2 Builder (#436 screen 2, adapted)

The Builder asks four things and nothing that a section owns (B4's rule, kept):

| Field | Home section | Notes |
|---|---|---|
| Project name | 1 | |
| Site location: a map pin, or an address search | 1 | §8. Optional here; section 1 shows it as a gap until set |
| Planning period | 1 | *Pending owner, Q3.* Recommended: the native horizons only, "One year (2030)" or "Growth 2027 → 2030 → 2035", which picks the single or staged template. Other years are edited in section 1 after creation. Free start/end years would need template regeneration (engine work), and the DC AI-load spec enforces 2027/2030/2035 |
| Work groups (the scope) | 8 | §5. The example pre-fills them, and the user can change them. **The same component** edits the scope in section 8; the Builder is not a second editor |

"Create" calls the existing create-from-template route (`useCreateFromTemplate`, or `POST /from_template/eh_site` for your own site). The project then opens **in the flow, at the first incomplete section**, in Guided. Sections 3, 4 and 7 are never duplicated in the Builder.

"Build your own › Your own site" shows the generic-site form's *site type* only. Its numbers are section 3, 4 and 5 inputs, which open in their sections after creation, pre-filled from the site-type preset and marked `assumed`.

> Creating from the form's full values in one step (today's `GenericSiteForm`) is kept as the Expert path: New project › Your own site.

---

## 4. Guided: the same sections, with the real parameters

Guided shows the ten sections in order, as the approved rail (design §3.4). Each card follows one layout (agreed with the UX reviewer):
1. **Up to three decisions** at the top: the structural choices.
2. **Required inputs**: a compact list of every input the section needs for the groups in scope. Each row shows its label, value, unit and an `assumed` badge where the value is a template default the user has not confirmed. It is editable inline with **the same editor component as Expert**. Guided never hides a required input (design §3.5).
3. **Advanced for this section**: collapsed; everything else the section owns.

Group-only rows are tagged with the group that needs them. They appear only when that group is in scope.

The lists below are the parameters a Guided user sets today (generic-site form, Site and Goal cards) plus what the templates and backend need. Sources: `pages/hubDesign/cards/*`, `genericSite.ts`, `project_templates/eh_site.py`, `eh_templates.py`, `models/energy_hub.py`, `pages/modelHorizon/*`.

### 1 Project & site
- Decisions:
  - planning period (one year / growth stages);
  - site location (pin or address).
- Required inputs:
  - **project name**;
  - **site location**: lat/lon, with the country and timezone derived from it;
  - **growth-stage years**: 1–3 stages, whole years from 2025 to 2060, increasing, before the horizon end (2045);
  - **time base**: representative weeks, or a chronological year (GTP-09).
- Advanced:
  - resolution (15 min … monthly; hourly by default);
  - weeks per month (1–5);
  - sampling seed;
  - snapshot weightings;
  - per-period economics (years per period, objective weight, auto-discount, per-carrier load multiplier, CAPEX budget). Today these are Model Horizon's steps.

### 2 Carriers & processes
- Decisions:
  - the carriers handled (electricity always; heat, H₂, NH₃ when present).
- Required inputs:
  - each carrier with its unit, pre-filled;
  - a process recipe where the site has one: electrolyser kWh/kg; Haber-Bosch kWh/t and t H₂/t (GTP-07; read-only until GTP-02/07 land).
- Advanced:
  - carrier CO₂ intensity.

### 3 Connection & grid
- Decisions:
  - **grid connection**: strong / weak or limited / none (island). This is today's Site-card archetype.
  - **connection limit** (MW).
  - Grid group only: **grid-code profile** (generic assumed / EU RfG-DCC / an uploaded TSO PDF).
- Required inputs:
  - **available from grid per stage** (MW, the energization schedule; 0 ≤ value ≤ the limit; the stage peak from section 4 is shown beside it);
  - **fault level** (MVA; 20 × the connection by default, `assumed`);
  - **point-of-connection bus** (tagged by the template).
  - Grid group only:
    - **connection ramp limit** (MW/min; grid code, distinct from the LP's `ramp_limit_up`);
    - **oscillation band**;
    - **connection power factor**.
- Advanced:
  - import/export tariff link (read-only, home 7);
  - losses;
  - PCC switchgear and taps;
  - transformer data.

### 4 Loads & demand
- Decisions:
  - **load shape** per demand: industrial / commercial / hospital / campus / flat, or the template's profile. Synthetic shapes (#436's office and two-shift) are entries of the same list (TSG-01), labelled "synthetic, not measured" and scaled to the annual MWh;
  - data centre only: **AI-training load model** (opt-in, DC-01; `dc_load`).
- Required inputs:
  - **peak load per stage** (MW, > 0 and ≤ 500; not above the energized MW that year; the energized MW is shown beside it);
  - **must stay on** (% of load, 0–100; "typical 15–50 %");
  - **other demands** by carrier, in their own units: H₂ offtake (MW H₂ or kg/h), NH₃ deliveries (t/d);
  - data centre:
    - **IT MW per phase** (12 / 24 / 36 staged);
    - **PUE**.
- Advanced:
  - PUE temperature slope;
  - redundancy (N+1);
  - flexibility (curtailable or deferrable share);
  - criticality tiers (GTP-08);
  - the annual-MWh normaliser of a synthetic shape.

### 5 Assets & sizing
- Decisions:
  - **what the study may add**: gensets, battery or PV, each with an "up to (MW) per stage";
  - **backup mode** (genset / BESS / hybrid, DC-03), shown where a demand has a critical share;
  - Control group only: **converter preset** per converter-coupled asset (§10).
- Required inputs:
  - **existing gensets**: count (0–8) × unit size (MW) × fuel (gas or diesel);
  - **solar PV** (MW);
  - **battery** power (MW) and energy (MWh), both or neither;
  - **UPS**: power (MW; 0 or ≥ the critical load) and bridge time (min, 10–240);
  - **ride through grid outages** (needs a battery);
  - **outage data** per firm unit (forced-outage rate, repair time): today an `eh_readiness` "outage data" gap;
  - **overnight cost** of each candidate (`assumed` from the template).
- Advanced:
  - unit commitment (min load, min up/down, ramp, restart);
  - lifetimes;
  - the per-period vintage bounds;
  - the full Controls block (§10);
  - availability (maintenance, must-run, derating);
  - DC-05 power architecture (data-centre site type only).

### 6 Time series & profiles
- Decisions:
  - **Generate defaults for all** (planned: RP-09/TSG; one job, one undo; never overwrites `measured`).
- Required inputs:
  - one row per series with its provenance (`measured` / `generated` / `assumed` / `network_native` / missing) and its QA result;
  - each row opens its owner's editor in a drawer (design §2.1);
  - renewables need the section-1 location (RP-01).
- Advanced:
  - profile source (synthetic, or ERA5, which needs a CDS key);
  - weather year;
  - Profile Studio (Expert-only, RP-Q4).

### 7 Commercial & parties
- Decisions:
  - **tariff** (the template's import tariff, or one from the Library);
  - **discount rate**.
- Required inputs:
  - energy and demand charges (€/MWh, €/MW);
  - fuel prices (diesel running cost, gas);
  - inflation;
  - money year;
  - where the investment case is requested: parties and their shares.
- Advanced:
  - contracts (PPA, CfD, DR, lease, EaaS);
  - the party board;
  - the market bundle (C2);
  - reserve products (DC-06);
  - the reference purchase (GTP-06).

### 8 Scenarios & optimisation
- Decisions:
  - **work groups / what the Run computes** (§5);
  - **reliability goal**: the allowed shortfall in hours per year (LOLE); blank means "no goal";
  - **stress conditions**: baseline, heatwave, dark and still week, low-wind week, from the project's registry; the editor moves here from FMEA (J13).
- Required inputs:
  - **VOLL** (€/MWh, > 0). Today it is read-only in Guided (J1); here it is editable;
  - **CO₂ price**;
  - Grid group:
    - **load flow** (AC power flow after the optimisation, today's "Stage 2 AC PF");
    - **N-1 security** (SCLOPF);
  - Control group: **island requirements** (sustained hours, bridge seconds, fuel autonomy, grid-forming margin, frequency limits). Their home moves here from Studies › Campus electrical › Island.
- Advanced:
  - energy strictness (ENS cap, parts per 10 000);
  - solver and MIP options;
  - study budget, Monte-Carlo draws and seed;
  - rolling operation (DC-07);
  - scenario rows (B5);
  - global constraints.

### 9 Readiness → run
- No inputs.
- The preflight summary of 1–8 (B1's `/flow`).
- When Grid is in scope, the grid-readiness scorecard (#435 / #442).
- **Run**. It is disabled only when the server would refuse, and then names the section and field. Display-only gaps show as notes beside an enabled Run (R1–R4).

### 10 Results → reports
- Guided tabs, grouped by work group (§6.3): Summary, Reliability, Operation (Energy flow, Load shape); Grid plan when Grid is in scope; Controls when Control is in scope; Hand-off.
- No inputs. An input shown here links to its section.

---

## 5. Work groups as the scope

### 5.1 The groups

| Group | On | Adds sections' blocks | Adds to the one Run (`run_request`) | Results (10) |
|---|---|---|---|---|
| **Capacity expansion** (incl. dispatch and economics) | always | sections 1–8 core blocks | `optimisation` (exists); `study` when a reliability goal is set or the template carries the study (exists) | Summary, Investment (capacity expansion, economics, prices, investment case), Reliability (adequacy, lost load, FMEA), Operation (dispatch, energy flow, load shape, storage cycling, curtailment, emissions) |
| **Grid design & grid-code compliance** (incl. the load flow it needs) | template or user | 3: grid-code profile, ramp MW/min, oscillation band, PF; 5: lines, transformers, electrical infrastructure; 8: load flow, N-1 | `loadflow`, `grid_readiness` (after the gate, §11.1). N-1 SCLOPF is an option of `optimisation`, so it needs no new kind | Grid plan: load flow, scorecard, campus electrical (PCC compliance, transformers, reactive compensation, short circuit), connection capacity, connection-point assessment |
| **Control strategy** | template or user | 5: the Controls block of each converter-coupled asset; 8: island requirements | `control_checks` (after the gate) | Controls: island report, frequency cuts, grid-forming recommendation |
| **Hand-off to others** | always (*pending owner, Q1*) | none (no input section) | none | Hand-off: reports (per-party variant, C4), exports (bundle, NetCDF, Excel, CSV, MATPOWER), the specialist packet (Planning → dynamics export, PowerFactory read-back) |

Template pre-fill:

| Template | Capacity | Grid | Control |
|---|---|---|---|
| data centre | on | on | on |
| island microgrid | on | on | on |
| hydrogen hub | on | off | off |
| ammonia | on | off | off |
| own site / blank / import | on | off | off |

These follow #436's presets. The user can change any of them.

### 5.2 Rules (agreed with the UX reviewer and B1)

- **One home.** The scope is stored per project in `flow_scope.json` `{version, groups, options}`. It is added to `_BUNDLE_FILES` like `flow_acks.json`, so it travels with bundles, snapshots and forks. Section 8 edits it, and the Builder mounts the same component.
- **It behaves like instantiation.** It shows or hides blocks, marks a section `not_needed`, and extends `run_request`. It never adds or reorders a section. A section none of the groups in scope needs is `not_needed`, with its reason in the summary.
- **`run_request` and `verdicts` grow additively** (B1 confirmed): `run_request: {optimisation, study, loadflow, grid_readiness, control_checks}` as bools; `verdicts: {kind: {ready, blocker} | null}`, null when not requested. `run_ready` is the AND over the requested kinds (R1). A kind's blocking items are found by their item `source` (`validation`, `eh_study`, then `loadflow`, `grid_readiness`, `control_checks`). `run_blocker` stays the first blocking item in flow order.
- **Every new verdict function is non-blocking on the network lock** (the gatekeeper's requirement for `check_eh_study_start(lock_blocking=False)`). A held lock answers `deferred`.
- **Out-of-scope items** stay in the payload with `in_scope: false`. They:
  - don't count toward a section's state;
  - aren't in `notes`;
  - never block.

  Expert's Issues panel lists them under "Not in scope". Hiding a block is never a way to lose a blocker: a block is hidden only when its group is off, and then its kinds are not requested.
- **Unknown is non-runnable.** A kind whose separability classification (§11.1) is not "supported" is shown as "Not yet available" and cannot be requested.
- **Turning a group off** removes its kinds from future runs only. It doesn't delete inputs, cancel a job or erase results; its results show as "outside scope".

---

## 6. Expert: every destination, regrouped

### 6.1 Shape

The Expert sidebar becomes the ten sections, in flow order, each row with its state dot (design §3.4) and the group chips of the blocks it holds. It sits under three bands:

| Band | Sections |
|---|---|
| **Set-up** | 1, 2 |
| **Model** | 3–7 |
| **Run** | 8, 9 |

**Results & hand-off** (10) sits below, sub-grouped by work group (§6.3). **Project** utilities sit at the foot. This answers both of #436's Expert concerns:
- grouped by purpose;
- Run before Results.

The UX reviewer's condition is kept: study groups are where things are **run and read**, never where their inputs are edited.

Inside a section, Expert shows every editor the section owns: its tables (the bottom tables filtered to the section's component classes), the Properties panel for the selected component, and the section's panels. The canvas, the Properties panel and the bottom tables stay as they are, around the section.

### 6.2 Mapping: every current location to its new home

Gating column: the existing gate is **kept**. RO means "read-only role: the server refuses writes; the UI disables". Inventory source: a sweep of `frontend/src` on `ef0856a` (routes, `layout/sidebar/*`, `AppHeader.tsx`, `PropertiesPanel.tsx`, `BottomPanel.tsx`, `pages/*`). **Moves** are value homes that change; everything else keeps its component and moves only in navigation.

**Workspace (outside the flow)**

| Current | New home | Gating kept |
|---|---|---|
| `/login`, `/set-password`, `/reset-password` | unchanged | public |
| `/projects` Projects home | **Home** (§3.1) | RequireAuth |
| New project › Blank / From file / Clone | Home › Build your own | — |
| New project › Template (IEEE 14, IEEE 39, Belgium, 3-bus) | New project › Templates (not on Home) | — |
| New project › EH templates (6) and Your own site | Home › Examples / Build your own → Builder | — |
| New project › Study (Planning → dynamics) | New project › Study; listed under 10 › Hand-off for projects of that kind | — |
| New project › Decision | New project › Decision | desktop-only (auth mode 404) |
| New project › Folder | New project › Folder | desktop-only |
| Projects home › Import local projects | Home › My projects › Import | when unclaimed projects exist |
| `/admin/*` (users, organizations, legacy migrate, email) | unchanged | RequireAdmin; super-admin rules |
| Settings panel (assistant model, API key, log) | unchanged (header) | super-admin; desktop-only |
| Header: Projects breadcrumb, project name, Undo, Save, Properties toggle, Settings, user menu, Assign members | unchanged | RO / authEnabled |
| Header: command palette | unchanged; its "jump to" lists sections, then panels | — |
| Header: stat chips, component search | unchanged (Expert) | Expert only |
| Header: Guided / Expert switch | unchanged; section-anchored (§7) | — |
| Header: **Run** | stays in the header *and* section 9; one action, same gate (R1) | RO; gate = server verdict |
| Project tabs, status bar, shortcuts help, guided tours, rescale dialog | unchanged | — |
| Sidebar footer: Recent, Projects home, Save, Duplicate, Export bundle, Import, Open project, Autosave, Theme and density | Project utilities (foot of the sidebar) | Expert-only items stay Expert-only |
| Sidebar footer: canvas Select / Connect, zoom | canvas toolbar | Expert only |
| Sidebar footer: **Assistant nav button** | **removed**: the companion is the one entry (#436 owner decision; §9) | — |
| PROJECT › Workspace panel (ownership, lock, People) | Project utilities | Expert only; People needs authEnabled |
| PROJECT › Project info (capacity mix, activity, derive lengths, download) | Project utilities | Expert only; RO |
| PROJECT › Snapshots | Project utilities | per snapshot state |
| Bottom › Log, History | unchanged (bottom panel) | Expert only |

**1 Project & site**

| Current | New home | Gating kept |
|---|---|---|
| MODEL › Model Horizon (Summary, Mode, Investment years, Period economics, Snapshot window, Representative weeks, Snapshot weightings) | 1, Horizon block (all steps unchanged) | multi-period steps as today |
| *(none: no site location field)* | 1, Site location (new; §8) | RO |
| Header project name (rename) | also shown in 1 (same editor) | RO |

**2 Carriers & processes**

| Current | New home | Gating kept |
|---|---|---|
| Bottom › Carriers table | 2 | attribute catalogue |
| Properties › Carrier (display name, unit) | 2 (and Properties when a carrier is selected) | — |
| *(none: recipes)* | 2, Processes (B6, after GTP-02) | — |

**3 Connection & grid**

| Current | New home | Gating kept |
|---|---|---|
| Properties › Link › Energy Hub role (`grid_import`), capacity | 3, Connection (the import/export Links) | role refused when study-internal |
| Properties › Bus › Energy Hub (PoC flag, critical bus, short-circuit level, IBR capacity) | 3 (PoC, short-circuit level); critical bus → 4; IBR capacity → 5 | — |
| Economics › Basis › Site connection (commercial PoC, timezone) | **moves** to 3 (J7) | — |
| Links availability series (energization, no labelled editor) | **moves** to 3, Energization table (new editor) | — |
| Campus › Grid codes (upload, drafts, published profile) | **moves** to 3, Grid-code profile [Grid] | — |
| Campus › Study settings: connection PF, PCC switchgear and taps | **moves** to 3 [Grid] | shown when a campus file exists |
| Bottom › Buses, Lines, Transformers, Shunts tables | 3 for boundary/grid buses and branches; 5 for site-internal electrical infrastructure, by `section_for_component` (B1 follow-up) | attribute catalogue |
| Properties › Bus (voltage, carrier, control, country), Line, Transformer, Shunt | by the same rule (3 or 5) | — |
| Properties › Bus › Coordinates | 1 for the site pin; per bus wherever the bus is edited (one editor, `CoordPairInput`) | — |
| Solver Settings › Network › transmission losses | 8 [Grid] | disabled with SCLOPF |

**4 Loads & demand**

| Current | New home | Gating kept |
|---|---|---|
| Bottom › Loads table; Properties › Load (p_set, profile link) | 4 | — |
| Time Series › Loads tab (electricity, hydrogen, heat, other) | 6 (overview); each row opens the load's editor in 4 | — |
| Properties › Bus › Energy Hub › critical bus | 4, criticality on the demand | — |
| Asset palette › Electrical / Hydrogen / Heating demand | 4, "Add demand" | Expert |

**5 Assets & sizing**

| Current | New home | Gating kept |
|---|---|---|
| MODEL › Assets palette and Creation form | 5, "Add asset" (B3 tiered picker) | Expert; RO via server |
| Properties › Generator, Storage unit, Store, Link (conversion roles), Asset group | 5, the B3 asset form (General → Sizing → Controls → Reliability → Commercial → Availability) | — |
| Bottom › Generators, Storage, Stores, Links (non-boundary) tables | 5 | attribute catalogue |
| MODEL › Capacity Bounds | **folds** into 5 › Sizing (J15); the table view stays as 5's "All bounds" view | vintage bounds when multi-period |
| Properties › Vintage period bounds | 5 › Sizing | multi-period |
| Campus › Island › per-unit control mode | **moves** to 5 › Controls (J16; §10) | — |
| Campus › Island › Grid-forming settings (droop, τ_f, H, i_max) | inputs → 5 › Controls; the recommendation → 10 › Controls with an "apply" that writes 5 through the confirmation card | after the island config is saved |
| Campus › Campus file (YAML), Campus › Library | 5 [Grid], electrical infrastructure (raw file Expert-only); the library is picked inline (C1) | hub project kind |
| Economics › Asset costs (overnight cost) | 5 › Commercial block on each asset; the table view stays as 7's "All asset costs" read-only view linking to 5 | — |
| Time Series › Renewables, Conventional, Demand Response, Links | 6 (overview); each row opens the asset's Availability block in 5 | — |
| Properties › Bus › Energy Hub › IBR capacity | 5 | — |
| Rescale dialog | 5 (and 4), unchanged | — |

**6 Time series & profiles**

| Current | New home | Gating kept |
|---|---|---|
| MODEL › Time Series (all five tabs) | 6, the overview and QA | — |
| MODEL › Profile Studio | 6 (Expert depth), reached from the Renewables tab; leaves the sidebar (RP-Q4) | ERA5 needs a CDS key |

**7 Commercial & parties**

| Current | New home | Gating kept |
|---|---|---|
| Economics › Basis › Rates (discount rate, inflation), Finance inputs | 7 (the single discount-rate home) | — |
| Economics › Tariffs and contracts (tariff, contracts, participants) | 7 | participants RO |
| Economics › Library | Library page (workspace) + inline pickers in 5 and 7 (C1) | — |
| Results › Investment case › Participants, Tariff, Contracts, Finance inputs, Library | **move** to 7 (J8); Results keeps the read-only Bill and Investment case with links | RO |
| Solver Settings › Dispatch › Economic assumptions (discount rate, lifetime) | discount rate read-only, linking to 7 (J6); lifetime → 5 | — |
| Decision › Prices and tariffs, Finance, Assumptions ledger | stay inside the Decision study (own project kind, desktop-only); not a flow destination | desktop-only |

**8 Scenarios & optimisation**

| Current | New home | Gating kept |
|---|---|---|
| RUN › Solver Settings › General (clustering, rolling horizon, Stage 2 AC PF chaining) | 8 (Stage 2 AC PF is the Grid group's `loadflow`) | AC PF needs `canRunAcPf`; rolling disabled with periods |
| Solver Settings › Solver, Dispatch (MIP, CO₂ price), Add. constraints | 8 | per installed solver |
| Solver Settings › Network (VOLL, ENS target, reserve margin, DR, N-1 SCLOPF) | 8 (VOLL editable in Guided too) | losses off with SCLOPF |
| Results › Reliability › FMEA › Stress scenario editor | **moves** to 8, Stress conditions (J13); FMEA keeps the results | max 10 scenarios |
| Guided Goal card (LOLE, ENS, archetype, VOLL) | 8 (archetype → 3) | — |
| Campus › Study settings (hours per criterion, design margin, transformer N-1, library buy) | **moves** to 8 [Grid] (question asked of the campus study) | when a campus file exists |
| Campus › Island (ride-through, islanding instant, requirements, capability cost) | **moves** to 8 [Control]; per-unit controls → 5 | — |
| PROJECT › Scenarios (scenario tree) | 8, scenario rows (B5) | RO; mutation guard |
| Planning → dynamics › Configuration (k, UC window, overlap, N-2 prune) | 8, for projects of that kind | locked while running |
| *(new)* Work groups / what the Run computes | 8 (§5) | RO |

**9 Readiness → run**

| Current | New home | Gating kept |
|---|---|---|
| RUN › Issues (preflight, site layout, topology) | 9, grouped by section; "Not in scope" list (§5.2) | site layout in site view |
| RUN › Solve Queue | 9 | admin actions super-admin |
| Header Run (LOPF / Load flow / AC PF) | 9 + header (one Run) | gate = server verdict |
| DC-02 scorecard (#442: Expert Issues, Guided Site card) | 9 [Grid] verdicts; its input rows in 3 | — |
| Guided Site card readiness rows (`eh_readiness`) | items on their sections (3, 4, 5, 8) and in 9 | — |

**10 Results → reports**: see §6.3.

| Current | New home | Gating kept |
|---|---|---|
| Results › Summary, Overview | 10 › Capacity | Overview Expert-only, multi-period |
| Results › Investment › Capacity expansion, Economics, Prices, Investment case view, Bill | 10 › Capacity | Expert-only tabs stay Expert-only |
| Results › Reliability › Adequacy (COPT, reserve margin, frontier, MC, loops, EH reference design), Lost load, FMEA results | 10 › Capacity › Reliability | as today |
| Results › Operation › Dispatch, Energy flow, Load shape (#440), Storage cycling, Curtailment, Emissions | 10 › Capacity › Operation | as today |
| Results › Asset detail; Properties › View results | 10, unchanged | Expert |
| Results › Grid plan › Load flow | 10 › Grid | the "turn on in Solver settings" CTA links to 8 |
| Campus › Results (Investment, PCC compliance, Transformers, Reactive compensation, Short circuit, Critical hours) | 10 › Grid | after a campus run |
| Planning → dynamics › Connection capacity, Connection-point assessment, Ranked snapshots | 10 › Grid | locked while running |
| Campus › Island › Frequency cuts; island study results | 10 › Controls | after the island config is saved |
| Guided Results card (chapters), Improve card (findings, sweep, Open FMEA) | 10 (Guided view), unchanged in content | — |
| PROJECT › Reports (generate, viewer, template picker, version diff, mapping plan, round trip) | 10 › Hand-off | as today |
| Sidebar › Export bundle (NetCDF, Excel, CSV, MATPOWER) | 10 › Hand-off (also a Project utility) | Expert |
| Planning → dynamics › Dispatch source, PowerFactory read-back, Assumptions ledger | 10 › Hand-off | locked while running |
| Results › Compare rail | 10, unchanged | — |
| Decision study (Hub, Answers, Options, Run, Verdict, Why and how, How robust, Report) | its own project kind and rail, unchanged; reached from Home › My projects | desktop-only |

**Canvas, unchanged in place, shared by all sections**

| Current | New home | Gating kept |
|---|---|---|
| Canvas switcher Blank / Satellite / Hybrid / Site 3D (`MapModeSwitcher`) | renamed **Schematic / Map / Hybrid / Site plan** (§8) | RO for edits |
| Schematic toolbar (add bus, connect, fit, auto-layout, reset, results overlay, grouping, legend) | unchanged | — |
| Map toolbar (import geometry, add or connect bus, OSM, site boundary, find an address, unplaced buses) | unchanged; "Find an address" also sets the section-1 site pin | RO |
| Site 3D overlay (site picker, sized, arrange, reset, snapshot, walk, fit, findings, legend) | unchanged | RO; needs a site |
| Snapshot picker / playback | unchanged | needs results |

**Not mounted today** (`PasswordTokenPage`, `LoadEditor`, `LoadProfileManager`, `GenerationStack`, `TopBar`, the full-panel `compare` id) stay unmapped; they have no UI entry now and gain none.

**Duplicates removed:**
- discount rate (7 only);
- tariffs, contracts, participants and finance (7 only, not in Results);
- grid-code profile (3 only);
- per-unit island controls (5 only);
- Capacity Bounds (5 › Sizing only);
- stress scenarios (8 only);
- the Assistant nav button (the companion only).

Each was edited in two places before. Each move is in B2's list (design §5), kept.

### 6.3 Results grouped by work group

| 10 › | Holds |
|---|---|
| Capacity | Summary; Investment; Reliability; Operation |
| Grid | Load flow; scorecard verdicts (read-only copy of 9); campus electrical results; connection capacity; connection-point assessment |
| Controls | island study results; frequency cuts; grid-forming recommendation (its "apply" writes the section-5 values through the confirmation card) |
| Hand-off | Reports; exports; the specialist packet |

A group out of scope shows its tab as "Outside scope", never as passed.

---

## 7. The Guided ↔ Expert switch keeps the section

- **One position, two depths.** `uiStore` gains `activeSection` (a flow section id), shared by both modes. The section's selected entity is kept beside it. `setUiMode` already keeps the project, the selection and the canvas (`uiStore.ts:742`). It now also keeps `activeSection`, and opens that section in the new mode instead of closing panels by kind.
- **Guided → Expert** lands on the same section with every field shown. **Expert → Guided** lands on the card of the section that holds the current panel or selected component (B1's `section_for_component`). From Expert the user navigates anywhere; the state dots show the same state as Guided (design §3.5).
- Resume reopens the last mode and section.
- A Guided card's "Edit in Expert" link and the "Help with this section" companion entry (§9) both carry the section id.
- The switch never runs, saves or approves anything. Autosave behaviour is unchanged.

---

## 8. Coordinates and the two canvases

- **The owner's "real map example"** is the app's Leaflet map, `pages/MapCanvas.tsx` (Satellite and Hybrid; Esri tiles, address search, click-to-place, bus drag, the unplaced-buses panel). The **site plan** is `pages/SiteCanvas.tsx` (the 3D site view; `site3d/*`) and the **schematic** is `pages/TopologyCanvas.tsx`. All three are views of one model, switched by `MapModeSwitcher`.
  - #436's Expert preview shows the same idea as Network / Map / Site tabs.
  - The switcher keeps its four modes, labelled **Schematic / Map / Hybrid / Site plan**. Guided shows Map and Site plan as a thumbnail on the section-1 and section-5 cards. The existing `SiteThumbnailCard` "Open in 3D" stays.
- **Site location lives in section 1.** A map pin, or an address search (`api/geocode.ts`) on the real map. Country and timezone are derived. The template examples ship every bus at 0,0, which `utils/geo.ts` and `network_geometry._bus_coord` treat as unplaced, so today renewable profiles refuse them (RP-01 `missing_coordinates`).
- **Placing the pin** (*pending owner, Q4*): recommended, the pin places every **unplaced** bus in a compact cluster around it, keeping the schematic's arrangement. The user then drags buses on the map. Placed buses are never moved. It is one undoable batch of bus `x/y` writes (the existing `busPositionPatch`), with no engine change. Line lengths are recomputed only where "derive lengths from geometry" is on, as today.
- **Per-bus coordinates** keep their one editor (`CoordPairInput` in the bus form). The Map is the visual editor of the same values.
- **Section states:**
  - section 1 reads `warning` "no site location" (display only, kind (c); B1 already emits `site_location_missing`);
  - section 6 lists each renewable profile blocked by coordinates, with a link to the pin (RP-01).
- **Templates do not invent locations.** An example card may suggest a default region in its description, but no coordinates are written until the user sets the pin.

---

## 9. Companion

- **Keep** the current companion and its dimensional character (#437 brings the character). One companion; the sidebar Assistant button goes (§6.2).
- **Three modes on hover, focus or tap**: Write (native composer, retained draft), Dictate (reviewed dictation; nothing is recorded until its own record control is pressed), Live chat (the in-page text conversation). They reuse `AssistantDock`'s `onCompose`, `onSpeak` and `onLive`. Continuous voice stays unavailable and labelled. The greeting tilt and the live-text orbit respect reduced motion. This is #437 (draft) as built.
- **Placement is LC-04's** (#441, UX OK on `5488f4ba`):
  - the launcher is portalled into `#companion-anchor` inside the canvas column, above the minimap;
  - it never sits over Properties, a slide panel or the bottom panel;
  - on full-screen tabs (Results, the Guided flow) it stays in the workbench row;
  - compact when idle; `.companion-extras` (the model chip and the "voice not connected" note) is clipped while idle and shown on hover or focus.

  The three-mode menu keeps that note reachable (UX condition). Any change to the launcher's DOM or size is pinged to LC-04.
- **Transient cards never cover content.** The coordinator's request; LC-04 owns the fix. A transient companion card, such as `CompanionOutcome`'s "Answer not checked" or an approval or outcome toast:
  - docks into the launcher's own footprint, or collapses to a badge on the launcher after a few seconds;
  - never overlays a section card, the Run button, the Properties panel or a table;
  - leaves the footprint reserved (a section card's bottom padding equals it), so the Run bar and card actions never sit under the launcher.
- **"Help with this section"** on each section card (#436's "Help parameterize this stage") opens Write with a prepared draft and the section id in `ui_context`. It never sends on its own.
- The old→new **edit card** (LC-04 PR-1, `components/EditCard.tsx`) names the flow section and field. Not built here.

---

## 10. Converters in Guided and Expert

Existing pieces:
- the schema `gridspine/schema/island.py`: kinds `bess, pv, wind, fuel_cell`; controls `gfl_pq, gfl_qu, gfl_pf, gfl_fw, gfm_droop, gfm_vsm`; four presets; `offered_services`;
- the sidecar `island_config.json`;
- `POST /api/campus-electrical/{name}/island/preset` (no UI calls it today);
- the GFM tuner `/island/gfm-settings`;
- `CampusIslandSection` (per-unit control dropdown) and `CampusIslandGfmSection`.

| | Guided (Control group in scope) | Expert |
|---|---|---|
| Where | the asset's card in section 5 | the B3 asset form's **Controls** block, section 5 |
| Decision | one **preset** per converter-coupled asset (*pending owner, Q5*): Grid-following / Grid-forming support / Fast frequency response / Voltage support, which is gridspine's `PRESETS`. Only presets valid for the kind are offered (grid-forming support and FFR are BESS-only) | control mode (`gfl_pq`, `gfl_qu`, `gfl_pf`, `gfl_fw`, `gfm_droop`, `gfm_vsm`) and its parameters |
| Required inputs | rating (`pf_rated`, read-only, from Sizing); the services it offers (from `offered_services`, read-only) | `pf_rated`, `neg_seq`; `cos_phi` / `cosphi_p_points`; `qu_points`; FFR `fw_k_mw_per_hz`, `fw_deadband_hz`, `fw_delay_s`, `fw_ramp_mw_per_s`, `fw_p_limit_mw`; GFM `droop_pct`, `q_droop_pct`, `tau_f_s` or `H_v_s`, `i_max_pu`, `k_gfm_min`; UPS `walk_in_s`, `f_window_hz`, `transfers_on_islanding` |
| Site-level | island requirements in 8 (sustained hours, bridge s, fuel autonomy, GFM margin, RoCoF window, frequency limits) | the same, in 8 |
| Warnings | `preset_leaves_no_grid_former`, `preset_not_applied_to_unit` show as section-5 warnings | the same |
| Results | 10 › Controls; the GFM recommendation's "apply" writes 5 through the confirmation card | the same |

There is one editor: the island panel's per-unit dropdown and GFM inputs move to 5 (J16). DC-04's presets (#422) become this block. The engine side (which kinds and controls the island check credits, and whether the preset route is the right write path from an asset form) goes to the gatekeeper with §11.1.

---

## 11. What is not adopted from #436, and why

| # | #436 | Instead | Why |
|---|---|---|---|
| N1 | Three work sections × nine checkboxes, a typed dependency graph with per-stage admission and receipts | The scope (§5): groups and a few options that are run kinds of the one Run | W4 (one Run); a second execution engine is what #436 itself warns against; B1's per-kind verdicts already give per-kind readiness |
| N2 | Guided rail "Model & inputs → selected activities → Review & run → Results" | The ten-section rail; groups show as chips and blocks | Templates and scope never add or reorder sections (design §2.3); W1 and W2 fix carriers in 2 and the horizon in 1 |
| N3 | Peak demand edited in the "Capacity expansion" stage | Section 4, one editor; other places read-only with a link | One editor per value (§6 checklist 2) |
| N4 | Reliability objective inside an optional Reliability stage | Section 8 (reliability goal, VOLL, ENS, stress) | Design §2.1: the question asked of the model lives in 8 |
| N5 | Stress weeks and synthetic shapes in "Model & inputs" | Stress in 8; shapes on the load (4) under TSG-01 | One home per value; "Model & inputs" is not a section |
| N6 | Free start/end planning years at entry | Native horizons; other years edited in section 1 (*pending Q3*) | The templates' periods are fixed in code; generating other years is engine work |
| N7 | "No distinct generic template" | `eh_site` is "your own site" | It exists on master |
| N8 | Expert groups Network analysis / Reliability / Controls & dynamics holding inputs; Run & validation after Results | Inputs in sections 3, 5, 8; those groups are results tabs in 10; Run (9) before Results | No study-hosted or results-hosted inputs (J7, J8, J10, J13, J16) |
| N9 | "Contingencies & resilience" and "Frequency & time domain" as selectable activities | N-1 SCLOPF is an option of the optimisation (8, exists). Others are "Not yet available" until classified | Unknown stays non-runnable (§11.1) |
| N10 | "Electrical infrastructure sizing" as a peer activity | Campus electrical study, a post-solve study in 10 › Grid with its inputs in 3, 5 and 8 | It may run a least-cost solve first: bundled, not separable, until the gate says otherwise |
| N11 | Product rename everywhere | Wordmark and titles only; `APP_NAME` and the gridspine study keep their names | The app-data path; name collision |
| N12 | The preview's top nav (Home / Builder / Guided / Expert) | The existing header with the mode switch | The preview's nav is a demo device |
| N13 | A versioned composition with stage ids, adapter versions and bindings | `flow_scope.json` `{version, groups, options}` | Nothing in the one-Run model needs more |
| N14 | A single bottom-right companion position | LC-04's canvas anchor (#441) | UX OK'd fix for the overlap |
| N15 | Select all / Clear / Use example preset over nine choices | "Use example preset" over the groups; no Select all | Three switchable groups don't need bulk controls |

Adopted as is: the visual system; Home with Resume, examples and build-your-own; the image-led cards and artwork; the Builder as the entry; the Guided/Expert switch keeping position; "Help parameterize" (§9); the "Your model" side card (the section-1 card's illustration or site thumbnail); the companion modes; the separability gate.

### 11.1 The separability gate (#436 slice 0, kept)

No kind is added to `run_request`, and no study becomes selectable as runnable, before it has an evidence-backed row in the matrix of `.scratch/workspace-redesign/backend-separability-assessment.md`. The matrix records:
- the classification: supported / bundled / unsupported / unknown;
- the entry point;
- the inputs and outputs;
- shared state and locks;
- implicit sub-steps;
- the freshness boundary.

It adds a precondition: the verdict function is non-blocking on the network lock. **Unknown stays non-runnable.**

What B1 (#434) already provides:
- the ten sections, their states and `not_needed`;
- `run_ready`, `run_blocker` and `notes`;
- `run_request` and `verdicts` per kind (`optimisation`, `study`);
- `blocking` and `source` per item, which key the per-kind blockers;
- `target` `{section, panel, component, entity, field}`;
- `deferred` / `deferred_reason`;
- the ack store in the bundle;
- `get_project_flow`;
- `flow_section:` on the harness workflows.

| Kind / study | Entry point | Starting classification (to prove) |
|---|---|---|
| `optimisation` | `/simulation/run` | supported (B1) |
| `study` (EH reliability) | `start_eh_study` / `check_eh_study_start` | supported (B1); mandatory pack and ENS stages are part of it |
| `loadflow` | `run_ac_pf`; Solver Settings Stage 2 | bundled: needs a solved dispatch, mutates flow and voltage results; verdict needs a lock-free precondition |
| N-1 SCLOPF | option of `optimisation` | supported as an option (no new kind) |
| `grid_readiness` | #435 scorecard | likely supported (read-only rows); confirm with #435 |
| campus electrical | `routers/campus_electrical.py` | unknown: may run least-cost first |
| `control_checks` (island study) | `POST /island/study` | unknown |
| contingencies beyond N-1, connection capacity | `routers/gridspine.py` | unknown |
| frequency and time domain | Planning → dynamics project; external read-back | unknown; screening ≠ time-domain |
| detailed process | `services/solver/process_uc.py` | unknown, after GTP |

---

## 12. Owner choices (sent to the coordinator, 2026-10-20)

| | Choice | Recommended | Status |
|---|---|---|---|
| Q1 | Hand-off: a fourth group (always on, owns section 10's deliverables) / the end of each group | fourth group | pending owner |
| Q2 | #436's nine choices: groups + run-kind options, the rest "not yet available" / all nine with disabled ones / groups only | groups + options | pending owner |
| Q3 | Planning period: native horizons, other years in section 1 / free years at entry | native horizons | pending owner |
| Q4 | Site pin places unplaced buses around it / pin only | places them | pending owner |
| Q5 | Converters in Guided: one preset per converter-coupled asset / one site-wide preset | per asset | pending owner |

---

## 13. Slice plan (replaces the queued B2–B6)

B1 (#434) stays as is. The queued B2–B6 are re-cut as **W-slices**; the old ticket each replaces is named. Every UI slice needs the UX OK on its head (design §6) and lands with the flow working end to end on the templates in design §2.3.

| Slice | What | Replaces | Gate | After |
|---|---|---|---|---|
| **W0 Separability matrix** | §11.1 matrix with native tests; lock-free verdict functions for each kind it marks supported | new (#436 slice 0) | gatekeeper | B1 |
| **W1 Visual system** | tokens (Manrope + OFL self-hosted, graphite/navy dark, cool light, red accents), wordmark, `brand.theme.test.ts`; no layout change | new | UX | #437 and #441 merged (they touch `index.css`) |
| **W2 Home and Builder** | §3: Home (Resume, examples with artwork, build your own, my projects), the Builder (name, pin, period, groups) → open in the flow at the first incomplete section; legacy templates under New project | B4 | UX | B1 |
| **W3a Flow shell and switch** | Guided rail = ten sections with `/flow` states and group chips; Expert sidebar regrouped (§6.1–6.2, navigation only); Issues by section; the Run's disabled reason; `activeSection` and the section-anchored switch (§7); the sidebar Assistant button removed | B2 (shell part) | UX | B1 |
| **W3b Moves** | design §5's B2 move list, as the UX reviewer confirmed: investment inputs out of Results; discount rate in 7; grid-code profile, connection PF, PCC → 3; energization table, connection ramp MW/min, oscillation band → 3; stress editor, island requirements, campus study settings → 8 | B2 (moves part) | UX | W3a |
| **W3c Guided cards at depth** | §4: each card's decisions, Required inputs (same editors) and Advanced; VOLL editable (J1) | B2 (Guided part) | UX | W3a; per-section, can split |
| **W4 Scope** | `flow_scope.json`; the section-8 scope component (mounted by the Builder too); `run_request` kinds and `in_scope` items (§5.2) | new | gatekeeper told (flow service); UX | W0 for each new kind; W3a |
| **W5 Asset form, picker and converters** | B3 as designed (General → Sizing incl. Capacity Bounds → Controls → Reliability → Commercial → Availability), tiered picker; §10's Controls block wired to the preset route and the GFM settings | B3 | UX; gatekeeper on the preset write path | W3a |
| **W6 Location and canvases** | §8: the section-1 pin (geocode, map), "place unplaced buses around the pin", switcher labels, Guided thumbnails | new | UX | W3a |
| **W7 Scenario rows** | B5 in section 8 | B5 | UX | W3b |
| **W8 Carriers & processes card** | B6 | B6 | UX | GTP-02, W3a |
| **W9 Companion** | #437's modes on top of #441; "Help with this section"; transient-card rule (§9, with LC-04) | new | UX | #441, #437 |
| **W10 Results by group** | §6.3 tabs; Hand-off tab (reports, exports, the specialist packet) | new | UX | W3a, W4 |

Order: W0 ∥ W1 ∥ W2 ∥ W3a → (W3b ∥ W3c ∥ W5 ∥ W6) → W4 → W7 → W10; W8 after GTP-02; W9 once #441 and #437 land. B2's existing condition stands: shell work waits until LC-04 leaves the shell/dock area. Sonnet sessions for the UI slices; Opus for W0 and W4 (contracts others read).

### 13.1 In-flight PRs and how they fold in

| PR | Fold-in |
|---|---|
| #434 B1 flow backbone | the backbone; W4 extends `run_request` additively (agreed with its session) |
| #440 A2 Load shape | 10 › Capacity › Operation, after Energy flow; unchanged |
| #441 LC-04 launcher; LC-04 PR-1 edit card | W9 builds on #441's anchor and clip rule; the edit card names sections; transient-card rule owned by LC-04 |
| #437 companion modes (draft) | it is W9's runtime; it touches `CompanionLauncher.tsx`, which LC-04 owns, so it rebases on #441 and pings LC-04 on DOM or size changes |
| #435 / #442 DC-02 scorecard | inputs stay in 3; verdicts become 9's Grid view; #442's Expert Issues placement folds into 9, and its Guided Site-card rows into the section-3 card in W3a |
| #418 ammonia | the Home card becomes creatable when it merges; its pre-fill is design §2.3's ammonia column |
| #436 | this branch carries its files; this document supersedes its spec where §11 says so. Its HANDOFF slice list is replaced by §13 |

## 14. Pointers

- #436 files: `.scratch/workspace-redesign/` (`GridSpine-modern.html`, `spec.md`, `HANDOFF.md`, `backend-separability-assessment.md`, `previews/`, `media/`, `reference-assets/`).
- The approved flow: [design.md](design.md); B1 brief: [issues/B1-flow-backbone.md](issues/B1-flow-backbone.md).
