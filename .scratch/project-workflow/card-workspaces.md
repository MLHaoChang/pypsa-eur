# Card workspaces: inputs, card assets, results and the assistant, per card

Status: draft for UX reviewer and engine gatekeeper review; owner questions in §9. No UI is built until the owner's go.
Date: 2026-10-21
Author: W10 session (results by card), commissioned through the coordinator
Base: master `87546ef`, with W4a (#496, `services/flow_scope.py`) read from its branch
Amends: [workspace-reconciliation.md](workspace-reconciliation.md) §4 "10 Results → reports", §6.3 and the W10 row of §13; [modular-cards.md](modular-cards.md) §6's W10 line.

## The owner's words

Decision RES-CARDS (2026-10-21, relayed by the coordinator):

> "for the results, I want it to be modularized for different cards that we have, right? Like capacity expansion, load flow, reliability, curtailment, etc. And for each of the card, we should understand design. What are the things that we display for each segment? For capacity expansion, I think we have already built a lot around results. For the others, we have to also design the corresponding tabs and the different result tabs that we have to showcase. It should also be integrated with the existing example cases."

Widened the same evening (about 22:2x CEST, relayed by the coordinator):

> "each card should have obviously the dedicated workspace … for the load flow card … do you think it makes sense that you need additional assets that is outside of capacity expansion? Maybe … statcoms or SVC or … converters or VSCs or whatever that might be … new assets that could be integrated that is not part of the capacity expansion. So maybe we need to add it to the model and then adjust the model enough also from visually … that they integrate these assets with the corresponding parameterization tab and the AI walkthrough if needed for both guided and expert mode. That needs to be designed as well … we need to kind of deep dive."

## 0. In one paragraph

A **card** (W4a's scope unit, `flow_scope.CARDS`) gets a **card workspace**: a filtered path through the ten flow sections that shows only the blocks the card owns, then its share of the one Run (9), then its own results group (10 › *card*), then a visible next step. The ten sections stay the one place each input is edited (design §2); the workspace is a lens, not a second editor. Each card has **card assets**, physical or contractual things that exist for that card's study and are not capacity decisions (a STATCOM for Grid, a converter's control mode for Control, outage data for Reliability, a PPA for Commercial); they are edited in their home section under the card's chip and drawn on the canvas like any asset. Results are regrouped into **six groups, one per card, at most four tabs each**. Every current result view gets exactly one home (§1, §3); Summary is the landing, listing each card in scope with its headline and a link. "Curtailment" is a Capacity tab, not a card. The assistant works per card with existing tools and LC-04 edit cards (§6).

## Words used here

- **Card**, **sub-option**: as in modular-cards §1 and `flow_scope.CARDS`. Card labels in the UI are exactly the registry's: *Capacity expansion*, *AC power flow & grid*, *Control*, *Reliability*, *Commercial & finance*, *Hand-off*.
- **Card workspace**: the card's lens over sections 3–9 plus its results group. Not a new section, not a new page with its own editors.
- **Card asset**: an item a card's study needs that the capacity optimisation does not size (§2.2).
- **Results group**: 10 › *card*. A **tab** sits in exactly one group. Separator in copy: "›".
- **Native view** vs **copilot content**: as integration-sensitivities §5.4. Native views show freshness; only copilot content carries a check state.

---

## 1. Inventory: every result on master today

Sources: `frontend/src/pages/Results.tsx` (`TABS`, 17 ids), `pages/results/groups.ts` (five groups), `backend/services/result_views.py` (`RESULT_VIEWS`, same 17 ids), `harness/catalogue.py:91` (`RESULTS_TAB_ENUM`), `harness/navigation.py:12` (`PANELS`), the panels outside Results, and `services/results/*`. "Run kind" is what produces the numbers. "New home" is this design's group › tab (§3).

### 1.1 Results tabs

| Tab id (label) | Shows | Mode | Run kind | Checked profiles | New home |
|---|---|---|---|---|---|
| `summary` (Summary) | cost, emissions, adequacy (unserved MWh, VoLL cost, shed hours, reserve margin), key capacities; "Next: hand off" (#543) | both | optimisation | generator-dispatch/1, network-linear-cost/1, carrier-energy/1 | **Summary** (landing, §4) |
| `overview` (Overview) | per-period cost, mix, load, curtailment, storage | Expert, multi-period | optimisation | withheld (`unsupported_snapshot_scope`) | Capacity expansion › Build (multi-period view) |
| `capex` (Capacity Expansion) | KPIs, capex by class, sized and retired assets, cost waterfall | Expert | optimisation | generator-cost/1, storage-unit-cost/1, capacity/1 | Capacity expansion › Build |
| `dispatch` (Dispatch) | stack, storage, loads, curtailment and lost-load overlays, duration curve, heatmaps, full-load hours | Expert | optimisation | generator-dispatch/1, storage-unit-dispatch/1, carrier-energy/1 | Capacity expansion › Operation |
| `flows` (Energy flow) | Source → Via → Destination MWh, conservation chip, CSV | both | optimisation (one period) | energy-balance/1 | Capacity expansion › Operation |
| `loadshape` (Load shape) | import/export heatmaps, a week, monthly and annual energy | both | optimisation (one period) | site-import/1 (#477, open) | Capacity expansion › Operation |
| `storage` (Storage cycling) | cycles per unit, carrier roll-up | Expert | optimisation | storage-unit-dispatch/1, storage-cycles/1 | Capacity expansion › Operation |
| `emissions` (Emissions) | CO₂ totals, cap shadow price, by carrier | Expert | optimisation | generator-emissions/1, network-emissions/1 | Capacity expansion › Operation |
| `curtailment` (Curtailment) | total, by carrier, time series, by cause (#515) | Expert | optimisation | curtailment/1 | Capacity expansion › Curtailment |
| `prices` (Prices) | marginal price, duration curve, drivers | Expert | optimisation | bus-price/1 | Capacity expansion › Costs & prices |
| `economics` (Asset economics, #543's rename) | per-asset revenue, cost, profit, LCOE/LCOS at model prices; LCOH | Expert | optimisation | withheld (S8 semantics) | Capacity expansion › Costs & prices (§3.1, decision D1) |
| `loadflow` (Load Flow) | AC PF gate, line/transformer loading, voltages, reactive flows, losses, line duals, branch loading from evidence | Expert | optimisation + AC PF (`run_ac_pf_after_lopf`) | branch-loading/1, network-loading/1; v2 displayed-data ACK | AC power flow & grid › Load flow |
| `lostload` (Lost load) | VoLL slack: unserved MWh and cost, per carrier, per bus | Expert | optimisation (VoLL capture) | lost-load/1 | Capacity expansion › Operation › Lost load (an operation result; Reliability › Adequacy links to it) |
| `adequacy` (Adequacy) | COPT, reserve margin, frontier, Monte Carlo, loops, EH reference design (certification, DTC, levers) | both | EH study, COPT, MC, frontier, loops | copt-screening/1 (#483, open); MC withheld | Reliability › Adequacy and › Outage screening |
| `fmea` (FMEA) | failure modes by EUR/a, expert rows, mitigability, sweep | both | FMEA sweep | withheld | Reliability › Stress & FMEA |
| `investment` (Investment case) | bill, value flows, investment case (NPV, IRR, cash flows, counterfactual), revenue by stream and capture (#515); still hosts input editors until #533 | Expert | investment case; optimisation + tariff for the bill | investment-case/1 (#480), investment-lcoe/1 (#482), open | Commercial & finance › Bill, › Contracts & revenue, › Investment case, › Parties |
| `asset` (Asset Detail) | one asset's every result, XLSX | Expert | optimisation | per-entity profiles | **Asset results drawer** reached from any card (§3.7) |

### 1.2 Results outside the Results panel

| Where | Shows | Run kind | New home |
|---|---|---|---|
| Campus electrical › Results (`CampusElectricalPanel.tsx` ~535; "Grid study results" after #545) | PCC compliance, transformers, reactive compensation, short circuit, critical hours | campus study | AC power flow & grid › Grid code & connection, › Electrical design |
| Campus › Investment (`CampusInvestmentSection.tsx`) | campus investment, cost per period, joint optimisation (MILP) | campus MILP job | AC power flow & grid › Electrical design |
| Campus › Island (`CampusIslandSection.tsx`, `CampusIslandCutsSection.tsx`, `CampusIslandGfmSection.tsx`) | island report, frequency cuts, grid-forming recommendation | island study, cuts job | Control › Island & ride-through, › Grid-forming |
| Grid-readiness scorecard (`GridReadinessScorecard.tsx`, in Issues and the Guided Site card) | verdict rows, with DC 03's after-battery values | advisory read, no run | **One home: 9** (a readiness read, R1–R4). 10 › AC power flow & grid › Grid code & connection does not copy it; it links "Grid-code verdicts: see Readiness › Grid readiness ›" (UX review) |
| Planning → dynamics (`GridspinePanel.tsx`) | connection capacity, connection-point assessment | gridspine planning study | AC power flow & grid › Grid code & connection |
| Planning → dynamics | ranked snapshots, assumptions ledger, per-hour bundles, PowerFactory read-back | gridspine planning study | Hand-off › Specialist packet |
| Reports (`ReportsPanel.tsx`, `ReportViewer.tsx`) | generated reports, versions, evidence hash | report job | Hand-off › Reports |
| Guided hub: `ResultsCard`, chapter tiles (`chapters.ts`), `ImproveCard` | verdict, cost, risks; Investment / Grid plan / Reliability / Operation tiles; findings | EH study, IC, campus | Guided Results = card summaries (§5); Improve stays |
| Compare rail (`Results.tsx:890`, `CompareView.tsx`) | base vs cases per tab | several runs | unchanged: a rail across groups, not a card (it compares the whole run) |
| Run-bound view (`RunBoundView.tsx`) | retained evidence of a discussed past run | run evidence | unchanged overlay over any group |
| Canvas results overlay, site 3D readout | per-snapshot flows and loading | optimisation / AC PF | unchanged (a view of the canvas, not a results tab) |
| Decision study (`pages/decision/*`) | verdict, tornado, report | decision run | its own project kind (audit #21); not a results group |

### 1.3 Duplicates and orphans

Duplicates (each gets one home in §3; the other places link):
- **Load flow** in four places: Results › Load Flow, campus PCC compliance, PowerFactory read-back, the Guided grid tile.
- **Investment case** read by Results › Investment and the Guided Investment tile; **bill and tariff preview** in Results › Investment and Economics (section 7).
- **Asset costs** from three sources: Capacity Expansion (`/simulation/asset_costs`), Asset economics (`/results/asset_economics`), section 7's table (`/network/asset_costs`). Results keep one (Build); section 7's is an input view.
- **Adequacy figures** read by Summary, Lost load, the Guided Results card, the Reliability tile and the EH panel.
- **Reports and Export** after #556: the sidebar "Export…" chooser and section 10's Reports row are the one entry each today; Hand-off's tabs must *be* them, not copies (§3.6).
- **Prices** fetched by both Prices and Load flow.
- **The scorecard** mounted in Issues and the Guided Site card (one component, two mounts; W3a already plans 9 as its home).
- **Reports** opened from three buttons (audit #16).
- Backend: LCOH, emissions, curtailment, lost load, prices and branch loading are computed twice (`services/results/*` and `services/compare/*`; branch loading three times with `harness/branch_calculations.py`). Not a UI problem; listed for the engine slices (§8, E-dup).

Orphans:
- `pages/ResultsViewer.tsx`: no importer.
- `/results/cfe_score`: endpoint and a fetch in `api/commercial.ts`, no UI. Home: Commercial & finance › Contracts & revenue (24/7 matching is a PPA question).
- `/results/process_view` (make / buy / lose per product): endpoint, no UI. Home: Capacity expansion › Operation › Products, when Process detail is in scope.
- The standalone `compare` panel id: kept, never opened (§6.2 already leaves it unmapped).

Inputs still hosted by results (design §2.2 says none):
- Results › Investment: participants, tariff, library, contracts, finance inputs, site connection. **#533 (W3b-1) moves them.**
- Results › FMEA: stress-scenario editor (#535 moves it to 8). Expert rows and mitigability stay on the worksheet: they annotate a result, they are not model inputs (UX ruling O4b). They are stored per project, and no run ever reads them.
- Results › Adequacy: the EH pack settings, Loop and Margin-loop targets, MC draws. Audit #1's **W3b-R** moves the goal to 8; each panel keeps only its Run.
- Solver settings › "Run AC power flow on current solution" (audit #4): becomes an action on AC power flow & grid › Load flow through the gate.

---

## 2. The card workspace

### 2.1 What a workspace is

A card workspace is the card's path through the flow, in flow order:

1. **Inputs**: the blocks the card owns in sections 3–8, each in its home section, under the card's chip (W3a). Nothing is edited outside its home section.
2. **Run**: the card's kinds in section 9's one Run (B1 `run_request`, W4b).
3. **Results**: 10 › *card*.
4. **Next**: the visible next step (UX-flow rule 3): from the last input block to Run, from results to Hand-off or the next card in scope.

It is entered from two places (UX review), which land on the same thing:
- **The card chip**, wherever it appears: on a section row (W3a), and as the card name on section 8's scope cards (W4a's `ScopeCards`), which becomes the chip rather than a separate "Open workspace" link. It filters the rail or sidebar to the card.
- **"Inputs for this card ›"** in the header of Results › *card*.

It is shown, not built (UX ruling O5): the Guided rail and the Expert sidebar already list the ten sections with chips. The workspace is the rail or sidebar **filtered to one card**, plus a header strip. The strip uses the rail's own words (`FLOW_STATE_WORDS`: "complete", "to check", "to do") and its `FlowStateDot` marks. The sketch below writes the dots as words:

```
[ AC power flow & grid ]  Inputs: 3 of 4 complete · Run: ready · Results: from the run of 21 Oct 21:40, out of date · Walk me through this card
  3 Connection & grid   complete   grid-code profile, PCC fault level
  5 Assets & sizing     to check   2 STATCOMs, transformer taps
  8 Scenarios           complete   load flow after the optimisation, N-1
  9 Run                 ready
  10 Results › AC power flow & grid   Load flow · Grid code & connection · Electrical design · N-1 security
```

"Walk me through this card" (§6) lives in this header strip only.

Rules:
- **One entry point per feature** (UX-flow rule 1): a workspace lists links into sections; it holds no editor of its own.
- **The filter is a view state**, not navigation away: clearing it returns to the full list at the same section (`activeSection`, §7 of workspace-reconciliation).
- **Capacity expansion's workspace is the whole flow** (it owns blocks in every section 1–8), so it has no filter; its header strip still shows Run and Results.
- **Hand-off's workspace is results-only**: it owns no input section; its header strip shows deliverables.
- **Guided** shows the same strip on the card's results group and in section 8's scope cards; the Guided rail is not filtered (Guided already shows only in-scope blocks).
- **Out of scope**: the workspace shows the card's `ScopeGate` ("Outside scope. Add <card>"), never an empty list.

### 2.2 Card assets

A **card asset** is placed and edited like any asset (canvas palette → the W5 asset form, home section by `section_for_component`), but:
- its palette entry, form block and canvas symbol carry the card's chip;
- it is **not a capacity decision** (fixed input, or sized by the card's own study, never by the LP);
- out of scope, its palette entry hides; existing ones stay drawn, dimmed, with "Not used: <card> is off" (values kept, modular-cards §2 "not lost");
- an asset the engine cannot represent is **not offered** in the palette; a user who asks is told so in words (§10a.2's rule).

Per-card lists are in §3. The engine mapping column is the gatekeeper's (asked 22:1x CEST; §3.2 marks what awaits their answer).

### 2.3 States, the same words in every group

From `components/ResultState.tsx` (UX overhaul S2), plus two this design adds:

| State | Pill | When | Action |
|---|---|---|---|
| ready | — | a value | — |
| not_run | Not run yet | the card is in scope; its kind has not run | "Run" (section 9 / header, through `requestRunSolve`) |
| running | Running | its job runs | — |
| stale | Out of date | inputs changed since the run (`solve_freshness`, `flow_service._freshness`); the old values stay, dimmed | "Run again" |
| not_modelled | Not modelled | the run didn't model it (losses off, no VOLL) | link to the section that turns it on |
| not_applicable | Not applicable | nothing to apply to (no lines, no renewables) | — |
| unavailable | Not available for this run | the payload can't tell | — |
| study_only | From the study | figure from the EH study, the project not solved | — |
| **outside_scope** (new) | Outside scope | the card or sub-option is off | `ScopeGate`'s "Add <sub-option>" in place. Earlier results are kept and shown below, dimmed and read-only, labelled "From the run of <date>, before <card> was switched off". The greyed card in the group strip shows the same `ScopeGate` text, so there is one wording |
| **failed** (new) | Did not complete | the job failed or the server refused it | the server's reason in words (RB-titles rule) and "Open <section>" for a refusal |
| not offered | — | the site doesn't offer the sub-option | `ScopeGate`'s "Not offered for this site: <reason>"; "Not yet available" when W0 hasn't cleared it |

Every native tab shows **freshness** in its header: "From the run of <date time>" or the stale pill (integration-sensitivities §5.4). Freshness never reads as "checked".

---

## 3. Per card

Each card: **A** workspace, **B** card assets, **C** results tabs (≤ 4), **D** assistant (detail in §6).

### 3.1 Capacity expansion

Sub-options: Asset sizing · Dispatch & economics · Process detail.

**A. Workspace.** All of sections 1–8 (no filter). Run: `optimisation` (+ `study` when Reliability is on). Next from results: Commercial & finance when in scope, else Hand-off.

**B. Card assets.** Every sized asset (generators, storage units, stores, links, lines with `s_nom_extendable`) and process units (Capacity › Process detail; GTP). These are the capacity decisions; nothing here is a "card asset" in §2.2's sense. With sizing off (F1) each asset's Sizing block reads "Not sized in this run" (modular-cards §4).

**C. Results tabs.**

| Tab | Displays | Units | Checked profiles | Notes |
|---|---|---|---|---|
| **Build** | Headline: total new build (MW, MWh), capex/a, system cost. Chart: capex by class; capacity by carrier (existing vs new); multi-period: per-period build and cost (today's Overview). Table: sized and retired assets, "built to its bound" flag | MW, MWh, EUR/a, EUR per period | capacity/1, generator-cost/1, storage-unit-cost/1; Overview withheld | With sizing off: header "Fixed sizes from <source>; not optimised" |
| **Operation** | Sub-views (a segmented control, not tabs): Dispatch · Energy flow · Load shape · Storage cycling · Emissions · **Lost load** (VoLL slack: unserved MWh and cost per carrier and bus; Dispatch's stack already overlays it) · Products (only with Process detail on: make / buy / lose per product from `/results/process_view`, LCOH) | MW per snapshot, MWh, cycles, t CO₂, EUR, t or MWh of product | generator-dispatch/1, storage-unit-dispatch/1, carrier-energy/1, energy-balance/1, storage-cycles/1, generator-emissions/1, network-emissions/1, lost-load/1 | Guided sees Energy flow and Load shape (as today). Lost load is an operation result (UX ruling O4a), so it follows Capacity's scope like the rest |
| **Curtailment** | Headline: curtailed MWh and % of available. Chart: by carrier over time. **By cause per plant** (#515: export at the limit / price < 0 / both, summing to the total). Link "Revenue lost to curtailment ›" to Commercial › Contracts & revenue (cross-link, no copy) | MWh, % | curtailment/1 | **The owner's "curtailment"** lives here (owner word → card, §3.8) |
| **Costs & prices** | Headline: system cost, mean import price. Cost waterfall (objective decomposition). Bus prices: duration curve, drivers. **Asset value at model prices** (today's Asset economics: revenue, cost, profit, LCOE/LCOS per asset). LCOH when electrolysers exist | EUR, EUR/a, EUR/MWh, EUR/kg | bus-price/1; asset economics withheld (S8) | Decision D1 below |

**D1: Asset economics goes to Capacity, not Commercial.** It is the asset's revenue at the LP's own nodal prices, before tariffs, contracts or finance. The checked-results audit (S8) already rules it is "market value at model prices, never compared with investment-case rows". Putting it under Commercial would set it beside the bill and the investment case, the comparison the audit forbids. Its tab label changes to "Asset value at model prices" in a sub-heading; Commercial › Contracts & revenue links to it.

**D. Assistant.** `explain-results` workflow, scoped to this group; reads `get_results(statistics | cost_breakdown | generators | storage | curtailment | prices | emissions)`, `get_energy_flows`, `get_load_shape`; `sizing` explains which constraint binds a size. Edits (a bound, a cost) go through EditCards naming section 5 or 7.

### 3.2 AC power flow & grid

Sub-options: Load flow · Grid code & electrical design · N-1 security (SCLOPF).

**A. Workspace.** Section 3 (connection: PoC, grid-code profile, PCC fault level, energization, ramp, oscillation band; W3b-4 #546), 5 (network elements and the card assets below, Electrical block), 8 (load flow after the optimisation, N-1, campus study settings; W3b-3 #545), 9 (Run, scorecard verdicts). Next from results: Control when in scope, else Hand-off.

**B. Card assets.** Baseline is workspace-reconciliation §10a.2 (swept on `ef0856a`); the gatekeeper is confirming each row against today's master (asked 22:1x CEST) and this table is updated with their answer before review.

| Asset | What it does in the study | Engine today | Sized or fixed | Canvas | Parameter block (W5 form) | Example |
|---|---|---|---|---|---|---|
| Shunt capacitor / reactor bank | fixed or stepped Mvar at a bus: voltage support, PF correction | PyPSA `ShuntImpedance` (g, b) in `n.pf()` **supported**; steps only in campus | fixed input; size *proposed* by campus `size_compensation` | shunt symbol at the bus | Electrical: Mvar, steps (sidecar), switching | data centre (campus PCC compliance) |
| STATCOM | dynamic ±Mvar at a bus, voltage control | **campus only** (sgen p = 0, ±q; IEC current source); none in `n.pf()` (GK: is a PV generator at p≈0 an honest stand-in without Q-limit enforcement?) | fixed input; campus sizing proposes | converter-to-bus symbol, "STATCOM" | Electrical: ±Mvar; Expert: control mode, current factor | data centre (campus library) |
| SVC | thyristor-switched Mvar, voltage control | **missing** in both engines | — | not offered | — | none |
| VSC / converter station (AC/DC, grid-side inverter) | P transfer plus V/Q control at the AC terminal | **partial**: a `Link` (fixed P both ends) or a PQ/PV injection; no converter control (P3) | P sized by the LP when extendable; V/Q setpoints are inputs once modelled | link symbol with a converter mark | Electrical: rating, Q range, control (after P3) | IPP (PV/BESS inverters as injections); none uses an HVDC link |
| 2-winding transformer, taps / OLTC | voltage step, tap position; OLTC regulates voltage | tap position **supported** (static); **OLTC control missing** in `n.pf()`; campus has the tap block | rating sized only if extendable; taps are inputs | transformer symbol | General: type (library / PyPSA std type); Electrical: tap side, range, step, vector group, earthing | data centre (site transformer) |
| 3-winding transformer | — | **missing** (gridspine ingest; pandapower has it) | — | not offered | — | none |
| Line / cable type | impedance and rating | **supported** (r, x, b; PyPSA std types) | rating sized if extendable | line | General: type, length, parallel; Expert r, x, b | all with a network |
| Switch / breaker | topology, switchgear rating | **missing** in ingest; priced switchgear item in campus | fixed | not offered | — | none |
| PCC short-circuit data | fault level and X/R at the PoC | campus **supported** (`sk_max/min`, `rx_max/min`); bus `eh_sk_mva` | fixed input | on the PoC bus | Connection (section 3): fault level, X/R | data centre, microgrid |
| Synchronous condenser / generator Q limits | reactive capability | PV control **supported**; **Q-limit enforcement missing** (no PV→PQ switching) | fixed | generator | Electrical: control PQ/PV; Q limits after P3 | gensets (data centre, microgrid) |

Rules for these assets:
- **The network owns what both engines model** (§10a.3's per-field owner table): r/x/b, `s_nom`, tap position, phase shift, `q_set`. The electrical sidecar (P0) holds only what PyPSA can't express: steps, STATCOM, vector group, earthing, switchgear ratings, PCC Sk and X/R.
- **Sized vs fixed**: none of these is sized by the LP (it is linear and active-power only). Reactive compensation is **proposed** by the campus study (`reactive_need`, `size_compensation`) and shown in results › Electrical design as "Proposed: 2 × 5 Mvar at bus X"; adopting it is an EditCard that adds the asset in section 5, never a silent write.
- **Not offered until an engine plan lands**: SVC, 3-winding transformer, switch, OLTC control, converter V/Q control, Q-limit enforcement are P3 engine slices (one gated plan each). They read "Not yet available" in the palette's Grid group with one line why.

**C. Results tabs.**

| Tab | Displays | Units | Evidence | Notes |
|---|---|---|---|---|
| **Load flow** | Headline: max line and transformer loading, min/max voltage, losses. Charts: loading heatmap by branch × hour, voltage band per bus, reactive flows. Table: overloaded branches with weighted hours. Action: "Run AC power flow again" through the gate (audit #4) | %, p.u., MW / MVA, Mvar, MWh | branch-loading/1, network-loading/1 (LOPF proxy; refused once AC PF overwrote outputs); v2 displayed-data ACK | Sub-option Load flow. Without AC PF: the LOPF proxy, labelled "active-power proxy, not an AC assessment" |
| **Grid code & connection** | What the scorecard doesn't show: campus PCC compliance (PF, voltage, harmonics where modelled), connection capacity and connection-point assessment (planning → dynamics). One link: "Grid-code verdicts: see Readiness › Grid readiness ›" (the scorecard, with DC 03's before / after-battery values, has its one home in 9) | MW, %, Mvar, kV, MVA | campus and gridspine withheld; the scorecard's own grid-readiness/1 (S9, #500, open) stays with 9 | Sub-option Grid code |
| **Electrical design** | transformer loading and sizing, reactive compensation need and proposal, short circuit (IEC 60909: Ik″, ip, breaking), critical hours, campus investment and joint optimisation (MILP) | MVA, Mvar, kA, EUR | withheld (`outside_checked_scope`) | Sub-option Grid code (electrical design half). Proposals adopt through EditCards |
| **N-1 security** | binding contingencies, the lines they load, the cost of security (SCLOPF vs plain LOPF objective) | MW, %, EUR | none | Sub-option N-1. **Gap: no view exists today** (ticket R-N1) |

**D. Assistant.** Reads `get_loadflow_summary` (displayed-data ACK: it may say "showing"), `campus_*` reads, `campus_grid_readiness`. Explains a red row with its limit and the input that sets it, and opens section 3 or 5 on the field. Proposals (add a capacitor bank, change a tap) are EditCards naming section 5.

### 3.3 Control

Sub-options: Converter control presets · Island, ride-through & load smoothing · Frequency & dynamics.

**A. Workspace.** Section 5 (the Controls block of each converter-coupled asset, §10), 8 (island requirements: sustained hours, bridge s, fuel autonomy, GFM margin, RoCoF window, frequency limits), 9 (Run). Next: Reliability when in scope, else Hand-off.

**B. Card assets.**

| Item | What it does | Engine today | Sized or fixed | Canvas | Block | Example |
|---|---|---|---|---|---|---|
| Converter control mode (bess, pv, wind, fuel cell) | `gfl_pq`, `gfl_qu`, `gfl_pf`, `gfl_fw`, `gfm_droop`, `gfm_vsm`, or a preset | `gridspine/schema/island.py` (`CONTROLS`, `PRESETS`), `island_config.json` sidecar; grid-forming only credited on BESS | fixed input | a small GFM/GFL badge on the asset | Controls (section 5) | data centre, microgrid |
| UPS (kind `ups`) | ride-through bridge, walk-in | island sidecar unit; DC 03 reads walk-in s | rating sized by the LP when extendable; walk-in fixed | storage symbol with UPS badge | Controls: walk-in s, frequency window, transfers on islanding | data centre |
| Battery reserve split (backup vs smoothing) | one reserve shared between backup and load smoothing (RLR-527) | DC 03 slices (#538 smoothing screen, #544 after battery, #552 latency) | fixed share | — | Controls / Availability on the BESS | data centre (AI-training load) |
| Load-shedding stages (frequency cuts) | under-frequency relay stages | campus cuts job | fixed (the study proposes, the EditCard applies) | — | 8 › island requirements | microgrid |

Nothing here is a new physical asset; Control's card assets are **settings on converter-coupled assets**. Frequency & dynamics needs a dynamics engine (GS-O2, GS-G1); not yet available.

**C. Results tabs.**

| Tab | Displays | Units | Notes |
|---|---|---|---|
| **Island & ride-through** | Headline: island sustained hours vs requirement, bridge seconds covered, fuel autonomy. Island report rows, pickup sequence (#534), frequency cuts, the load-smoothing screen (#538) | h, s, Hz, Hz/s, MW | Sub-option 2. Today run from Campus › Island; W4a marks it "Not yet available" until W0 (open item O2) |
| **Grid-forming** | GFM margin, the recommendation per unit, its "apply" as an EditCard naming section 5 (§10) | MVA, % | Sub-options 1–2 |
| **Frequency & dynamics** | "Not yet available" with why; when GS-O2/GS-G1 land: RoCoF, nadir, settling | Hz, Hz/s, s | Sub-option 3 |

No checked profiles; every copilot claim here reads "Requested results unavailable: outside_checked_scope".

**D. Assistant.** Reads `campus_*` island reads; explains a failing requirement and which unit's control mode is short; proposes a preset change as an EditCard (section 5).

### 3.4 Reliability

Sub-options: Reliability goal & study · Stress conditions & FMEA · Outage-data screening.

**A. Workspace.** Section 3 (archetype), 5 (each asset's Reliability and Availability blocks: outage rate, MTTR, derating), 8 (the one reliability goal: LOLE, ENS, draws, W3b-R; stress conditions, #535), 9. Next: Commercial & finance when in scope, else Hand-off.

**B. Card assets.**

| Item | What it does | Engine today | Block | Example |
|---|---|---|---|---|
| Outage data per asset | forced outage rate, MTTR, method, source, confidence | `asset_health.json` sidecar (`services/adequacy/asset_health.py`), over the network's outage columns; COPT, MC and the EH study read it | Reliability block (section 5) | every EH template |
| Availability profile | maintenance, derating, must-run | network `p_max_pu` series and the Availability block | Availability (section 5) | data centre, microgrid |
| Redundancy (N+1 units) | identical-unit counts the EH pack checks | EH reference design | Reliability block | data centre |
| Stress conditions | heatwave, dunkelflaute, low-wind week | stress scenarios sidecar | 8 (#535) | data centre, microgrid, H2 hub |

**C. Results tabs.**

| Tab | Displays | Units | Evidence | Notes |
|---|---|---|---|---|
| **Adequacy** | Headline: the verdict (pass / fail / inconclusive) of LOLE against the one goal, ENS. EH reference design certification, levers, DTC; Monte Carlo; frontier; the loops' result (inputs leave, W3b-R). The dispatch's unserved energy is cited with a link to Capacity expansion › Operation › Lost load | h/a, MWh/a, EUR | MC and EH per-year LOLE withheld (`samples_not_retained`) | Sub-option 1 |
| **Outage screening** | COPT LOLE and EUE, reserve margin per period, derating, binding hours | h/a, MWh/a, MW, % | copt-screening/1 (#483, open) | Sub-option 3; no solve |
| **Stress & FMEA** | FMEA worksheet ranked by EUR/a, sweep results per stress condition, read-only list of the conditions with "Change in Scenarios & optimisation ›"; the worksheet's expert rows and mitigability stay as annotations (O4b) | EUR/a, MW, h/a | withheld | Sub-option 2 |

Lost load is not a Reliability tab: it comes from the optimisation's VoLL slack, so it is an operation result and lives under Capacity expansion › Operation (UX ruling O4a). Every Reliability tab follows the scope rule without exception.

**D. Assistant.** `run-study` and `improve-design` workflows; reads `get_adequacy_results`, `get_study_findings`, `explain_verdict`; proposes a goal change or an outage-rate edit as EditCards (8 or 5); the FMEA sweep starts through the gate.

### 3.5 Commercial & finance

Sub-options: Tariffs & bill · Contracts & PPAs · Investment case & parties.

**A. Workspace.** Section 7 only (tariffs, market bundle, contracts, finance, parties; #533 moves the last editors out of Results), plus 5's Commercial block on each asset (party tags). Run: the LP's tariff/PPA terms inside `optimisation`; `investment_case`. Next: Hand-off.

**B. Card assets.** Commercial objects, not physical ones: tariffs, PPAs (financial, pay-as-produced, CfD), market bundles, parties and their ownership tags. Placed in 7, tagged on assets in 5 (C6). No canvas symbol; the party board (`ValueFlowConfig`) is 7's own view.

**C. Results tabs.**

| Tab | Displays | Units | Evidence | Notes |
|---|---|---|---|---|
| **Bill** | Headline: annual bill, EUR/MWh all-in. Components (energy, network, capacity, fixed), months, settlement, the billing-vs-LP gap; "incomplete" when a component can't be billed (P2d-peak) | EUR, EUR/MWh, EUR/kW | none | Sub-option 1 |
| **Contracts & revenue** | Revenue by stream per year (#515: merchant, PPA, CfD difference, import cost, O&M, replacement; sums to the IC revenue), capture price and rate, battery charge/discharge price, CFE score (orphan endpoint gets its home), links to Curtailment and to Asset value at model prices | EUR, EUR/MWh, % | none | Sub-option 2 |
| **Investment case** | Headline: NPV, equity IRR, LCOE; verdict against the counterfactual; cash flows by year; break-even (e.g. PPA price at target IRR, island break-even) | EUR, %, EUR/MWh | investment-case/1 (#480), investment-lcoe/1 (#482), both open | Sub-option 3 |
| **Parties** | value flows ledger, per-party view (C7), balance per party | EUR | none | Sub-option 3 (parties half) |

**D. Assistant.** `investment-decision` workflow; reads `get_investment_case`, `explain_cashflow`, `explain_investment`, `solve_ppa_price`; proposes a tariff, PPA or finance change as an EditCard naming section 7.

### 3.6 Hand-off

Sub-options: Reports · Data exports · Specialist packet.

**A. Workspace.** Results-only (no input section). Its deliverable choices (report variant per party, formats) live in its own tabs as actions, not model inputs.

**B. Card assets.** None.

**C. Results tabs.**

| Tab | Displays |
|---|---|
| **Reports** | generate (variant per party), viewer, versions, evidence hash, template picker, round trip. This tab **is** today's Reports panel (one component, moved), and section 10's sidebar Reports row opens it |
| **Exports** | This tab **is** the one "Export…" chooser from #556 / SAVE-1 (bundle, NetCDF, CSV, Excel, MATPOWER), as one component. The sidebar "Export…" opens Hand-off › Exports, not a standalone dialog. Per-view CSVs stay on their views |
| **Specialist packet** | planning → dynamics: dispatch source, ranked snapshots, assumptions ledger, per-hour bundles, PowerFactory read-back |

So R6 adds no second Reports or Export (audit #10, #16): the existing entries now open these tabs.

The last tab of every other group ends with "Next: Hand-off › Reports ›" (or the next card in scope). Hand-off is always on (`locked_reason="Always on."`).

**D. Assistant.** `build_study_report`, `export_*` (withheld as checked claims; an export's bytes are not a claim), the planning bundle reads.

### 3.7 Not a card's tab

- **Asset results** (today's Asset Detail tab) becomes a **drawer**. It has three contextual entry points: a row in a results table, an asset on the canvas, and Properties' "View results". There is no general "Asset detail" entry anywhere (contextual entries are fine under UX-flow rule 1). It shows the asset's results from every card in scope, sectioned by card, and keeps its id `asset` for the assistant (`ui_open_asset_detail`).
- **Compare** stays a rail across groups (base + N cases, integration-sensitivities §5.2); a card off in one case reads "outside scope" in that column.
- **Run-bound view** stays an overlay.

### 3.8 The owner's words → cards

| Owner word | Card › tab |
|---|---|
| capacity expansion | Capacity expansion › Build |
| load flow | AC power flow & grid › Load flow |
| reliability | Reliability › Adequacy |
| curtailment | Capacity expansion › Curtailment (by cause from #515; cross-linked from Commercial › Contracts & revenue) |
| statcoms, SVC, converters, VSCs | AC power flow & grid card assets (§3.2 B); converter control modes are Control's (§3.3 B) |

---

## 4. One entry point per result, and the landing

- **Results › *card* › *tab*** is the single home of each result. Every other place links to it (UX-flow rule 1): the canvas overlay, Properties, the Guided tiles, the scorecard in 9, Summary.
- **Summary is the landing** after a solve (#543; `results:solve-seen`). It shows **one row per card in scope**, in card order: the card name, its headline (§4.1), its state pill or freshness, and "Open ›" to its first tab. Cards out of scope are listed under "Not in scope" with "Add" (W4a's `NotInScopeGroup`). It ends with #543's "Next: hand off" (Reports, Export).
- **Group strip**: Summary, then the six cards in registry order. An out-of-scope card's group stays in the strip, greyed, and opens to its "Outside scope" panel (never hidden: scope is visible).

### 4.1 Headline per card on Summary

| Card | Headline (one line) |
|---|---|
| Capacity expansion | system cost; new build MW / MWh; curtailment %; unserved energy when not zero |
| AC power flow & grid | max loading %; voltage band; scorecard "n of m pass", linking to Readiness › Grid readiness (its one home, 9) |
| Control | island sustained h vs requirement; GFM margin |
| Reliability | LOLE vs goal (verdict); ENS |
| Commercial & finance | NPV, IRR; or the annual bill when no investment case |
| Hand-off | last report and its date; "Generate" |

---

## 5. Guided

- Guided Results shows the **same groups** at its depth. Each group's first Guided view is a **card summary** (today's chapter tile, grown: headline, state, one chart) with "Show the detail" opening the Expert tab as "Advanced" (as `Results.tsx` does today).
- The chapter tiles (`chapters.ts`: Investment, Grid plan, Reliability, Operation) are renamed and re-keyed to the cards: Investment → Commercial & finance, Grid plan → AC power flow & grid, Reliability → Reliability, Operation → Capacity expansion › Operation; Control gets its tile (today folded into Operation's control line).
- The **Improve** card stays (findings, sweep), linking to Reliability › Stress & FMEA.
- Guided and Expert agree: the same groups, tab ids and states; Guided hides Expert-only tabs, never moves them.

## 6. The assistant in a card workspace

What exists and is reused:
- **Workflows** (`harness/workflows/`): `explain-results`, `run-study`, `improve-design`, `investment-decision`, `hub-design`, `build-network`.
- **Navigation**: `ui_open_panel(panel_id="results", results_tab=…)` with `RESULTS_TAB_ENUM`; `ui_open_asset_detail`.
- **Reads**: the views' `reads` in `result_views.py`; `list_result_views` (status per view, never a value).
- **Edits**: LC-04's EditCard (old → new, section named) for every change; the copilot never edits a results tab (there are no inputs there).
- **Check answers**: per view `checked_profiles`; copilot content carries its check state, native views their freshness (§5.4).

What this design adds (one harness slice, R0 and H1 in §8):
- `ResultView` gains `card` and `sub_option`; `list_result_views` filters by card, so "walk me through the Grid card" lists only that card's views with their status.
- New view ids for the views that have no id today (§7.1); `RESULTS_TAB_ENUM` and `navigation.PANELS` follow.
- A **"Walk me through this card"** entry (W9's "Help with this section", at card scope), in the workspace header strip only (§2.1): the companion reads the workspace (`/flow` filtered to the card), says what is incomplete in the card's sections, opens each field in place, then runs the card through the gate, then opens the card's results group and explains the headline with `explain-results`. One workflow file per card is not needed: one workflow, `card-walkthrough`, takes the card id; its steps are the four workspace steps (§2.1).
- **Card assets**: the copilot can add a card asset only through an EditCard naming section 3 or 5, and only for assets the palette offers (§2.2); for one that isn't offered it says so.
- Guided: the same walkthrough, with the Guided depth (key parameters only); Expert: every parameter.

## 7. Examples

### 7.1 Opening tab and filled tabs

Presets from W4a `TEMPLATE_PRESETS` (#496); "not yet" means W4a marks the sub-option not available. Opening tab via `initial_results_tab` / `initial_results_section` (#515 introduced them; only the IPP sets them today).

| Example | Cards in scope (preset) | Opens on | Tabs it fills | Headline numbers (source) | Gaps → ticket |
|---|---|---|---|---|---|
| Data centre (`eh_datacenter`) | Capacity 1–2; Grid: grid code; Control: presets (+ island, not yet); Reliability 1–2; Commercial: tariffs (+ IC, not yet); Hand-off | **Summary** (its Grid row shows the scorecard's "n of m pass" with a link to 9; its Reliability row the LOLE verdict). Grid code & connection only once a campus run exists | Build, Operation (incl. Lost load), Costs & prices; Grid code & connection, Electrical design (after a campus run); Island & ride-through, Grid-forming; Adequacy, Outage screening, Stress & FMEA | DC 03: a 40 MW / 10 MWh UPS walks a 23.4 MW step in at 93.6 MW/min against a 4 MW/min limit, so ramp still fails after the battery; oscillation and voltage change pass; 2 rows pass after mitigation, 3 red flags stay (`tests/test_grid_readiness.py:737-756`). No cost or LOLE pinned | **X-DC1**: pin the example's headline (cost, LOLE) in a test. **X-DC2**: no tariff in the single-year template, so Commercial › Bill is empty although Tariffs is preset |
| Data centre, staged | as above, multi-period | Commercial & finance › Investment case | + Build per period, Investment case (NPV with island break-even) | NPV < 0 and break-even > 0 (`test_eh_datacenter_staged_case.py:455-456`); −7,992,548 EUR only in a spec | X-DC1 (pin NPV) |
| Island microgrid (`eh_microgrid`) | Capacity 1–2; Control 1 (+2 not yet); Reliability 1–3; Commercial: tariffs (+ IC not yet); Hand-off | **Reliability › Adequacy** | Build, Operation (incl. Lost load); Island & ride-through, Grid-forming (GFM recommender); Adequacy, Outage screening, Stress & FMEA; Investment case | NPV 4,588,396 EUR; new battery 7.519 MW (`test_eh_microgrid_case.py:134,147`) | **X-MG1**: the investment case runs but the preset's IC sub-option is "not yet" (W4a/W0 mismatch, open item O3) |
| Island microgrid, staged | as above | Reliability › Adequacy | + per period | NPV 43,231,055 EUR; battery 6.08 / 6.08 / 3.27 MW (`test_eh_staged_hubs.py:288`, `eh_staged_hubs.py:525`) | — |
| Hydrogen hub (`eh_h2_hub`) | Capacity 1–3 (process not yet); Reliability 1; Commercial 1–3; Hand-off | **Capacity expansion › Operation › Products** | Build, Operation, Costs & prices (LCOH); Adequacy | objective 7.0 M EUR, 8.8 / 11.4 / 28.9 M at 1 / 4 / 24 h islanding (measured, `eh_templates.py:839-843`, not pinned) | **X-H2**: Products sub-view needs a UI for `/results/process_view` (orphan) |
| Hydrogen hub, staged | as above | Commercial & finance › Investment case | + Investment case vs grey H2 | NPV +14.6 M EUR; grey H2 bought 51,041 / 112,361 MWh in 2030 / 2035 (`test_eh_staged_hubs.py:236-239`) | X-H2 |
| Green ammonia, staged (`eh_ammonia_staged`) | Capacity 1–3 (process not yet); Reliability 1; Commercial 1, 3; Hand-off | Capacity expansion › Operation › Products | Build, Operation (UC), Costs & prices; Adequacy; Investment case vs grey NH3 | none pinned (unit anchors 501.1 and 800 EUR/t, `test_eh_ammonia_staged.py:107-108`); retune pending (AMM-RETUNE) | **X-NH3**: pin a headline after AMM-RETUNE; X-H2 |
| Renewables IPP + battery (`eh_ipp_hybrid`) | Capacity 1–2; Commercial 1–3; Hand-off | **Commercial & finance › Investment case** (sets it today: `initial_results_tab: investment`, section `case`) | Build (battery size), Operation, Curtailment (by cause), Costs & prices; Contracts & revenue (revenue by stream, capture), Investment case; 8 cases in Compare | break-even wind PPA at 8 % equity IRR (`test_eh_ipp.py:413`); both curtailment causes positive (`:216-227`); day-ahead mean 75.8 EUR/MWh, 148 negative hours (issue 03) | **X-IPP**: pin battery MW and revenue by stream |
| Own site (`eh_site`) and presets (incl. campus, hospital) | Capacity 1–2; Reliability 1 (hospital +2); Commercial: tariffs (+ IC not yet); Hand-off | Summary | Build, Operation; Adequacy; Bill | none pinned (generic NPV 18,642,945 EUR only in a spec) | — |
| Blank / import | Capacity 1–2; Commercial: tariffs; Hand-off | Summary | Build, Operation, Costs & prices | — | — |

Cross-example gaps:
- **No example exercises AC power flow › Load flow or N-1** (W4a: both "from settings"; no template turns `run_ac_pf_after_lopf` on). **X-LF**: the data centre (or a new campus preset) turns load flow on, so the Load flow tab has a shipped example; gated with the gatekeeper (it changes a template's solve).
- **No example uses a STATCOM, shunt bank or OLTC in the network.** The campus library carries them in YAML only. **X-RC**: after P0/P2, the data-centre example carries its PCC compensation as section-5 assets.
- **Control › Frequency & dynamics** has nothing to show anywhere (not yet available; GS-O2/GS-G1).

### 7.2 Opening-tab rule

`initial_results_tab` takes the new `card` and `tab` ids (§8 R0), with the old ids as aliases. Every shipped example sets it (X-OPEN, template data, one small PR after R1).

**Never open an example on a tab that will read "Not run yet"** (UX review). The opening tab is where Results lands after the example's first Run, so it must be a tab that this Run fills. A tab that needs a separate study (the campus run, the FMEA sweep) is never an opening tab; the example opens on Summary, whose row for that card links on. Before any run, a new project opens in the flow at its first incomplete section (audit #26), not in Results.

---

## 8. Slices

Order follows workspace-reconciliation §13 (W10 after W3a, W4a; W3b moves first).

| Slice | What | Model | Gate | After |
|---|---|---|---|---|
| **R0 Result registry by card** | `ResultView.card`, `.sub_option`, `.tab` (group-local); new view ids: `grid_code`, `electrical_design`, `n1_security`, `island`, `grid_forming`, `dynamics`, `bill`, `contracts_revenue`, `parties`, `reports`, `exports`, `specialist_packet`, `products`; old ids kept as aliases (deep links, stored tabs, `RESULTS_TAB_ENUM`); `list_result_views(card=…)`; `navigation.PANELS` / ACK rules unchanged except the new ids (mounted-view only) | Opus | **gatekeeper** (harness contract) + coordinator told; UX on labels | W4a #496 |
| **R1 Results shell by card** | group strip = Summary + six cards from `/flow` scope; tabs per §3 mapping today's components; `outside_scope` and `failed` states (`ScopeGate`); freshness in each group header; Summary per-card rows (§4.1); Asset Detail as a drawer | Sonnet | UX | R0, W3a, #496 |
| **R2 Grid group** | campus results (#545's "Grid study results"), campus investment, gridspine connection capacity/assessment into Grid tabs; Load flow's "Run AC power flow again" through the gate (audit #4, with GK on the run path) | Sonnet | UX; GK on the run path | R1, #545 |
| **R3 Control group** | island report, cuts, pickup (#534), smoothing screen, GFM recommendation → Control tabs; GFM apply via EditCard | Sonnet | UX | R1; W0's Control row for "in the Run" |
| **R4 Reliability group** | Adequacy / Outage screening / Stress & FMEA; Adequacy cites Lost load (which moves to Capacity › Operation in R1) | Sonnet | UX | R1, W3b-R (inputs out of the panels), #535 |
| **R5 Commercial group** | Bill / Contracts & revenue (CFE score's UI) / Investment case / Parties | Sonnet | UX | R1, #533 |
| **R6 Hand-off group** | Reports (today's panel, moved), Exports (#556's chooser, moved), Specialist packet; the sidebar "Export…" and the Reports row open these tabs; remove the duplicate entries (audit #10, #16) | Sonnet | UX | R1 |
| **R7 Guided card summaries** | chapter tiles → card summaries; Control tile; Improve unchanged | Sonnet | UX | R1 |
| **R8 Capacity Products** | `process_view` and LCOH as Operation › Products (X-H2) | Sonnet | UX; GK told (reads only) | R1 |
| **R-N1 N-1 view** | binding contingencies and security cost from an SCLOPF solve | Opus (engine read) then Sonnet (view) | **gatekeeper** | R1; W4b for the N-1 kind |
| **H1 Card walkthrough** | `card-walkthrough` workflow over `/flow` + `list_result_views(card)`; EditCards for every change | Opus | **gatekeeper** (harness), UX | R0, W9 |
| **X-OPEN, X-DC1, X-DC2, X-IPP, X-NH3** | example opening tabs; pinned headline tests; DC tariff | Sonnet (data/tests) | GK told for pinned numbers | R1 (X-OPEN); independent (pins) |
| **X-LF, X-RC** | an example with load flow on; an example with compensation assets | Opus | **gatekeeper** | P0/P2 (X-RC) |
| **Card assets** | already sliced as **P0 → P1 → P2, P3** (workspace-reconciliation §13): P1 adds bus, line, transformer, shunt to the palette; P2 the campus assets (STATCOM, banks, taps, switchgear, PCC data) via the sidecar; P3 one gated engine plan per missing element (SVC, 3-winding, switch, OLTC, converter V/Q, Q limits) | Opus (P0, P2, P3), Sonnet (P1 UI) | gatekeeper; UX | as §13 |
| **E-dup** | one computation per result (results vs compare duplicates, §1.3) | Opus | gatekeeper | independent; low priority |

Dependencies outside this package: W4a #496 (scope, `ScopeGate`), W3b (#533 investment inputs, #535 stress editor, #545 campus split, #546 connection limits, W3b-R reliability goal), W3c #543 (Summary landing, "Next: hand off"), DC 03 (#544 merged, #562 / #565 open: after-battery rows and report chapter), IPP 05 (#515 merged: revenue by stream, curtailment by cause), checked-results S8/S9 (#480, #482, #500), LC-05/LC-12 freshness (`solve_freshness`).

## 9. Owner questions

| | Question | Options (recommended first) |
|---|---|---|
| RC1 | How many result tabs per card? | **A. ≤ 4 per card, matching the sub-options (§3)** (recommended: one tab per sub-option keeps the card ↔ results mapping obvious; Capacity's Operation holds its six views as sub-views) · B. One page per card, sections stacked (fewer clicks, long pages) · C. Keep today's five groups and add Grid/Control/Commercial (least change, but the groups don't match the cards) |
| RC2 | Where does "Asset economics" (per-asset revenue at model prices) live? | **A. Capacity expansion › Costs & prices** (recommended: it is the LP's own market value; the audit forbids comparing it with investment-case rows) · B. Commercial & finance, beside the investment case · C. Both, one linking to the other |
| RC3 | A card that is switched off: its results group | **A. Stays in the strip, greyed, opening to "Outside scope · Add" with the old results kept below, dimmed** (recommended: scope stays visible, "not lost" holds) · B. Hidden from the strip until switched on · C. Shown only when old results exist |
| RC4 | Grid card assets the engine can't model yet (SVC, VSC control, OLTC, 3-winding, switches) | **A. Not offered until each gated engine plan (P3) lands; the palette says why** (recommended: no asset that the run silently ignores) · B. Offered as drawing-only placeholders marked "not in the study" · C. Model them now in the campus engine only (results only after a campus run) |

## 10. Open items for the reviewers

- **O1 (GK)**: confirm §3.2 B's engine column per asset (asked 22:1x CEST).
- **O2 (GK, W0)**: W4a marks Control › Island "not yet available" while the campus island study runs today from its panel. Do its results show under Control as "From the campus study" until W0 adds it to the Run, or stay hidden?
- **O3 (GK, W0)**: same for Commercial › Investment case on the microgrid and data-centre staged examples, which run an investment case today.
- **O4 (UX, resolved 2026-10-21)**: (a) there is no Lost-load exception; Lost load moves to Capacity › Operation. (b) The FMEA expert rows and mitigability are result annotations, not inputs.
- **O5 (UX, resolved)**: the workspace is the filtered rail or sidebar plus a header strip, not a page.

## 11. Pointers

- Cards and scope: [modular-cards.md](modular-cards.md); W4a branch `claude/w4a-flow-scope` (`services/flow_scope.py`, `components/flowScope/*`).
- Results views: `pypsa-gui/backend/services/result_views.py`; `pypsa-gui/frontend/src/pages/Results.tsx`, `pages/results/groups.ts`.
- Assistant navigation: `harness/catalogue.py:91` (`RESULTS_TAB_ENUM`), `harness/navigation.py:12` (`PANELS`).
- Checked results: `.scratch/checked-results/audit.md`; display contract: [integration-sensitivities.md](integration-sensitivities.md) §5.4.
- Load-flow model and engine gaps: [workspace-reconciliation.md](workspace-reconciliation.md) §10, §10a.
- UX-flow audit: [issues/UX-flow-expert-audit.md](issues/UX-flow-expert-audit.md) (#1, #4, #10, #16, #25).
