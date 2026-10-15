# UX overhaul: assessment and proposal

**Date:** 2026-10-14. **Status:** proposal. Nothing is built yet; each section waits for the owner's pick.
**Baseline:** master `b2f6a5a`. M3 Summary (PR #245) read at `4fab3bd`.
**Browsable version:** https://claude.ai/artifact/KUBVM7ZckrzTgPUY7BGTDH (screenshots beside mockups; private to the owner until shared). Source: `2026-10-14-ux-overhaul/prototype.html` (throwaway mockup).

## How this was assessed

- **Setup.** Drove the real app with Playwright at 1440×900, plus 1280×800 for one check, in the shipped dark theme. Used the API on :8000, Vite on :5173, signed in as `admin@example.com`, and the **Data Center Energy Hub** template.
- **Three project states:**
  1. Unsolved.
  2. Guided hub study run: certified, 0.6 h/yr shortfall against a 3 h/yr goal.
  3. Expert LOPF solved: optimal in 4.78 s, total system cost €39.75 M.
- **Assistant.** No LLM key was configured, so the dock shows its "add a key" state throughout.
- **Labels.** Each finding is marked **M** (measured from a screenshot, a source grep or a contrast calculation) or **O** (opinion).
- **Screenshots** live in `2026-10-14-ux-overhaul/shots/`, named by surface.
- **Earlier assessment.** `.scratch/live-voice/dots-design-assessment-2026-10-14.md` covers the chat and Live companion compared with Dots. It contains no results-layout or tab-grouping analysis. I took the answer-first Summary from M3 (#245) instead and built the IA around it.

## 1. Findings per surface

### Login and projects

| # | Finding | Kind | Evidence | M/O |
|---|---|---|---|---|
| L1 | The static login page pre-fills the password `admin-pass-123` and prints "Review account: admin@example.com / admin-pass-123" (`frontend/index.html:395,402`, `public/login.html`). It ships in the build. The real dev password is different, so the hint is also wrong. | Bug / hygiene | `01-login.jpg` | M |
| P1 | At 1440×900 the existing-projects list is below the fold. Above it sit three eyebrow pills and three paragraphs about tenancy ("root projects", "scenario branches", "workbench"). | Crowded, jargon | `03-projects.jpg` | M |
| P2 | There are two creation menus with different names for the same things. The five cards say "Import from disk" and "Duplicate a project". The six wizard tabs say "From file", "Clone", "Study" and "Decision study". | Inconsistent | `03-projects.jpg`, `05-wiz-template.jpg` | M |
| P3 | The template cards show a developer path (`backend/project_templates/`) and internal pack ids (`weak_flexible`, `strong_grid`). The Belgium card says both "5 nodes" and "10 buses". | Hard to read | `05-wiz-template.jpg` | M |
| P4 | "Decision study" is offered as a card and as a wizard tab. Opening it says "not available in multi-user mode". | Misleading | — | M |
| P5 | Creating from a template failed with a 403 and showed no visible error. The account had no workspace; I created it through bootstrap, so this may be specific to this environment. | Bug, needs repro | backend log | M |

### Workspace shell (both modes)

| # | Finding | Kind | Evidence | M/O |
|---|---|---|---|---|
| W1 | With a slide panel open at 1440 px wide, the canvas gets about 220 px. The sidebar takes 240, the panel 560 and the assistant dock 420. The canvas legend then covers half the canvas. The dock keeps its 420 px (29 % of the width) even when it can't answer because no key is set. | Crowded | `35-expert-economics.jpg`, `40-run-dialog.jpg` | M |
| W2 | The sidebar's bottom chrome takes about 290 of 900 px: project card, Mode, Zoom and Preferences. That leaves about 450 px for navigation, and expanding Assets pushes the steps off screen. | Crowded | `30-expert-canvas.jpg` | M |
| W3 | The project name appears 5 times: breadcrumb, project tab, sidebar card, status bar and assistant. Run state appears 3–4 times: header pill, status bar, sidebar dot and the read-only banner. | Redundant | `40-run-dialog.jpg` | M |
| W4 | Switching from Guided to Expert leaves Guided's "Hub design" panel open, with a "GUIDED" crumb, inside Expert. | Inconsistent | `30-expert-canvas.jpg` | M |
| W5 | The same project, in the same session, shows "Autosave on · 5 min" in Guided and "Autosave off" in Expert. | Inconsistent, needs repro | `17-` vs `30-` | M |
| W6 | Right after creating a project, a banner told admin@example.com that admin@example.com was editing it ("Read-only"). Seen once. | Bug, needs repro | `10-workspace-first.jpg` | M |
| W7 | At 1280 px, "Run LOPF" wraps onto two lines and the KPI labels wrap onto three. | Crowded | `80-res-capex-1280.jpg` | M |
| W8 | The Log tab prints raw Python `LogRecord` reprs and linopy warnings. | Hard to read | `40-run-dialog.jpg` | M |
| W9 | The API key is requested three times on one Guided screen: the step banner, the dock card and the dock key form. | Crowded | `17-guided-results.jpg` | M |

### Guided

| # | Finding | Kind | Evidence | M/O |
|---|---|---|---|---|
| G1 | The plain-language flow shows raw ids: `grid_import (40 MW)`, `it_bus`, `site_transformer`. | Hard to read | `15-`, `17-guided-results.jpg` | M |
| G2 | Before the study has run, the Results step is greyed and disabled. It doesn't say what will appear there or how to get it. | Results not yet run | `14-guided-results-unsolved.jpg` | M |
| G3 | Key results are prose lines, and the money in one card uses two styles: "€39.75 M" in monospace and "€7.89 M" in proportional type. "Biggest risks" lists `grid_import — no shortfall` as risk 2, which is not a risk. | Inconsistent | `17-guided-results.jpg` | M |
| G4 | "Open full report" lands on the Expert-density Adequacy panel. It shows pipeline stage chips, three paragraphs of method caveats, `€39.7m` beside `€39.75 M`, and `ENS cap 10‱`. | Wrong density | `19-guided-full-report.jpg` | M |
| G5 | "Solved" means two different things. After the hub study, Guided shows certified results, while Expert and the assistant say "Not solved yet" until an LOPF runs. | Misgrouped state | `17-` vs `30-` | M |
| G6 | Guided Results shows the verdict, the cost and the risks. Grid plan and operation have no place in it at all. | Missing groups | `17-guided-results.jpg` | M |

### Expert inputs

| # | Finding | Kind | Evidence | M/O |
|---|---|---|---|---|
| E1 | The sidebar groups mix stage and category: Build, Time, Economics, Run, Results, Studies, Reports. Versioning (Snapshots, Scenarios) sits under Reports. Grid-plan work (Campus electrical) sits under Studies, apart from the Load flow results. Decision study is listed although it can't be used in this mode. | Misgrouped | `71-expert-reports.jpg` | M |
| E2 | "Economics" is the name of both an input panel and a Results tab. The Site connection input form appears in Economics and again inside Results → Investment. | Misgrouped | `35-expert-economics.jpg`, `67-res-investment.jpg` | M |
| E3 | Campus electrical starts from a raw YAML textarea (`campus:\n pcc: …`). | Hard to use | `70-expert-campus-electrical.jpg` | O |

### Results

| # | Finding | Kind | Evidence | M/O |
|---|---|---|---|---|
| R1 | There are 14 tabs in one strip. At 1440 px with the dock open, 7 are visible and the rest scroll away with no overflow cue. On master the panel opens on Capacity Expansion, or on Dispatch when unsolved, with no summary first. M3 adds the summary. | Crowded | `50-`, `22-expert-results.jpg` | M |
| R2 | Results are not grouped by question. Market value and revenue sit in Load Flow. Lost load is split from Adequacy (its own text says adequacy "moved to the Adequacy tab"). Asset Detail is an inspector, not a result. Investment hosts an input form. | Misgrouped | `52-`, `63-`, `67-`, `68-` | M |
| R3 | **Not run is shown as zero or a dash.** Load Flow shows "Total energy lost 0 MWh / 0.000 %" when losses were not modelled; a caption says so. Capacity Expansion shows "Storage charge cost —" next to "Storage capex €0.00". Curtailment draws empty axes for an all-zero series. Lost load says "No lost-load data available" while Guided reports 0.6 h/yr of shortfall. | Results not yet run | `52-`, `50-`, `62-`, `63-` | M |
| R4 | There are six KPI tile implementations: `results/shared.tsx KPI`, `PageKit StatCard`, `LostLoadTab Kpi`, `Curtailment Kpi`, `CompareView Kpi` and `decision/Verdict Kpi`. M3 adds a seventh (`SummaryTab Card/Headline`) with its own `fmtTonnes` and `fmtCapacity`. Labels are all caps and run up to 3 lines. In 2 of the 4 Prices tiles the value wraps ("136.64 / €/MWh"). | Inconsistent | source grep, `69-res-prices.jpg` | M |
| R5 | Units and precision vary for the same quantity: €39.75 M, €39.7m, €7.9m and 31.5M. Economics KPIs use TWh while the tables use MWh. Emissions read 134,902.1 tCO₂ in the KPI and 122,640 in the table. Dates come in three formats: `07-01 10:00`, `07/01/2030, 12:00 AM` and `2030-07-01T00:00:00`. | Inconsistent | `50-`, `60-`, `61-`, `68-`, `69-` | M |
| R6 | **Charts:** <ul><li>Axis titles overlap the tick labels (Capacity Expansion "EUR (annualised)", Dispatch "MW").</li><li>Some ticks carry no unit (Dispatch generation "60 / 45 / 30", Prices hourly "160 / 120").</li><li>Titles are cut off with an ellipsis ("System-cost wate…", "Generation Dispat…").</li><li>No limit or target is drawn: not the 40 MW import cap, not the 3 h/yr goal.</li></ul> | Hard to read | `50-`, `51-`, `69-` | M |
| R7 | Several tabs open with method prose before any number: Adequacy, the Economics "zero-profit" box and the Curtailment formula. | Hard to read | `64-`, `60-`, `62-` | O |
| R8 | The Asset Detail summary cuts off "350,400…". | Clipped | `68-res-asset.jpg` | M |

### Accessibility and tokens (dark theme, computed contrast)

| # | Finding | M/O |
|---|---|---|
| A1 | Axis and secondary text `#8b7679` on panel `#211b1c` is **4.0 : 1**. The AA minimum for 10–11 px text is 4.5 : 1. | M |
| A2 | White on accent `#ff5252`, used for primary buttons, is **3.2 : 1**. `accent-700 #e03131` gives 4.5 : 1. | M |
| A3 | `ink-300 #574447` on bg is **2.1 : 1**. It is used for disabled and placeholder text. | M |
| A4 | The TSX uses ten arbitrary text sizes from 9 to 15 px. `text-[10px]` appears 893 times and `text-[9px]` 130 times. | M |

## 2. Proposed information architecture

### One category vocabulary everywhere

The same four categories name the Expert tools, the Results groups, the Guided chapters and the report chapters. A fifth, **Project**, holds the things that cut across them.

| Category | Question it answers | Inputs and tools | Results |
|---|---|---|---|
| **Investment** | What should we build, and what does it cost and earn? | Assets and capacity bounds, Economics (rates, costs, tariffs, finance), Decision study | Capacity, system cost, asset economics (revenue, LCOE), prices, investment case |
| **Grid plan** | Can the network and the connection carry it? | Network (buses, lines, transformers), Campus electrical, Grid codes | Load flow (flows, loading, losses, voltages), grid-code compliance, short circuit |
| **Reliability** | Does the critical load stay on? | Reliability targets (ENS, reserve margin), outage data, stress scenarios | Adequacy (LOLE, EUE, margin), lost load, FMEA, Monte Carlo |
| **Operation** | How does it run hour by hour? | Horizon, time series, dispatch and solver settings, island operation and control | Dispatch, storage cycling, curtailment, emissions |
| *Project* | — | Scenarios, Snapshots, Reports, Solve queue, Issues | Compare runs, Asset detail (drawer) |

Two placements are the owner's call (decision D2): Emissions under Operation, and Prices under Investment.

### Results: two levels, Summary first

```
Summary │ Investment │ Grid plan │ Reliability │ Operation          [Compare] [Asset ▾]
          Capacity · Cost · Asset economics · Prices · Investment case
```

| Today's tab | New home |
|---|---|
| (M3) Summary | Summary: one KPI card per group |
| Capacity Expansion, Economics, Prices, Investment | Investment |
| Load Flow, Campus electrical results | Grid plan |
| Adequacy, Lost load, FMEA | Reliability |
| Dispatch, Storage cycling, Curtailment, Emissions | Operation |
| Asset Detail | Drawer, opened from any asset name |
| Overview (multi-period) | Summary, per-period strip |

**Implementation rules:**
- The group layer is presentation only. The `ResultsTab` ids, the deep links and the assistant's `ui_open_panel` targets keep working unchanged.
- If a group becomes a navigable target, regenerate the inventory (`assistant_inventory.py --write`, then `--check`).
- M3's Summary cards map onto groups as follows: cost and capacities → Investment, adequacy → Reliability, emissions → Operation. One Grid plan card is added.

### Results that haven't run: one component, six states

Each group and each tile always renders. Its state is stated in words, and a number appears only when one was computed.

| State | What the tile shows | Example from this run |
|---|---|---|
| **Ready** | The value, its unit and its basis | Total system cost €39.8 M, annualised |
| **Computed zero** | 0, with what makes it zero | Curtailment 0 MWh, all available renewable energy used |
| **Not run yet** | "Not run yet", what produces it, and a **Run …** button | Grid-code compliance: Run Campus electrical |
| **Not modelled** | "Not modelled in this run", the setting that turns it on, and a re-run button | Transmission losses: turn on in Solver settings |
| **Not applicable** | Why it doesn't apply | Line loading: this network has no lines |
| **Out of date** | The old value dimmed, with "Inputs changed since this run" and a re-run button | — |

A dash or a 0 is never used to stand in for any state other than Ready or Computed zero.

**In Guided:**
- The Results step is never locked.
- It shows the same groups and states, and each "Run" action points to the Guided step that produces the result.

## 3. Design rules

### KPI card standard (one component, replacing the seven)

- **Label:** sentence case, 12 px, muted, at most 2 lines. No all-caps.
- **Value:** 22 px, tabular figures. The unit is 13 px and muted, on the same baseline and on the same line (`nowrap`).
- **Basis line:** 11 px, for example "annualised · whole horizon" or "vs 3 h/yr goal".
- **State:** a pill for any state other than Ready.
- **Delta:** optional. Its colour comes from a semantic token (good, warn or bad), never from the accent.
- **Headline card:** one per group, marked by weight, not by a coloured rail.
- **Grid:** `repeat(auto-fill, minmax(180px, 1fr))`, equal heights, 12 px gap.
- **Placement:** the KPI row is always first in a tab. Charts follow, then tables, then method notes, which sit behind "How this is computed".

### Units and formatting (one `format.ts`)

| Quantity | KPI form | Table form |
|---|---|---|
| Money | `€39.8 M`, `€2.21 M`, `€801` (3 significant figures, k/M/B) | One unit per column, given in the header |
| Energy | `9.69 GWh`, auto-scaled within a row | One unit per column, chosen from the column's maximum |
| Power | `40 MW` | Same rule |
| Emissions | `135 kt CO₂` | `t CO₂` column |
| Price | `136.6 €/MWh` | Same |
| Reliability | `0.6 h/yr`, `3.78 MWh EUE` | Same |
| Percent | `19.8 %` | Same |
| Time | `2030-07-01 14:00` (ISO, 24 h) everywhere; axis ticks `Jul 01` with the year in the subtitle | Same |

**Further rules:**
- Raw ids never appear in Guided. Show display names, with the id in a tooltip for Expert.
- Developer paths never appear in the UI.

### Chart standard (one `ChartCard`)

- **Title:** wraps, never cut off with an ellipsis.
- **Subtitle:** gives the unit, the basis and the window, for example "€/MWh · hourly · 2030-07-01 → 07-07".
- **Y-axis:** the unit is printed on the top tick or as a horizontal axis label above the axis, so it never overlaps the ticks.
- **Highlight what matters:**
  - Draw reference lines for limits and targets: the import cap, the ENS goal, the mean.
  - Annotate the extreme point the KPI refers to.
  - Show the referenced series in full colour and mute the rest.
- **Colour:**
  - Carrier colours come only from `colourForCarrier`.
  - Semantic colours stay separate from the accent.
  - Axis text has at least 4.5 : 1 contrast.
- **Zero data:** an all-zero series shows a sentence, not empty axes.
- **Legend:** at the top left, in the same order as the stack.

### Density and spacing

- **Spacing:** 4 px grid.
- **Type tokens:** 11, 12, 13, 16 and 22 px, replacing the ten arbitrary sizes.
- **Three-column rule:** at most three content columns at once.
  - Opening a slide panel collapses Properties.
  - The assistant dock becomes a 48 px rail when it is unconfigured or a full-width panel is open, unless the user has pinned it. This touches the companion stream's files and goes to the coordinator first.
- **Sidebar:** gives its full height to navigation. Mode, theme and density move to the user menu, and zoom moves into the canvas toolbar.
- **Status:** shown in one place, the header pill. The status bar keeps the project facts.
- **Copy:** speaks in the user's terms. The how-it-works prose goes behind a disclosure.

## 4. Mockups

`2026-10-14-ux-overhaul/prototype.html` is a throwaway, static, HTML-only mockup in the app's own tokens. The published proposal page shows each mockup next to its "before" screenshot. It covers:
1. Results Summary with the group tabs, showing all six states using this run's real numbers.
2. KPI tile before and after, from Prices and Capacity Expansion.
3. Chart before and after: this run's grid-bus price, 168 h, with the evening peak and the mean highlighted.
4. Guided Results step before and after.
5. Workspace column budget before and after, as a schematic.

## 5. Slice plan (ordered by user value and risk)

| Slice | What | Value | Risk | Touches |
|---|---|---|---|---|
| **S1** | `format.ts` plus the `Kpi` card standard. Migrate the six results KPI variants. Display only, so no number changes beyond the agreed rounding. | High: consistency everywhere | Low | results/*, PageKit. M3 adopts it after #245 merges |
| **S2** | `ResultState` with the six states. Apply it to Summary, Load Flow losses, Capacity Expansion storage, Curtailment and Lost load. | High: answers the "never zero" rule | Low–med | results/*. Semantics per metric go to the owner (D3) |
| **S3** | Results group layer (two levels), Asset Detail as a drawer, and the Investment input form moved back to Economics. | High | Medium: navigation, layout tests, inventory | Results.tsx (after M3), inventory regen |
| **S4** | `ChartCard` and axis standard, reference lines, contrast tokens (A1–A3). | Medium–high | Low–med | shared.tsx, index.css |
| **S5** | Shell density: the three-column rule, the dock rail, the sidebar chrome moving to menus, a single status. | Medium–high | Medium | App.tsx, Sidebar. AssistantDock needs the coordinator |
| **S6** | Guided alignment: Results never locked, the same groups and cards, display names, and a Guided-density report entry. | High for Guided | Medium | hubDesign/*, split with the E2E session |
| **S7** | Entry surfaces: projects list first, one creation menu, template copy, login hint (L1). | Medium | Low | ProjectsHomePage, NewProjectWizard, index.html |
| **S8** | Expert sidebar regrouped by category. | Medium | Med–high: habit | sidebar/steps.tsx |

**Split with the other sessions:**
- **E2E guided demo (session_01X4NV…):** this session owns the IA, the shell and the shared components (`Kpi`, `ResultState`, `ChartCard`, group navigation). The E2E session owns the guided steps, the explanations and highlights, and the report chapters. Its chapter names use the four category names, and it builds its views from these components.
- **M3:** lands first. S1–S3 build on its Summary rather than replacing it.

## 6. Owner decisions (2026-10-14, via the coordinator)

| # | Question | Decision |
|---|---|---|
| D1 | Results grouping | (a) Two levels: Summary · Investment · Grid plan · Reliability · Operation, with sub-tabs. Existing tab ids stay. |
| D2 | Emissions and Prices | (a) Emissions under Operation, Prices under Investment. |
| D3 | Metrics that weren't modelled | (a) A "Not modelled" pill with no number, plus the setting that turns it on. The per-metric list is in §7, signed off. |
| D3-backend | The 10 cases the payload can't tell apart | (a) This session builds S2b: `voll_applied` on lost_load; `configured` on adequacy and reserve margin; a tri-state `binding` on emissions caps. Until it lands those 10 show "Not available for this run". |
| D4 | First slices | (a) S1 and S2 now; S3 after M3 (#245) merges. |
| L1 | Login credential | Removed now as its own PR (#249, merged). |
| R1 | Tile rounding | (a) 3 significant figures for money, energy, power and emissions; one decimal for prices, % and h/yr; ISO 24 h dates everywhere. Tables keep full precision. |
| G5 | Guided "certified" vs Expert "Not solved yet" | The E2E session's slice 10 owns the fix. ResultState gains `study_only` ("From the study"), agreed with E2E. |

| Q5 | Assistant dock over Results | (a) A rail that collapses over Results (or with no API key) unless pinned. Only Results collapses it (owner, 2026-10-15). |
| Q6 | Expert sidebar | (a) Regrouped by category: Model, Run, Results, Studies, Reports. |
| Q7 | Next slices | (a) S4 (charts), then S5. |
| S6 split | Guided alignment, agreed with E2E | UX builds the shared pieces (`displayName`, `AssetName`). E2E edits `hubDesign/**`: Results always reachable (a "Not run yet" card), display names, the Guided cards on Kpi / ResultState, and the Guided-density report entry. |

**Progress (2026-10-15):**
- Merged:
  - L1 #249, the plan #263.
  - S1 #252, S1b #261, S2a #257, S2b #262.
  - S3 #264, S3b #271, S4a #267, S4b #275, #279.
  - Dock rail #272, sidebar #273, `<Explain>` #274.
  - D3 rows: #277 (Economics, Storage cycling), #278 (Load Flow lines), #280 (Dispatch per carrier).
- Open: S7 #281 (entry surfaces), S6 #282 (display names).
- With E2E: S6 edits in `hubDesign/**` (see the S6 split above).
- Not done:
  - D3 rows 16–17 sit in Dispatch's hidden system strip, so changing them would change nothing visible.
  - The import-cap reference line has no chart that plots grid import yet.

## 7. Signed-off result-state list (D3)

This list comes from a code sweep of `frontend/src/pages/results` on master `bb933c3`. Codes:
- **NM**: not modelled or not configured.
- **NA**: not applicable (the asset doesn't exist).
- **NR**: not run.
- **CZ**: a genuinely computed zero.
- **FE-ok**: the frontend can already tell the cases apart.
- **BE**: needs the S2b backend field.

S2a (#257) covers rows 1–5, 8–14 and 30. S2b (#262) covers 18, 19, 26 (lost load) and 27. Rows 28 and 29 (adequacy and reserve margin) get their fields in S2b but are mapped to the existing "no target applied" copy in the panels. The remaining FE-ok rows (Dispatch per-carrier, Economics, Storage cycling) are the next S2 follow-up.

| # | Tab | Metric | Shows today | Condition | Turn-on setting | Tell apart? |
|---|---|---|---|---|---|---|
| 1–3 | Load Flow | Total energy lost, peak loss, % of demand | `0 MWh` / `0 MW` / `0.000 %` | NM: `losses.enabled === false` (`transmission_losses` off or SCLOPF). NA: no lines | Solver Settings → Enable transmission losses | FE-ok |
| 4 | Load Flow | Peak flow | `—` or `0.00 MW` | NA: no lines. NR | add lines / run | FE-ok |
| 5 | Load Flow | Mean loading | `—` | NA: no lines. NR | same | FE-ok |
| 6 | Load Flow | Lines table | "Lines (0)" plus "No line flow time-series." | NA vs NR | same | FE-ok |
| 7 | Load Flow | Top-loaded lines chart | "No data." | NA vs NR | same | FE-ok |
| 8 | Capacity Expansion | Storage charge cost | `—` | NA: no storage. CZ: storage that didn't charge | add storage | NA FE-ok; €0-charge vs never-charged needs BE |
| 9 | Capacity Expansion | Curtailment cost | `—` | NM: no generator has `curtailment_cost > 0`. CZ: has one, nothing curtailed | per-generator `curtailment_cost` | FE-ok |
| 10 | Capacity Expansion | Storage CAPEX (new) | `€0.00` | NA: no storage. NM: none extendable. CZ: extendable, built 0 | mark storage extendable | FE-ok |
| 11 | Capacity Expansion | Total new investment | `€0.00` | NM: nothing extendable. CZ: nothing built | set `*_nom_extendable` | FE-ok |
| 12 | Curtailment | Curtailed / delivered / rate | `0.0 MWh` / `0.00 %` | NA: no renewables | add renewables | FE-ok |
| 13 | Curtailment | Curtailment over time | flat zero chart | CZ: show a sentence when max = 0 | — | FE-ok |
| 14 | Curtailment | By-carrier table | "dispatched every available MW" with no renewables | NA vs CZ | add renewables | FE-ok |
| 15 | Dispatch | Curtailment toggle and chart | drawn flat at 0 | `hasCurtailment` checks only length | — | FE-ok |
| 16 | Dispatch | Storage discharged / charged | `0 MWh` | NA: no storage | add storage | FE-ok |
| 17 | Dispatch | Renewable curtailment | `—` or `0 MWh · 0.0 %` | NA vs CZ | — | FE-ok |
| 18 | Dispatch | Lost load / Lost-load cost | `—` | NM: VoLL = 0. CZ: VoLL set, no shed. NR | Solver Settings → VOLL | BE (S2b) |
| 19 | Dispatch (per carrier) | Lost load / cost | `—` | as 18 | VOLL | BE (S2b) |
| 20 | Dispatch (per carrier) | Storage discharge / charge | `0 MWh` | NA: no storage on carrier | add storage | FE-ok |
| 21 | Dispatch (per carrier) | Gen — links | `—` | NA: no links onto carrier vs CZ | — | FE-ok |
| 22 | Dispatch (per carrier) | Curtailment | `—` | NA: no renewables on carrier vs CZ | — | FE-ok |
| 23 | Dispatch (per carrier) | Storage charge cost | `—` | as 8 | — | Partial |
| 24 | Dispatch (per carrier) | Curtailment cost | `—` | as 9 | `curtailment_cost` | FE-ok |
| 25 | Dispatch (per carrier) | LCOE | `—` | NA: no dispatch on carrier | — | FE-ok |
| 26 | Overview | Storage, curtailment, lost load, lost-load cost | `0 MWh` / `—` | as 16, 17, 18 | as those | storage and curtailment FE-ok; lost load BE (S2b) |
| 27 | Lost load | Whole-tab empty state | "Set a VOLL" even when VOLL was set | `voll` at solve time | VOLL | BE (S2b) |
| 28 | Adequacy | Target chips / no-target note | "No reliability target…" | NM (no ENS target) vs NR | Solver Settings → ENS target (needs VOLL > 0) | BE (S2b `configured`) |
| 29 | Adequacy | Reserve-margin panel | "No firm-capacity standard…" | NM (no `reserve_margin`) vs NR | Solver Settings → Reserve margin | BE (S2b `configured`) |
| 30 | Emissions | Total emissions | `0 tCO₂` | NM: no carrier has `co2_emissions > 0` | Carrier tab → co2_emissions | FE-ok |
| 31 | Emissions | Shadow price / cap slack | `—` | cap inactive vs not binding | CO₂ cap global constraint | BE (S2b tri-state `binding`) |
| 32 | Economics | Charge cost (storage) | `€0.00` | NA: no storage | add storage | FE-ok |
| 33 | Economics | System LCOE/LCOS, avg capture price | `—` | NA: nothing dispatched vs NR | — | FE-ok |
| 34 | Storage cycling | Cycles / empty state | `0.00` when unbuilt; "needs extendable storage" with no storage | NA vs NR | add storage | FE-ok |
