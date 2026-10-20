# Project workflow: design

Status: needs-triage (draft for the owner's approval)
Owner input: 2026-10-20 (goal below; "generic workflow, templates are instantiations", relayed by the coordinator the same day)
Author: UX reviewer session (the coherence counterpart of the engine gatekeeper)
Base: master `8119972c`, with the specs from #419 (`claude/dc-grid-readiness-spec`) and #420 (`claude/site-commercial-ux-spec`)

## Goal, in the owner's words

> "Make sure that we are able to create a coherent UX UI flow, and means that every part that is closely connected should be integrated into the same group. When I parameterize a site, I'm also going to parameterize the connections… The user does not implement something, then jumps to another section completely unrelated, and then jump back… Create a flow: these are the tasks, this is the goal we want to achieve from the beginning to the end. This is the project we have as a greenfield. This is the end goal… when the parameterization is done, where we can run the simulation. And everything in between should be designed as workflow, where we complete a section and it's marked as complete."

> "Let's not limit ourselves only to data center. It should be a generic workflow and user experience… The templates are examples that we provide of that generic workflow, that they are the instantiation of the workflow. But the workflow itself should be generic for all the different activities… different loads or you integrate different carriers. Also, if you have processes that you need to model, then you need to take that into consideration."

## Words used here

- **Project flow**: the one ordered list of flow sections that takes a project from greenfield to "ready to run", then to results and reports. It is a new glossary entry for `pypsa-gui/CONTEXT.md`. Avoid "workflow" (unqualified) for it: in `CONTEXT.md` a **Workflow** is an assistant flow in `harness/workflows/`. The assistant's workflows *follow* the project flow (§3.6); they don't replace it.
- **Flow section**: one group of the project flow, such as "Connection & grid". Avoid "step" (the hub-design rail and the Model Horizon page already use it for their own sub-steps) and "stage" (EH pipeline stages, growth stages).
- **Section state**: `complete`, `warning`, `incomplete` or `not_needed` (§3).
- **Instantiation**: what a template does to the project flow: it pre-fills sections and may mark one `not_needed`. It never adds, removes or reorders a section (§2.3).

---

## 1. Audit: today's flow

<!-- AUDIT -->

---

## 2. The project flow

### 2.1 The sections

One sequence for every project and every template. Each row names what the section holds, the **grouping rule** that decides what belongs there, and what it hands to later sections.

| # | Flow section | Holds | Grouping rule | Hands on |
|---|---|---|---|---|
| 1 | **Project & site** | name, site type, location (map pin → lat/lon, country, timezone), the model horizon (years, growth stages, investment periods, resolution, representative weeks or chronological year) | *Where and when.* Anything that frames every later section and is set once per project. | coordinates (renewable profiles, geocoded market bundles), timezone (snapshot basis), the period list (every per-period value below) |
| 2 | **Carriers & processes** | the carriers the site handles (electricity, heat, H2, NH3, other commodities, each with its unit), and the process chain: which process units turn which inputs into which product, with their recipe (inputs per unit of product) | *What flows.* A carrier or a recipe is declared once, here, and every later section offers it as a choice. | the carrier list (connections, loads, assets, prices and results all filter by it), recipes (asset forms pre-fill efficiencies from them) |
| 3 | **Connection & grid** | each boundary where a carrier crosses the site fence: for electricity, the import/export limit, ramp-rate limit, loss factors, energization schedule (MW per period), fault level / SCR, the grid-code profile (generic assumed, EU RfG/DCC, or an uploaded TSO PDF); for other carriers, pipeline or delivery limits | *Everything the network operator or supplier sets at the fence.* If the operator would put it in a connection offer, it goes here. | the connection's limits (the LP, the readiness check), the grid-code limits (the grid-readiness scorecard) |
| 4 | **Loads & demand** | every demand by carrier: electrical loads and their profile model (flat, uploaded, library shape, the GPU / AI-training model, PUE and its temperature slope), heat and H2 demand, product deliveries (t/d of NH3), criticality (critical share, tier), flexibility (curtailable, deferrable) | *What the site must serve, and how much it can give up.* A demand and its shape, criticality and flexibility are edited together on one row. | peaks per period (the connection check in §3), criticality (backup sizing, the reliability goal), flexibility (dispatch, the flexibility market) |
| 5 | **Assets & sizing** | generation, storage, conversion and process units: existing vs candidate, sizing (manual or auto-sized: `p_nom_extendable`, `p_nom_min`, `p_nom_max`), unit commitment (min load, min up/down, ramp, restart), backup mode (genset / BESS / hybrid), control presets (grid following, grid forming support, fast frequency response, voltage support), the power architecture (AC, MV UPS, 800 VDC, SST), outage data | *What is built or could be built, and how it behaves.* An asset's sizing, its controls and its reliability data are one form (B3 pattern), never three panels. | the candidate set (the optimisation), presets (the grid-readiness scorecard), outage data (the reliability study) |
| 6 | **Time series & profiles** | the status of every time-varying input from sections 4 and 5: renewable profiles (generated, uploaded, network-native, missing), load profiles, price series; the bulk action "Generate defaults for all"; Profile Studio (Expert only) | *One place to see that every row has a series, and to fill the gaps in bulk.* The **shape choice** stays on its row in sections 4 and 5 (rule: you choose a load's model where you define the load). This section shows the status and does the bulk work. It never holds a parameter that is not also reachable from its row. | complete series on every row the LP reads |
| 7 | **Commercial & parties** | tariffs, the market bundle per country, contracts (PPA, swap, demand response, offsite PPA), finance inputs (discount rate, money year, price basis, overnight costs, lifetimes), parties and who owns or earns what (the party board over `ValueFlowConfig`), reserve-market products and their prices | *Money: who pays whom, for what, at what price.* Every price and every money split is here. No price is edited in Results. | the investment case, the bill, the ledger |
| 8 | **Scenarios & optimisation** | what the run should answer: the objective, foresight, solver, VOLL and CO₂ price, the reliability goal (LOLE / ENS cap), stress scenarios, variants and sweeps (multi-value chips from the asset forms), rolling operation | *The question asked of the model.* Settings that change the answer but not the site. | the run request |
| 9 | **Readiness → run** | the preflight summary of sections 1–8, the grid-readiness scorecard, the EH readiness estimate, Run | *Nothing to edit.* Each line here links to the field in its own section. | a solve, a study |
| 10 | **Results → reports** | results groups (Summary, Investment, Grid plan, Reliability, Operation), post-solve studies (campus electrical, planning → dynamics, decision study), reports | *What the run found.* Read-only for model inputs: an input shown here links back to its section. | report files |

Why this order:
- **Carriers & processes comes second**, before the connection, because the connection, the loads and the assets all choose a carrier. Declaring H2 or NH3 first means no form has to say "go and add a carrier, then come back".
- **The horizon sits in section 1**, not in section 6, because the energization schedule (3), growth-stage peaks (4) and per-period candidates (5) are all per period. If the period list arrived in section 6, sections 3–5 would be filled against a list that is not there yet.
- **The connection comes before loads**: the owner's example ("when I parameterize a site, I'm also going to parameterize the connections"). The fence is set by the operator's offer; the load is then checked against it. The check runs both ways: section 3 shows the stage peaks from section 4 next to the energized MW, and section 4 shows the energized MW next to each stage peak. Neither needs a jump.
- **Commercial comes after the physical sections**, because a tariff or a party split refers to connections, loads and assets that must exist first. A party's tag *on an asset* is set in the asset form (5) and shown on the board (7). It is one value, with two views.

### 2.2 Cross-section links without a jump

The rule: **a value is edited in one section and shown read-only wherever it matters, with a link to its home.** A link may open the home section in place (a drawer or an inline expand) and return to the same scroll position. It never sends the user to an unrelated panel to *finish* the current section.

| Value | Home | Shown read-only in |
|---|---|---|
| A carrier | 2 | the carrier pickers in 3, 4, 5 and 7; results filters |
| A process unit's recipe | 2 | the asset form (5): its efficiencies are computed from the recipe, as in GTP-07 |
| Energized MW per period | 3 | each growth-stage row in 4 |
| Stage peaks | 4 | the energization table in 3 |
| A party tag on an asset or contract | 5 / 7 (contract) | the party board in 7 |
| Discount rate | 7 | Solver settings in 8 (today it is editable in both; see §1) |
| The reliability goal | 8 | the Reliability results header |
| Grid-code limits | 3 | the grid-readiness scorecard in 9, the compliance study in 10 |

**A new carrier flows through without a jump.** When the user adds `nh3` in section 2:
- section 3 offers an NH3 delivery boundary (or marks it "no boundary");
- section 4 offers an NH3 demand (deliveries in t/d);
- section 5 offers process units producing NH3 and NH3 storage;
- section 7 offers an NH3 price (a reference purchase or a sales contract);
- results filter and label by carrier, in the carrier's own unit.

Results never add units across carriers. An energy Sankey stops at a process unit's energy input (A1 follow-up, §4.2).

### 2.3 Templates instantiate the flow

A template pre-fills sections and may set a section to `not_needed`. It never adds, removes or reorders sections. Its pre-filled values carry `source: template` (and `assumed` where the template says so), so the completion model can tell "set by the user" from "accepted default" (§3.2).

The templates below: data centre (`eh_datacenter`, `eh_datacenter_staged`), energy hub / generic site (`eh_site`: industrial, commercial, hospital, campus, other), microgrid (`eh_microgrid*`), H2 hub (`eh_h2_hub*`), ammonia (`eh_ammonia_staged`, #418). Key: **P** = pre-filled, **R** = required input from the user, **–** = not needed, **opt** = optional.

| Section | Data centre | Energy hub / generic site | Microgrid | H2 hub | Ammonia (process) |
|---|---|---|---|---|---|
| 1 Project & site | P (horizon: staged COD phases); **R** location | P from site-type preset; **R** location | P; **R** location | P; **R** location | P (staged); **R** location |
| 2 Carriers & processes | P: electricity only | P: electricity (heat opt) | P: electricity | P: electricity, H2; electrolyser recipe | P: electricity, H2, NH3; Haber-Bosch recipe (kWh/t, H2 t/t) |
| 3 Connection & grid | P limit, energization per phase; **R** confirm limits and ramp; grid code opt (generic assumed) | P from preset; **R** connection MW | – grid connection if islanded (P "no fence"); else P | P | P; NH3 delivery boundary P |
| 4 Loads & demand | P IT load, PUE, critical share; GPU/AI profile opt (DC-01) | P per stage from preset | P | P (H2 offtake) | P: `nh3_delivery` 50/100/150 t/d; criticality tiers (GTP-08) |
| 5 Assets & sizing | P gensets/UPS/BESS, candidates; backup mode opt (DC-03); presets opt (DC-04); architecture opt (DC-05) | P existing + candidates from preset | P | P electrolysers, H2 storage | P committable Haber-Bosch, NH3 tank, H2 storage |
| 6 Time series & profiles | P (representative week); renewable profiles opt | P | P; renewables **R** if PV/wind added | P | P; chronological year opt (GTP-09) |
| 7 Commercial & parties | P tariff; parties opt (DC party template, C8); flex market opt (DC-06) | P tariff, finance | – tariff if islanded; else P | P | P: grey-NH3 reference purchase (GTP-06) |
| 8 Scenarios & optimisation | P; **R** reliability goal (LOLE) | P | P | P (no goal set: the run asks, see the H2 headline rule) | P |
| 9 Readiness → run | computed | computed | computed | computed | computed |
| 10 Results → reports | all groups | all groups | all; Grid plan – if islanded | all | all + Production tiles (GTP-07) |

**DC-specific tickets made generic** (owner decision, coordinator item 5):

| Ticket | Generic form | Stays DC-only, and why |
|---|---|---|
| DC-01 GPU / AI load profile | one **load profile model** among many in section 4 (flat, uploaded, library shape, AI-training). Its dynamic envelope (max ΔP/Δt, swing, oscillation band) is a field any load model may fill. | The AI-training parameters themselves (job-synchronised ramps, checkpoint dips): they describe GPU clusters. The *envelope* is generic. |
| DC-02 grid-readiness check | the scorecard runs for every template (spec D2 already says so); it shows in section 3 (inputs) and section 9 (verdicts) | none |
| DC-03 battery backup | the backup mode lives on any load with a critical share (hospital, process-critical tiers) | none; the rule is "critical load", not "data centre" |
| DC-04 control presets | any converter-coupled asset (BESS, UPS, PV inverter, electrolyser rectifier) | none |
| DC-05 power architecture | — | **DC-only**: AC / MV UPS / 800 VDC / SST is an IT-hall distribution choice. It is a section-5 field shown only when the site type is data centre. |
| DC-06 flex-market revenue | any storage or flexible load with headroom | none |
| DC-07 rolling operations | any project (section 8 "rolling operation") | none |

---

## 3. Completion model

### 3.1 One source: the flow endpoint

A new read-only route, `GET /api/projects/{name}/flow`, returns one row per flow section:

```
{ section: "connection", state: "complete|warning|incomplete|not_needed",
  required: true, items: [ { code, severity: "error|warning|info", message,
  target: { section, panel, entity, field } , source: "validation|eh_readiness|profiles|commercial|grid_readiness|library" } ],
  summary: "40 MW, energized in 3 steps" }
```

It invents no checks of its own. It **collects and files** the ones that exist:

| Source | What it gives | Filed under |
|---|---|---|
| `validation_service.validate_for_run` (what `/simulation/preflight` and `/run` already use) | `Issue(severity, code, component_class, name, message)` | by a **code → section table** (below) |
| `services/adequacy/eh_readiness.eh_readiness` | `import` (links, applied), `critical_buses`, `scr`, `outage_units.missing`, `mc_boundary`, `warnings`, stage predictions | 3 (import, SCR, energization firm MW), 4 (critical loads), 5 (outage data), 8 (VOLL, goal, stage skips) |
| renewable-profile readiness feed (RP-02, `GET …/profiles/readiness`) | per generator: coordinates, profile `none/uploaded/generated/network_native`, `stale_reason` | 6 (and 1 when coordinates are missing) |
| project bind issues (`routers/projects.py`: `commercial_config_invalid`, `finance_inputs_invalid`, library pin issues, tariff reference load) | `{field, code, message}` | 7 |
| grid readiness (DC-02, when it lands) | rows with `verdict: pass/warn/fail/unknown, assumed` | 3 (per input), 9 (the scorecard) |
| placement check (layout findings) | advisory | 1 (info only, never blocks) |

**The code → section table** lives beside the validator (`services/flow_sections.py`), keyed by issue code, with a fallback on `component_class` (Bus/Line/Transformer → 3 or 5 by role; Load → 4; Generator/StorageUnit/Store/Link → 5; snapshots and weightings → 1; solver modes, `sclopf_*`, `rolling_*`, `myopic_*`, `voll_*`, `co2_*` → 8; `carrier_*` → 2; `discount_rate_*` → 7). A unit test fails when a code the validator can emit has no row, so a new check can never fall into "Other" unnoticed.

`/run` keeps re-running `validate_for_run` itself (one source of truth, as today). The flow endpoint is a view over it, never a second gate.

### 3.2 States

| State | When | Shown as |
|---|---|---|
| `incomplete` | a required input is missing, or any item in the section has `severity: error` | an open circle and "n to do"; the first item says what and links to the field |
| `warning` | nothing is missing and there is no error, but a warning stands, or a value is still a template default the template marked `assumed` and the user has not confirmed it | an amber dot and "n to check" |
| `complete` | required inputs present, no error, no unacknowledged warning | a tick |
| `not_needed` | the template (or the user, e.g. "islanded: no grid") marked the section not needed | a dash, collapsed, reopenable |

Rules:
- **Errors make a section incomplete. Warnings never do.** A warning can be acknowledged ("I've checked this"). The acknowledgement is stored per project, with the issue code, entity and the value it was acknowledged at. A changed value re-raises it.
- **"Unknown" is never "complete"** (ADR-0001, and DC-02's rule "a missing input gives unknown, never pass"). An unknown grid-readiness verdict is a warning, not a tick.
- **Sections 9 and 10 have no state of their own.** Section 9 summarises 1–8. Section 10 shows the run's freshness: "not run", "running", "fresh", "stale" (the hub-design `stale` rule).
- **Required sections**: 1–5 and 8 always; 6 when any row has a time-varying input; 7 when the run requests an investment case, bill or ledger. The template table (§2.3) can turn a section `not_needed`, never `required`→optional by itself.

### 3.3 Run unlocks only when the required sections are complete

Run is enabled when every required section is `complete` or `warning`. It is disabled when any is `incomplete`. The disabled Run says which, in words: "Connection & grid: the connection limit is not set." Its one action opens that field.

This replaces the two Run gates today: the hub-design Goal card's own `hub-goal-blocked`, and the Expert header Run that only finds out at `/run`. The server still refuses a run with errors, so a stale client can't bypass it.

### 3.4 Where the state is shown

| Place | Guided | Expert |
|---|---|---|
| The flow rail | the hub-design rail grows from 5 cards to the 10 sections; each shows its state and a one-line summary. The rail is the Guided home | the sidebar's MODEL / RUN / RESULTS / STUDIES / PROJECT steps are regrouped into the same 10 sections, each row with the same state dot |
| Inside a section | a card with ≤3 decisions and the defaults; "to do" and "to check" items at the top | the full tables and forms; the same items at the top |
| Issues panel | hidden (as today); items show on their section card | grouped by section, in flow order; each item links to its field |
| Run button | disabled with the reason (§3.3) | the same |
| Assistant | `get_project_flow` (read) returns the same payload; the harness workflows' `done_when` reads the section state (§3.6) | the same |

### 3.5 Guided and Expert: same sections, different depth

- **Same sections, same order, same states.** The state comes from the server, not from the mode. A section complete in Expert is complete in Guided.
- **Depth.** Guided shows each section as a card with ≤3 decisions, the template's defaults, plain words and the assistant's Delegate button. Expert shows every field. A field Guided doesn't show still counts: a Guided user sees "1 to do — ask the assistant, or switch to Expert to edit" with the field named. Guided never hides a blocker.
- **Profile Studio** stays Expert-only (RP-Q4): Guided reaches the same outcome through "Generate defaults for all" and the assistant.
- **Results in Guided**: Summary, Reliability, Operation (as today, plus A1 Energy flow).

### 3.6 The assistant follows the same flow

- `harness/workflows/hub-design.md` (Guided) and `build-network.md` (Expert) today state `done_when` in prose. They gain a `flow_section:` key per step, so "done" means the section state, not the model's reading of the transcript.
- `hub-design` grows to the 10 sections and becomes the one generic Guided workflow. Its name may stay for the stored session state, or move to `project-flow` (§8, choice 4).
- LC-05's ordered workflows (change → verify → run → explain) use the flow endpoint as their verify step for model inputs.

---

## 4. Placement of queued tickets

Each ticket gets one home section. The coherence note says what the ticket must do so that it does not create a jump.

### 4.1 Data-centre grid readiness (`.scratch/datacenter-grid-readiness/`, spec #419)

| Ticket | Section | Coherence note |
|---|---|---|
| DC-01 GPU / AI load profile | 4 Loads | A **load profile model** on the load row, chosen from a list that also holds flat / uploaded / library shapes. The dynamic envelope shows on the load row and, read-only, in section 3 next to the ramp and oscillation limits it is checked against. The roadmap knob (IT MW, rack density per COD phase) is per period, so it reads the period list from section 1. |
| DC-02 grid-readiness check | 3 inputs, 9 verdicts | Each scorecard row carries `section` and `target`, so the flow endpoint files it. Row `unknown` → a section-3 warning naming the missing input. The report chapter is in 10. The scorecard runs for every template. |
| DC-03 battery backup | 5 Assets | Backup mode (`genset/bess/hybrid`) on the backup-asset form. It reads the critical share from section 4 (shown read-only) and the redundancy level. The cost delta shows in 10 (Investment), not in the form. |
| DC-04 control presets | 5 Assets | A **Controls** block in the asset form of every converter-coupled asset. It is not edited in the island panel (Studies › Campus electrical) as today: that panel shows the preset read-only, with a link back. |
| DC-05 power architecture | 5 Assets | A site-level field shown only for the data-centre site type. Its loss chain shows, read-only, on the PUE line of the load (4). The compare view is a section-8 variant set, its results in 10. |
| DC-06 flex-market revenue | 7 Commercial (products, prices), 5 (headroom on the asset) | Reserve products and their price series in 7. The committed headroom on the BESS/UPS form in 5, with the "critical-backup energy untouchable" floor shown from 4's critical share. Revenue in 10. |
| DC-07 rolling operations | 8 Scenarios & optimisation | A run mode, not a page. Its plan-vs-operation gap is a results tab in 10 › Operation. |

### 4.2 Site commercial UX (`.scratch/site-commercial-ux/`, spec #420)

| Ticket | Section | Coherence note |
|---|---|---|
| A1 Energy-flow Sankey (#423) | 10 › Operation | Placement checked (comment-only) on 966910ab: fine. Follow-ups taken in 82d33c08: mixed-unit Links return "not available", and the money ↔ energy flows views link to each other. **Queued:** option (b), stop the Sankey at a "Process <name>" node so an NH3 site still shows its electricity side. |
| A2 Load Shape Analysis | 10 › Operation, after Energy flow | Same "Not run yet" / "Not available" states and words, and the same window slider as A1. |
| B1–B4 | re-cut in §5 | |
| C1 Library page | outside the flow (workspace) + inline pickers in 5 and 7 | The library is **managed** on its page. During the flow it is **picked from** inside the asset form (5) and the tariff/contract editors (7), in a drawer that returns to the form. No "go to the Library, then come back". |
| C2 Market bundles per country | 7 | The country comes from the section-1 pin (geocoded). The bundle is picked in 7; its price series show in 6's status list. |
| C3 Contract types | 7 | One contract editor. A physical PPA names its asset (from 5) and its connection (from 3) through pickers, never typed names. |
| C4 Report variant selection | 10 | A report option, per participant. |
| C5 Party board | 7 | The only editor of `ValueFlowConfig` (spec). Party tags set in an asset form (5) are the same `asset_owners` value, so the board and the form never disagree. |
| C6 Party groups | 5 (tag on asset), 7 (tag on contract, splits) | Tag where the thing is defined. The board shows the result. |
| C7 Per-party "my view" | 10 › Investment | Results; per party; links back to the board (7) for the split. |
| C8 Party templates by sector | 7, and the §2.3 template table | A template's party set is part of its instantiation of section 7. |
| C9 Offsite PPA cards | 7 | On the board. Financial/virtual PPAs have no physical flow: they never appear in the energy Sankey (rule: physical and money flows stay separate). |

### 4.3 Profiles (`.scratch/renewable-profiles/`)

| Ticket | Section | Coherence note |
|---|---|---|
| RP-01 coordinates refusal | 6 (item), 1 (fix) | A missing-coordinates refusal becomes a section-6 "to do" item whose link opens the bus location. That's a field of section 1 (site pin) or 3/5 (bus placement) and is edited inline in a drawer. |
| RP-02 readiness feed | 6 | A flow-endpoint source (§3.1). |
| RP-03 horizon from snapshots | 1 → 6 | The weather year defaults from the section-1 horizon. Section 6 shows "from your snapshots" or "default". |
| RP-04 Generate inside the Time Series Manager | 6 | The Renewables tab *is* section 6's Expert depth. The per-row badge also shows on the generator's asset form (5) and links to its row in 6. **"Generate defaults for all"** is section 6's bulk action, in Guided and Expert. |
| RP-05 tuning panel | 6 (Expert depth) | Collapsed by default; Guided never shows it. |
| RP-06 shaping, RP-07 compare | 6 (Expert depth) | Inside the Renewables tab / Profile Studio; no new destination. |
| RP-08 chat default-first | 6 via the assistant | The workflow's steps gain `flow_section: profiles` (§3.6). |
| RP-09 batch apply | 6 | One undo step for "Generate defaults for all". |
| RP-10 Guided offer | 6 | The offer reads the same section-6 items. A declined offer leaves the item standing, so the section stays `incomplete` or `warning`. |
| RP-11 QA | — | Its Playwright journeys walk sections 5 → 6 → 9. |
| RP-12 representative weeks | 1 (time base), 6 | |
| Profile Studio (Expert-only, RP-Q4) | 6 (Expert depth) | Linked from the Renewables tab; leaves the sidebar, as decided. |

### 4.4 Grid-to-process (`.scratch/grid-to-process/`)

| Ticket | Section | Coherence note |
|---|---|---|
| GTP-05 ammonia template (#418) | §2.3 template table | Pre-fills 2 (NH3, Haber-Bosch recipe), 4 (`nh3_delivery`), 5 (committable HB, tanks), 7 (buy/lost prices). |
| GTP-06 grey-NH3 investment case | 7 (reference purchase), 10 (case) | The reference purchase is a commercial input. |
| GTP-07 process-unit form, Guided process view | 2 (recipe), 5 (unit sizing and UC), 10 (Production tiles) | **Split** the form: the recipe (inputs per t, kWh/t) is declared once in section 2. The section-5 form picks a recipe and sizes the unit, and its efficiencies are computed from the recipe, read-only. That keeps "what makes NH3" in one place when several units share it. |
| GTP-08 criticality tiers, lost production | 4 (tier on the demand), 8 (lost-production value beside VOLL) | The lost value is a shortfall valuation, like VOLL, so its home is 8. It shows read-only beside the product's price in 7. |
| GTP-09 chronological year | 1 (time base `week/year`) | The validator's refusal of a non-continuous inventory under `week` files to section 1, naming the store (5). |

### 4.5 Live copilot (`.scratch/live-copilot/`)

| Ticket | Section | Coherence note |
|---|---|---|
| LC-04 parameter edits | every section | Each chat edit's old→new card names the flow section and field. Edits resolve to the same `target {section, entity, field}` the flow items use, and the flow state refreshes after a verified write. |
| LC-05 ordered workflows | 9 (verify), every section | The verify step reads the flow endpoint. A workflow step's "done" is a section state (§3.6). |

---

## 5. Package B re-cut: the build plan

The spec's package B (B1 scenario rows, B2 tiered picker, B3 asset form, B4 seven-step wizard) builds parts of the flow, but not the flow. Re-cut so that the backbone comes first and every later piece plugs into it:

| New | What | Replaces | Files (expected) | Gate |
|---|---|---|---|---|
| **B1 Flow backbone** | `services/flow_sections.py` (the code → section table and its completeness test), `GET /api/projects/{name}/flow`, the acknowledgement store, the assistant read tool `get_project_flow`, `flow_section:` on harness workflow steps | new | backend `services/`, `routers/projects.py`, `harness/workflows/*.md`, `harness/catalogue.py` (coordinator's go) | not gated (read-only over existing checks); the gatekeeper is told, since it reads `eh_readiness` |
| **B2 Flow shell** | the Guided rail grows to the 10 sections (`pages/hubDesign/`: `flow.ts` states become content-based); the Expert sidebar regrouped into the 10 sections with state dots (`layout/sidebar/steps.tsx`); the Issues panel grouped by section; the Run button's disabled reason. **Moves:** tariff / contracts / finance / participants editors out of Results › Investment into section 7, leaving Results read-only with links; the discount-rate editor out of Solver settings (read-only there); the grid-code profile choice out of Studies › Campus electrical into section 3; island controls out of the island panel into section 5 | spec B4's "Review" step, part of B1 | frontend `layout/`, `pages/hubDesign/`, `pages/results/investment/`, `pages/economics/` | UX gate |
| **B3 Asset form and picker** | spec B2 + B3 merged: the tiered picker (Standard / Advanced), and one asset form: General → Sizing (manual / auto-sized, min/max) → Controls (DC-04 presets) → Reliability (outage data) → Commercial (party tag, link to 7) → Availability (profile badge, link to 6). Multi-value chips create section-8 variants | spec B2, B3 | `layout/CreationForm.tsx`, `layout/properties/` | UX gate |
| **B4 New project** | Details → Location (map pin → lat/lon, country, timezone) → Template, then the project opens **in the flow** at the first incomplete section. The spec's Load, Connection, Tariffs and Review steps are sections 4, 3, 7 and 9; the wizard no longer duplicates them. | spec B4 | `layout/NewProjectWizard.tsx`, `pages/hubDesign/cards/StartCard.tsx` | UX gate |
| **B5 Scenario rows** | spec B1, placed in section 8: each scenario row shows which sections it overrides (Physical = 2–6, Commercial = 7, Optimisation = 8) | spec B1 | `pages/Scenarios*` | UX gate |
| **B6 Carriers & processes card** | the section-2 card: the carrier list with units, and the recipe list. It reads GTP-02's schema; editing recipes lands with GTP-07 | new | `pages/hubDesign/cards/`, sidebar row | UX gate; after GTP-02 |

Order: B1 → B2 → (B3 ∥ B4) → B5; B6 after GTP-02 and B2. B2 waits for LC-04 to leave the shell/dock area (the spec's existing condition). Sonnet sessions for B2–B6; B1 is an Opus session (a backend contract many tickets read).

Each B ticket lands with the flow working end to end on the five templates in §2.3: the state of every section is right before and after, and Run unlocks at the right moment.

---

## 6. Review checklist for every UI PR

I apply this to the exact head SHA of every PR that changes UI, and reply "OK #<pr> <sha>" or name the blocking findings. Until the owner approves this design, findings are comments only.

1. **Placement.** Every new or moved field has one home section (§2.1) and the PR says which. Its grouping matches that section's rule.
2. **No duplicate editors.** The value is editable in exactly one place. Elsewhere it is read-only with a link home (§2.2).
3. **No jump away and back.** Finishing the section never needs another section's panel. A cross-section value opens inline (drawer or expand) and returns to the same place. Results never edit inputs.
4. **Completion wired.** Any new required input or check emits a flow item (`code`, `severity`, `target`) through an existing source. A new validator code has a row in the code → section table (its test passes). The section's state is right in three cases: empty, filled, broken.
5. **Run gate.** A new blocker shows in the disabled Run's reason with a working link to the field.
6. **Unknown is not complete.** A value the server can't establish shows as unavailable and keeps the section from a tick (ADR-0001).
7. **Guided and Expert.** Same section, same state in both modes. Guided shows ≤3 decisions per card and never hides a blocker. An Expert-only control has a Guided route (default, assistant, or "switch to Expert" naming the field).
8. **Generic.** No template-specific flow, section or ordering. Template-only fields are shown by site type or carrier, not by template id. Any carrier works, with its own unit: nothing adds MWh to tonnes.
9. **Words.** Labels use the glossary (`CONTEXT.md`), no stage ids or code names in the UI, "assumed" shown on assumed values.
10. **Assistant parity.** If the PR adds a field or a section item, the assistant can read it (flow endpoint) and the harness workflow step names its section.
11. **Evidence.** Screenshots of the section in Guided and Expert, before and after, on at least one template where the section is pre-filled and one where it is required.

---

## 7. Changes to other queued tickets

Changes I ask for in tickets already queued. I'll send each to its session or to the coordinator when the owner approves.

| Ticket | Change |
|---|---|
| site-commercial spec, package B | replaced by §5 (B1–B6). |
| A1 (#423) | done in 82d33c08. Queue option (b), the process node, as A1b after GTP-07. |
| A2 | same states, words and window control as A1 (agreed with the A1/A2 session). |
| C1 | add "picked inline from 5 and 7" to *Done when*. |
| C5, C6 | party tags in the asset form (B3) write `asset_owners`; the board reads them. One value, two views. |
| DC-01 | "load profile model" as a generic registry on the load. The envelope is a generic load field. AI-training is one entry. |
| DC-02 | rows carry `section` and `target`; the flow endpoint is one consumer; `unknown` → warning. |
| DC-03, DC-04, DC-06 | the fields live in the B3 asset form (Controls, backup mode, headroom), not in the island panel. They are not DC-only (§2.3). |
| DC-05 | DC-only by site type, a section-5 site field. |
| GTP-07 | split the recipe (section 2) from the unit form (section 5). |
| GTP-08 | lost-production value has its home in section 8, beside VOLL. |
| RP-04 | the per-row badge also shows on the generator's asset form (B3), linked to its row in 6. |
| RP-10 | the offer reads the section-6 items. |
| LC-04 | the edit card names the section; edits resolve to flow `target`s. |
| LC-05 | verify reads the flow endpoint. |
| harness workflows | `flow_section:` per step (B1). |
| `CONTEXT.md` | add **Project flow**, **Flow section**, **Section state**, **Instantiation** (B1). |

---

## 8. Open choices for the owner

<!-- CHOICES -->
