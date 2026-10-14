# Plan: one guided data center plan, from projected load to report

**Date:** 2026-10-14. **Status:** audit and run-through done; slices proposed, waiting on the owner's answers
(§6) before any large build. **Session:** the end-to-end demo session, commissioned through the coordinator.

**Goal (owner):** a non-expert takes the data center demo case from a projected load to a full plan, guided by
the assistant, in five stages: (1) investment decision-making, (2) reliability trade-off, (3) electrical
requirements for grid-code compliance, (4) control strategy, (5) report export. Each stage's results are shown with
what needs attention flagged; reruns say clearly that the user must wait; the design extends to other cases (no
demo-only hacks in shared code).

## 1. How this was checked

- **Driven in the app.** API on :8000 and Vite on :5173, headless Chromium through Playwright, signed in as
  `admin@example.com`. Starting from **New project → From template → Data Center Energy Hub**, I clicked through
  Guided Hub design (Start → Site → Goal → Results → Improve), then switched to Expert and ran Run LOPF, Campus
  electrical, Reports and the Generate report dialog. The screenshots are in the session scratchpad; §2 cites what
  each one showed.
- **Read in the code** by three read-only audits (one per stage group); file:line citations are in §3.
- **Not verified live:** anything the assistant does. This container has no API key for the app's assistant, so
  the chat panel says "Add an Anthropic API key to talk to me", and every **Let the assistant do this** button is
  inert. Assistant coverage below comes from the tool catalogue and workflows, not from a live conversation.

## 2. The run-through: what a user sees today

| # | Action | Seen | Verdict |
|---|---|---|---|
| 1 | New project → From template | Seven templates; **Data Center Energy Hub**: "50 MW hyperscale campus behind a weak 40 MW grid connection … Ready for an Energy Hub study (weak_flexible)" | works |
| 2 | Create it | Opens in **Guided / Hub design**, step 2 Site, all four checks green (grid_import 40 MW, it_bus, grid strength, 9 outage units). A banner read **"Read-only — admin@example.com is currently editing this project"** for the same user who had just created it; after reopening, the banner was gone | works; read-only banner is a transient bug, seen once (to reproduce) |
| 3 | Goal → Run study (3 h/yr allowed shortfall, €5,000/MWh) | "Studying… (up to 30 calculation steps)" with **Abort**; done in **34 s**. The header pill still says **Idle** throughout | works; the busy state shows only on the card |
| 4 | Results | **"Certified: about 0.6 h/yr of shortfall, under the 3 h/yr goal."** Yearly cost **€39.75 M**. Biggest risks: site_transformer (~€7.89 M/yr); **grid_import — "no shortfall — the site copes without it"** is listed as a risk | works; the second risk line reads as a non-risk |
| 5 | Improve | One recommendation ("site_transformer accounts for 100% of the outage risk … compare with a spare unit or storage"), Check risks (FMEA), Add a stress scenario | works, but every action needs the assistant |
| 6 | Results → **Open full report** | Drops the non-expert into the Expert **Adequacy** wall (COPT, reserve margin, frontier, MC, loops, EH reference design). EH card: ENS cap 10‱, cost €39.7 M, **MC LOLE 0.60 h/yr [0.00–1.43], "draws 2000 (not converged)"**, **LCOE €95/MWh**; pipeline: **frontier: skipped**, redundancy skipped. COPT screening above it says **LOLE 0.0 h/yr** | the numbers are there; nothing is highlighted, and the two LOLEs disagree with no explanation on screen |
| 7 | Expert → Campus electrical → Draft from project | **"the project is not solved; solve it and save, then draft the campus"**. The guided study solved a private copy, so the project itself is unsolved | **seam broken**: stage 2 → 3 has no handoff |
| 8 | Run LOPF | Header **Optimal**, status bar `Completed` but **`Obj: —`**. Hub design Results now says **"These results are from an earlier study; the network was solved since. Run again to refresh."** | meeting stage 3's prerequisite makes stage 2 stale |
| 9 | Draft from project | Draft made; "storage unit **bess_new: built at 0 MW**" left out. The LOPF builds no battery, though the EH study's plan may differ | works; the two solves disagree on the design |
| 10 | Campus electrical → Run study | The button reads **Running…**, but the header still says **Idle** and there is no progress or wait message. The request is synchronous: at 100 % CPU in `gridspine/static/frequency.py` `e4_ups_bridged → simulate` (I6, eleven 5 MW pickup blocks, RK4 in pure Python) for **11 minutes** in all (18:54 → 19:05). **Result:**
    - Buys a 63 MVA grid transformer, 3 × 25 MVA site transformers and 19 switchgear bays: **capex €7.0 M**,
      **€0.62 M/yr** annualised, **1.5 %** of the hub's €39.75 M.
    - Reactive compensation is not needed.
    - Every PCC check under EU RfG/DCC **passes with the assets**. As is, the grid transformer is UNDERSIZED
      (43 MVA peak against 50 MVA, 52 MVA needed with the margin) and the switchgear is NOT RATED.
    - Two to check: SITE_TRANSFO is rated ADEQUATE, yet 3 units are bought; the Island section still says
      "Not yet available: island study", although the 11 minutes were spent in the island frequency checks | works, but runs 11 min with no "please wait"; good content, no highlighting |
| 11 | Reports → Generate report… | Dialog with a **Template** picker (built-in default; user .docx upload exists), language, 13 sections, **all reliability** (target, MC, cost, frontier, sizing, redundancy, levers, DtC, FMEA, TEA, gates, multi-energy). The panel also says "Open a Project to list its reports" while one is open. Generating needs the LLM | partial: no investment case, electrical, grid-code or control section; needs a key |
| — | Investment (stage 1) | `GET /api/results/investment_case` → 204 (none run). Running one on the template refuses with **`value_flows_not_configured`**: the template ships no commercial config (`services/results/finance_case.py:1489`). The Investment, Economics and Capacity tabs are **Expert-only** (`Results.tsx:66-86`) | **not wired** for this case |

## 3. Stage by stage

### Stage 1: Investment decision-making. **Missing for this case.**

Exists:
- The investment case engine: NPV, IRR, payback and LCOE (`services/finance/engine.py:315-376`). Chat tools
  `run_investment_case` and `get_investment_case`.
- The Investment tab: Participants, Bill, Library, Tariff, Contracts, Finance inputs, Investment case.
- Expert → Economics E1/E2.
- EH-study LCOE, system cost ÷ served energy (`services/adequacy/eh_study.py:295-318`), shown only in the Expert
  EH card.
- U3's decision study (verdict, options, tornado), but only one question, `bess_at_site`. It runs on its own
  synthetic site network (`services/study/packs.py:607`), not the template's, and its value streams are demand
  charge, energy shift and export, with no reliability. The workflow says outright that data-centre questions
  are "coming" (`harness/workflows/investment-decision.md:30-33`).
- Multi-period support in Expert: Model Horizon, `investment_periods`, vintages, `load_scalers`.

Gaps:
1. **No projected load or energization path.** The template is one July week ×52.14, a single period, at a
   fixed ~36 MW IT load. The finance engine refuses phased builds (`staged_build_not_supported`,
   `finance_case.py:1514`, which is P5).
2. **The investment case can't run on the template** (no commercial config or owner), and Guided can't see it.
3. **No "assets required" view.**
   - The EH study's build lives in a private copy (`project_context.py:343-346`).
   - Its `sizing` section is generators only (`eh_report.py:48-77`), so `bess_new` is left out. The 500 MW
     `grid_supply` counts towards `total_p_nom_mw`.
   - The sizing section isn't rendered.
4. No business-case summary (LCOE, NPV, IRR, payback, capex by asset) anywhere a non-expert looks.

### Stage 2: Reliability trade-off. **Works for one point; no trade-off curve.**

Exists and verified: the guided Hub design run (34 s, certified, cost and risks), the FMEA, stress scenarios
(heatwave, dunkelflaute) and the Adequacy stack.

Gaps:
1. **"What reliability each investment level buys" isn't produced on this template.** The `weak_flexible` pack
   has no `frontier_default` (`models/energy_hub.py:355-372`), so the frontier is **skipped**. Even when it
   runs, each point has cost and ENS but no LOLE and no asset mix (`services/adequacy/frontier.py`). The margin
   loop gives cost against LOLE but is a step function, and it's Expert-heavy.
2. **Sensitivities are scattered.** The decision-study tornado is NPV only. Stress scenarios feed the FMEA only.
   `run_sensitivity_sweep` re-solves at most 4 forks without re-running adequacy.
3. **The guided results don't flag anything.** "Not converged" MC, the COPT-vs-MC LOLE disagreement and the DSR
   warning are buried in the Expert panel.

### Stage 3: Electrical requirements (grid code). **Exists in Expert; the handoff is broken.**

Exists:
- `routers/campus_electrical.py` → `services/campus_electrical_service.py:1155` runs prepare → island tables
  → rank critical hours → island checks I4–I6 → size → invest.
- Sizing covers N-1 transformers, reactive compensation (inverter Q first, then STATCOMs, then capacitor or
  reactor steps), IEC 60909 short circuit against switchgear, and PCC compliance (`campus_compliance.py`).
- The investment step buys the least-cost **additional assets** from a library (transformers, cables,
  capacitor banks, reactors, STATCOMs, switchgear) and re-checks each by AC load flow. Every library price is
  an assumed placeholder.
- The chosen assets flow into the investment case as extra owner capex (`routers/results.py:1463-1600`).
- Grid codes: `eu_rfg_dcc_ce` and `generic_assumed` only (`gridspine/templates/data/grid_codes.yaml`). A PDF
  can be uploaded and its limits extracted by the copilot, then confirmed by a person.

Gaps:
1. **Stage 2 → 3 handoff.** The campus study needs the project solved and saved. The guided study doesn't
   solve the project, and solving it marks the guided study stale (run-through steps 7–8).
2. **The run is synchronous and long** (step 10), with no progress, no wait message, and an Idle header. It
   isn't a job, so `wait_for_job` can't follow it.
3. **Probable bug: unresolved needs never reach the investment case.** The UI (`CampusInvestmentSection.tsx:229-232`)
   says the case is "not compliant" without them, but `_ic_campus_from_response` (`routers/results.py:1544-1575`)
   reads only `assets`, `source_hash` and `stale`.
4. Grid-code coverage is voltage, Q range and RVC only. There is no FRT/LVRT, harmonics or frequency
   ride-through.
5. The campus file and island config are raw YAML/JSON textareas; no non-expert path.

### Stage 4: Control strategy. **Screening only; no design output.**

Exists:
- Per-unit control modes `gfl_pq|gfl_qu|gfl_pf|gfl_fw|gfm_droop|gfm_vsm`. The template's `bess_new` is
  gfm_droop 4 %.
- Single-bus aggregate frequency screening (`gridspine/static/frequency.py`) for events E1–E6, with RoCoF,
  nadir, quasi-steady-state deviation and return time, an ok flag and the binding limit, plus EMT/RMS flags.
- The I7 cuts turn a failing event into the inertia, headroom or droop gain the hub must buy, and re-solve up
  to 3 trials.

Gaps:
1. **No control-design output.** Nothing recommends droop, H or i_max per unit, or compares GFM against GFL.
   I7 buys capacity rather than settings.
2. **Order.** The island checks run *before* the stage-3 assets are bought, so they never see them
   (`campus_electrical_service.py:1175` against `:1176/:1186`).
3. **I7 does nothing on the default template.** Its scenario is `ups_bridged` only, cuts apply only to
   `seamless` (`frequency.py:1172`), and E4 gets no cut (`campus_island_cuts.py:41`).
4. **Failing checks aren't highlighted.** `ReportTable` prints `ok` as a plain "yes/no".
5. **The assistant can't read the I4–I6 report.** No chat tool returns it.

### Stage 5: Report export. **Exists for reliability; doesn't cover the plan.**

Exists:
- The LLM report (`routers/reports.py`, `report_jobs.py`, `services/reports/*`) exports **docx** and **pdf**
  (LibreOffice is present here: `capabilities → {"pdf": true}`).
- User .docx templates, tagged (`{{ }}`) or untagged (an LLM-proposed mapping plan), and a Word round-trip.
- Chat tools `generate_report`, `export_report_docx` and `export_report_pdf`, the template tools, and a
  browser smoke (`frontend/scripts/smoke-reports.mjs`).
- The decision-study report (docx, xlsx, html) and the EH report docx.

Gaps:
1. The evidence covers EH and adequacy only. **No investment case, campus electrical, grid code or control
   section**; a grep for campus, island or grid_code in `services/reports/` finds nothing.
2. There's no single five-stage report.
3. Generating needs the LLM; there's no deterministic (no-key) report.
4. "Open a Project to list its reports" shows while a project is open (seen once, to reproduce).

### Cross-cutting

- **AI guidance.**
  - The workflows are open-project, build-network, import-data, hub-design, run-study, explain-results,
    improve-design and investment-decision (battery). **None covers stages 3–5, and none spans all five.**
  - Guided steps are Start, Site, Goal, Results and Improve (reliability only).
  - `ui_open_panel` reaches `campusElectrical`, `reports`, `decision`, `economics`, and Results tabs including
    `investment` and `adequacy`, but not campus *sections*.
  - `ui_open_panel` isn't in `CONTROL_TOOLS`, so it drops out after `use_toolset` narrows the catalogue
    (`harness/toolsets.py:4-15`).
  - The navigation ACK allow-list lacks `decision` and `economics` (`harness/navigation.py:12-31`).
- **Reruns and waiting.**
  - There is no automatic rerun anywhere (only stale markers).
  - The header pill says Idle during the guided study and the campus run; the busy state lives only on the
    card or button.
  - The campus run isn't a job.
  - Stale chain: solving marks the guided study stale; the campus study has its own stale mark; the investment
    case has another. Nothing says "these three are out of date, rerun them in this order".
- **Results flags.** The Issues panel covers preflight, solve failure, layout and topology. **No shared
  registry of per-stage findings.**
  - Decision-study findings (`services/study/findings.py`), campus compliance rows and island pass/fail are
    each separate.
  - M3 (the Results Summary tab, PR #245, gatekeeper) is in flight; this plan builds on it, not beside it.

## 4. Design: one plan spine, cases as data

The five stages exist as islands. The missing piece is a **plan spine**: one place that knows, for a Project, which
stages ran, on which inputs, what they found, and what is stale. The guided UI, the assistant and the report all
read from it. To keep it general, the spine knows nothing about data centers. A **case definition** (data: which
template, which stages apply, which workflow, the report outline) says what a data center plan, a hydrogen hub
plan or a microgrid plan consists of.

- **Plan status** (backend, read-only): `GET /api/projects/{name}/plan`. For each stage it returns
  `not_run | running | stale | done`, the headline figures (each with an Unavailable flag, per ADR-0001), the
  findings (severity `info | attention | action`, text, the view that shows it) and the next step.
  - It is assembled from the existing records (EH study, investment case, campus state, island report, reports).
  - It stores nothing new.
  - One chat tool, `get_plan_status`, read tier, so the assistant explains and navigates from the same data.
- **Case definition**: `harness/cases/<case>.yaml` (or beside the template's `eh_template.json`) lists
  stages, the workflow id and the report outline. The data center case is the first. Other templates get a
  file, not code.
- **A guided Plan view** extends Guided mode past Hub design: stage cards in order, each showing status,
  headline figures and flags, with "Run" / "Rerun" / "Open details". While anything runs, one app-wide busy
  banner ("A study is running; please wait. You can keep reading.") reads the same job state as the header pill.
- **A `datacenter-plan` workflow** (Markdown, `harness/workflows/`) leads the five stages with existing tools
  plus `get_plan_status`. It asks the user back with Choice cards at each decision: target, investment level,
  grid code, control mode, report template.

### 4.1 Owner requirements added 14 Oct (relayed by the coordinator)

1. **The AI-guided walkthrough is the top priority**: a smooth path from projected load to results, with the
   assistant leading each step.
2. **Guidance continues once results exist.**
   - Every result tab shows its key results, highlighted, and explains them in each section or as a summary.
   - When the user switches tabs, the assistant follows and explains the tab they're on.
   - Both work the same in Guided and Expert: built once and shared, never keyed to the mode toggle.
   - Builds on M3's Summary tab (gatekeeper, PR #245).
3. **The report is structured**:
   - an executive summary first;
   - one chapter each for the investment case, the reliability case, the grid/electrical case and the control
     case;
   - a conclusion;
   - each chapter carries its stage's key results and flagged items;
   - it uses the template choice and upload.

The UX/UI overhaul session (session_015QMvmyA2gE13qRZc7E49hf) owns the information architecture: the category
grouping (Investment, Grid plan, Reliability, Control), the KPI card and chart standard, how not-yet-run
results look, and the layout. This plan renders highlights through **its** KPI/card standard, not a parallel one;
the split is in §4.3.

### 4.2 Highlights and explanations: what is computed and what is narrated

One rule: **every number and every severity is computed; the assistant only narrates.** The owner can then see
which is which on screen and in the report.

| Layer | Produced by | What it contains | Where it shows | Without an API key |
|---|---|---|---|---|
| **Key results** (computed) | A per-tab **insight rule set** in the backend (`services/insights/<tab>.py`), pure functions over the stored result records (EH study, investment case, campus state, island report, solve results) | 3–6 KPIs per tab, each with value, unit, Unavailable flag (ADR-0001), source record and run id | The KPI cards at the top of each tab (UX session's card) and the report chapters | shown |
| **Flags** (computed) | The same rule set: a threshold or condition per rule, with severity `info / attention / action` and a fixed sentence template filled with the computed numbers (for example "MC LOLE did not converge after 2,000 draws"; "LOLE 0.60 h/yr is under the 3 h/yr target"; "unresolved need: 12 Mvar reactive at the PCC"; "E4 block 3 nadir 47.3 Hz, under the 47.5 Hz limit") | rule id, severity, sentence, the view that shows it | Highlighted rows and cards in the tab, a flag list per section, the plan status, the report | shown |
| **Section summary** (computed) | Fixed templates over the KPIs and flags ("Certified: about 0.6 h/yr of shortfall, under the 3 h/yr goal", the style Hub design already uses) | one or two sentences per tab/section | Under the KPI row | shown |
| **Explanation** (narrated) | The assistant, given the tab's insight payload through one read tool, `get_tab_insights(tab)`, and the existing number audit (`services/reports/number_audit`; answer assurance) | Why it matters, what drives it, what to do next, answers to follow-ups; may only quote numbers present in the payload | The assistant dock; the report's prose paragraphs | absent; the computed layers stand alone |
| **Follow the tab** (narrated, triggered) | A tab-change event from the UI (debounced, opt-out) → the assistant reads `get_tab_insights` for the new tab and posts a short explanation | 2–4 sentences plus the top flag | The assistant dock | a computed "On this tab" card from the summary layer instead |

So the highlights exist with or without an LLM, in both modes, and the assistant's text can always be checked
against the numbers beside it. Rule sets are per tab, not per case: a data center, hydrogen hub or microgrid gets
the same rules, and a case definition only chooses which tabs and stages it shows.

### 4.3 Proposed split with the UX/UI session and M3

| Piece | Owner |
|---|---|
| Category IA, KPI card and chart standard, not-yet-run states, layout | UX/UI session |
| Results Summary tab | gatekeeper (M3, PR #245) |
| Insight rule sets and the `/insights` payload; the plan status; `get_tab_insights` / `get_plan_status` | this session |
| Rendering insights inside each tab, using the UX card component | this session, once the card exists (until then, M3's KPI tiles) |
| The guided walkthrough (workflow, Guided stage cards, busy banner) | this session |
| Follow-the-tab in the dock | this session's hook and event; the companion stream owns `AssistantDock.tsx`/`ChatPanel.tsx`, so the dock-side change goes to them or is agreed first |
| The structured report | this session |

## 5. Slices

### 5.0 Revised order after the owner's answers (14 Oct, relayed by the coordinator)

The owner's answers:
- Q1 (a) all five stages, thin.
- Q2 (b) **build P5**.
- Q3 (a) the template's numbers.
- Q4 **add IEEE 2800/NERC** beside EU RfG/DCC.
- Q5 (a) docx and PDF with the chapter outline.
- Q6 (a) a key will be added. Until then the fake provider; live-assistant claims stay unverified.
- Q7 (b) **recommended GFM settings in the first demo**.
- Q8 (a) a computed card, and the assistant only when the chat is open.

This session builds P5 and the GFM settings; the gatekeeper reviews and OKs each engine PR. **Engine first**, then the
walkthrough. Designs are in §7 and go to the gatekeeper before any code.

| # | Slice | Main files | Gates |
|---|---|---|---|
| 1 | **P5a: finance safety.** Close H1: refuse `staged_build_vintage_bounds_not_supported` when a vintage-bound parent was built in more than one period, instead of financing it all in year 1. Fix H2: the counterfactual's served load applies the solve's `load_scale_factors` | `services/results/finance_case.py`, tests | **G** |
| 1b | **FCA stress hours follow `load_scalers`.** `commercial/connection.py:498` `_site_load` ranks the FCA curtailment hours on the unscaled `p_set`. Until 1b lands, the FCA stress entry on a multi-period project with `load_scalers` is **not** scaled, so it may pick the wrong hours. 1b threads the LP demand through `bind_commercial` and re-binds when `load_scalers` change; the test checks that the ranked hours move when a scaler reshapes the load (gatekeeper, 14 Oct) | `services/commercial/{connection,binding}.py`, `routers/simulation.py`, `services/study/compile.py`, tests | **G** |
| 2 | **P5b: per-asset COD and staged capex** in the engine: `FinanceCase.asset_cod`, `Operating.stage_capex`, per-asset clocks for degradation, replacements, terminal value and LCOS; tax vintages; equity-funded later stages | `services/finance/{case,timeline,cashflow,replacements,engine,lcos,report,export_xlsx}.py`, `models/finance.py`, tests | **G** |
| 3 | **P5c: adapter.** Staged builds stop being refused: rule P5-a (a stage's COD year is a period year), stages from `build_year` and from campus extras, a stage table in the report; the remaining refusals listed in §7.1 | `services/results/finance_case.py`, tests | **G** |
| 4 | **GFM-a: recommender engine** `gridspine/static/gfm_tuning.py`: bounds, margin, analytic warm start, bisection, a capacity fallback through `cuts_for_event`, and the GFM-vs-GFL comparison; E4 excluded | `gridspine/static/gfm_tuning.py` (new), a small `judge` refactor in `frequency.py`, `gridspine/drivers/campus_study.py`, tests | **G** |
| 5 | **GFM-b: product.** Routes `GET/POST …/island/gfm-settings[/apply]` (apply writes the sidecar and the campus YAML under the edit lock and marks the study and hub stale); chat tools `campus_island_gfm_settings` (read) and `campus_apply_gfm_settings` (write, card); a `CampusIslandGfmSection` | `services/campus_island_tuning.py` (new), router, `chat_tools.py`, `catalogue.py`, frontend section, inventory | R |
| 6 | **Grid codes: IEEE 2800 / NERC** profiles beside `eu_rfg_dcc_ce` (design §7.3); the fields steady-state checks can test, the rest as "dynamic study needed" flags | `gridspine/templates/data/grid_codes.yaml`, maybe `campus_compliance.py`, tests | **G** |
| 6b | **Two misleading campus labels** (owner, 14 Oct). SITE_TRANSFO's "ADEQUATE" beside a purchase becomes "planned (placeholder rating adequate; bought as 3 × 25 MVA for N-1)" (`CampusElectricalPanel.tsx:511`). "Not yet available: island study" becomes "Cost of islanding study: not run yet", since it means I9a, not the island checks the run did complete | `CampusElectricalPanel.tsx`, `CampusIslandSection.tsx`, tests | — |
| 7 | **D1: campus run as a background job** with progress, Abort, the busy banner and `wait_for_job` (the GFM and grid-code runs need it to be usable) | `campus_electrical_service.py`, router, panel | R |
| 8 | **Data center template, multi-period.** Milestone periods with an IT-load ramp, staged candidates per period, a commercial config so the investment case runs out of the box (D4) | `project_templates/eh_templates.py`, sidecars | **G** (IC inputs) |
| 9 | D2 plan status and D12 insight rules (the spine) | new `services/plan_status.py`, `services/insights/`, router, catalogue | R |
| 10 | D9 + D3: the `datacenter-plan` workflow and the stage 2 → 3 handoff | `harness/workflows/`, EH study service | R |
| 11 | D13 + D14: highlights in every tab; follow the tab (card first, assistant when the chat is open) | frontend tabs, dock (agreed with the companion stream) | — |
| 12 | D8: structured report (executive summary, investment, reliability, grid, control, conclusion) | `services/reports/*` | — |
| 13 | D5/D6/D7 deepening, D0 small fixes, D11 end-to-end smoke | various | per slice |

The tables below (D0–D14) keep the original slice definitions.

Each slice is a small PR off master, test-first, with a release-note fragment. Gatekeeper OK is needed where
marked **G** (gridspine/ or IC engine semantics). **R** means a new route or chat tool: it runs the lock-policy,
handler-dependency, argshape and ACL suites, and regenerates the inventory.

| Slice | What | Files (main) | Gates |
|---|---|---|---|
| **D0** | Fix the small bugs found: header pill reflects study/campus jobs; "Open a Project to list its reports" with a project open; "Biggest risks" shows a non-risk; status bar `Obj: —` after Optimal; reproduce the read-only-after-create banner | frontend only | — |
| **D1** | **Campus run as a background job** with progress, Abort, the busy banner and `wait_for_job` support (the MILP job already has this shape) | `campus_electrical_service.py`, router, panel | R |
| **D2** | **Plan status** endpoint plus the `get_plan_status` tool, assembled from existing records; per-stage findings with severities | new `services/plan_status.py`, router, catalogue | R |
| **D3** | **Stage 2 → 3 handoff**: "Use this design for the electrical study" applies the EH study's build to the project, solves and saves it as a job, and doesn't mark the EH study stale when the inputs it read are unchanged | EH study service, solve path | R, maybe **G** |
| **D4** | **Investment for the case**: the template ships a commercial config (owner, POC link, a tariff from the library) so the investment case runs out of the box; a Guided **Invest** card (LCOE, NPV, IRR, payback, capex by asset, assets required); fix EH sizing to include storage and exclude the grid-supply generator | template sidecars, `eh_report.py`, Guided card | **G** (IC inputs) |
| **D5** | **Reliability trade-off**: frontier enabled for this case, each point with LOLE and asset mix; Guided card with the cost-vs-reliability curve, the chosen point and stress/VOLL sensitivities | `frontier.py`, pack defaults, card | **G** if the frontier changes |
| **D6** | **Electrical in Guided**: a card for grid-code choice, Run, and the needed assets (kind, size, cost) highlighted; carry unresolved needs into the investment case (the bug) | campus panel pieces, `results.py` | R, **G** (IC) |
| **D7** | **Control strategy**: re-check islanding after the stage-3 assets; highlight failing checks; a chat tool that reads the I4–I6 report; then (later) recommended GFM settings by bisection on droop, H and i_max | campus service, frequency driver | **G** |
| **D8** | **Structured report**: executive summary → investment case → reliability case → grid/electrical case → control case → conclusion. Each chapter takes its key results and flags from the insight rule sets (D12), so every number is computed, and the LLM writes only the prose around them (number-audited). Without a key, the chapters render from the computed summaries. The outline comes from the case definition; the existing template choice and upload are reused | `services/reports/*` | — |
| **D12** | **Insight rule sets**: `services/insights/` per results tab (KPIs, flags with severity, computed summary), `GET …/insights?tab=`, the `get_tab_insights` tool; the first tabs are adequacy/EH, investment, campus and island | new module, router, catalogue | R |
| **D13** | **Highlights in every tab**: the KPI row, flag list and summary rendered in each Results tab and the campus sections, shared by Guided and Expert, with the UX card | frontend tabs | — |
| **D14** | **Follow the tab**: a debounced tab-change event that the assistant explains, with the computed "On this tab" card as the no-key fallback | dock (companion stream), event | agree first |
| **D9** | **The `datacenter-plan` workflow** and its case file; offered on the data center template | `harness/workflows/`, case file | — |
| **D10** | **Projected load and energization**: milestone years (for example 2027/2030/2035 IT load) | depends on the owner's answer (§6 Q2) | **G** |
| **D11** | **End-to-end smoke**: a Playwright script like `smoke-reports.mjs` that drives the whole plan on the template, with a fake provider for the assistant | `frontend/scripts/` | — |

**Proposed order (the guided walkthrough first, per the owner):**
1. D1 (campus job + busy banner), D2 (plan status), D12 (insight rules) — the shared spine.
2. D9 (the `datacenter-plan` workflow) with D3 (stage 2 → 3 handoff) and D4 (investment runs on the case) — the
   walkthrough runs end to end.
3. D13 and D14 (highlights in every tab; follow the tab).
4. D8 (structured report).
5. D5, D6, D7 (deepen reliability, electrical, control); D0 alongside; D11 last; D10 when the owner decides.

## 6. Questions for the owner (sent through the coordinator)

1. **Scope of the first demo.** (a) **All five stages, thin** (Recommended): each stage runs and shows its
   headline figures and flags, so the story is complete end to end. (b) Stages 1–3 deep, 4–5 later. (c) Stages
   1–2 only first.
2. **Projected load and energization.** (a) **Milestone years as separate single-year cases** (Recommended):
   for example 2027/2030/2035, each solved and financed on its own, with a staged plan table. It needs no P5
   staged-build finance. (b) Build P5 (multi-period finance of staged builds); larger, and gatekeeper-owned.
   (c) A single design year only.
3. **Demo numbers.** (a) **Keep the template's synthetic 50 MW campus behind a 40 MW grid**, labelled
   illustrative, with placeholder library prices (Recommended). (b) The owner supplies the load projection and
   prices. (c) A public reference case.
4. **Grid code.** (a) **EU RfG/DCC as already encoded** (Recommended), with its limits named in the report.
   (b) Add a profile for IEEE 2800 / NERC (US). (c) A national code such as VDE-AR-N 4130.
5. **Report format.** (a) **docx plus PDF through the existing template upload** (Recommended); the new work is
   the five-stage outline. (b) Also HTML. (c) PDF only.
6. **Live assistant testing.** (a) **An app API key for this session's environment** (Recommended), so the AI
   guidance can be verified live. (b) The fake provider and tests only; stage claims about the assistant stay
   unverified live.
8. **Follow the tab.** (a) **On a tab switch the computed "On this tab" card updates at once; the assistant adds
   a short explanation only when the chat is open, and the user can turn that off** (Recommended): it costs no
   tokens on fast clicking, and works without a key. (b) The assistant explains every switch automatically,
   which costs tokens on every click. (c) Only an "Explain this tab" button, so nothing is proactive.
7. **Control-strategy depth.** (a) **Screening plus flags first, recommended GFM settings later**
   (Recommended). (b) Recommended settings in the first demo; gridspine work, gatekeeper.

## 7. Engine designs (for the gatekeeper's OK before code)

### 7.1 P5: multi-period finance of staged builds

**What exists.** P5 was named, not designed. The P0/P1 roadmap (`2026-09-26-edge-investment-case-p0-p1.md:692`)
gives it flex archetypes. P4 rule C2 (`2026-09-30-edge-investment-case-p4.md:57-66`) gives it per-asset CODs and
staged builds. G1/G2 (`2026-10-07-ic-g1-g2-campus-equipment.md:108-116`) refuse later campus purchases until P5.
The engine already steps period templates over years (`cashflow._templates_by_year`, C3) and holds year-indexed
arrays. Only the single COD (`timeline.py:79-84`, `finance_case._cod`) and the refusal
(`finance_case.py:1505-1516`) stand in the way.

**Two hazards on master now, fixed first (slice 1):**
- **H1.** Vintage bounds clone build-year rows for the solve and fold them back into the parent, with the
  breakdown in `n.meta["vintage_results"]` (`vintage_service.py:877-890`). The refusal reads only the parent's
  `build_year`, so a ramp built through vintages gets past it and is financed as if it were all bought in year 1.
  Fix: refuse `staged_build_vintage_bounds_not_supported` when a parent has more than one period with
  `p_nom_opt > 0`. A later slice may split it into `<parent>@<year>` assets.
- **H2.** `_served_load` (`finance_case.py:633`) reads the raw `loads_t.p_set`, so the counterfactual bill
  ignores `load_scalers`. Fix: apply `load_scale_factors(n, cfg)`. The same applies to
  `commercial/connection.py:498`.

**Design (slices 2–3).**
1. **Model.**
   - `FinanceCase.asset_cod: dict[str, date]` (in the hash). `case.cod` = the earliest.
   - `Operating.stage_capex` (array), apart from `capex`, so stage 1 is computed exactly as today.
   - `FinanceInputs.stage_capex_lead_years = 1`.
2. **Per-asset COD.** The typed date, else 1 January of `build_year`.
   - **Rule P5-a:** a later stage's COD year must equal an investment period year, because the template that
     dispatches it starts there. Otherwise refuse `staged_cod_not_period:<asset>`.
   - Campus extras with a later `build_year` become stages under the same rule, replacing
     `extra_asset_staged_build`.
3. **Templates.** C3's step function is unchanged, with no interpolation: each period is what the LP dispatched.
   A staged asset's lines and energy start in its period because PyPSA leaves inactive assets out.
   - Refuse `asset_retires_in_horizon:<asset>` when build_year + lifetime ends inside the case horizon, since a
     replacement would contradict the dispatch.
4. **Staged capex.**
   - Each stage costs Σ overnight × (1 + contingency) × (1 + r_capex)^(y − base_year), placed in
     y = COD year − lead. It must be ≥ y0. Escalation uses the `capex` class, as replacements already do.
   - Project and equity cash subtract it.
   - It is **equity-funded**: debt sizes on stage 1 only, flagged `staged_capex_equity_funded`. CFADS
     subtracts it.
   - Tax depreciates it as `vintages`, reusing the replacement path (`tax.py:250-279`).
5. **Per-asset clocks.** Degradation operating year k, replacement schedule (`replacements._generated`),
   `remaining_life_terms`, `asset_lifetime_short` and the LCOS capex share all run from the asset's own COD.
6. **Metrics.**
   - NPV, IRR, payback, LCOE and LCOS use the same year arrays; there are no new formulas.
   - `payback_sustained` is reported, because mid-life capex makes it meaningful.
   - DSCR, LLCR and PLCR stay stage-1 debt metrics.
7. **Still refused:**
   - an asset in service before the first period;
   - a stage COD that is not a period year;
   - a network retirement inside the horizon;
   - incentives, ITC/PTC or dated tax schedules on a later-stage asset (`staged_incentive_not_supported:<a>`),
     because `case.cod` drives eligibility;
   - debt on later stages;
   - vintage-bound parents until they are split;
   - myopic-freeze results;
   - interpolation between periods.
8. **Tests.**
   - Rewrite the four pins: `test_finance_case_adapter.py:590-614`, `test_finance_case_extra_assets.py:371-379`,
     `test_finance_cashflow.py:139-143,219` and `test_finance_case_upfront_parts.py:259-270`.
   - New `test_finance_staged.py`: a two-stage hand case checked against hand-computed numbers (capex year,
     escalation, vintages, degradation k, replacements, terminal value), every refusal, and H1/H2 regressions.
   - One live-solve staged data center case: 2027, 2030 and 2035 with a load ramp.

### 7.2 Recommended GFM settings

**What exists.**
- `frequency.py` screens fixed settings: k = P_n/(R·f0); inertia from a genset's H, a VSM's H_v, or τ_f·P_n/(2R)
  for a droop GFM; i_max as a P cap.
- `cuts_for_event` inverts a failing event into capacity: ΔM, ΔK and headroom.
- The island schema has lower bounds only (`gridspine/schema/island.py:102-130`).
- Nothing tunes the settings.
- E4 (`e4_ups_bridged`) uses gensets only (`frequency.py:651`), so GFM settings cannot change it, and the
  recommender skips it. This also keeps the recommender away from the slow code the gatekeeper is looking at.

**Design (slices 4–5).**
- **Module.** A new module `gridspine/static/gfm_tuning.py` with
  `recommend_gfm_settings(cases, limits, *, bounds=GFM_BOUNDS, margin, search=(5e-3, 10), verify=(1e-3, 20)) -> Recommendation`.
- **Cases.** E1/E2/E3/E5 (seamless) and E6 (planned) at the island hours, built like `_event_run` and
  deduplicated on `(repr(units), dp)`.
- **Bounds (ledgered as assumed).**
  - droop 1–10 %;
  - H_v 0.5–10 s;
  - τ_f 0.01–1 s;
  - i_max fixed at its current value, because raising it is hardware and so capacity. With `allow_i_max`, up
    to 1.5 pu.
- **Margin.** Judge against limits shrunk by `gfm_margin`. A `_checks()` helper is refactored out of `judge`
  with no change in behaviour, so a slack value can be reported.
- **Search.** One setting per unit across all hours, since these are firmware constants, with common scalars
  across GFMs so they keep sharing proportionally:
  1. If the current settings pass with margin, keep them.
  2. Warm start analytically: K_req = ΔP_max/Δf_allowed·(1+m) − K_gensets; M_req = f0·ΔP/(2·RoCoF·(1−m)) −
     M_gensets; cap H by 0⁺ inertial saturation.
  3. Bisect droop on the steady checks.
  4. Bisect H on the transient checks.
  5. At most two rounds.
  6. Verify once at the study's step.
  - Expected cost: about 32 short runs per case at about 0.06 s each, so seconds to a minute.
- **Fallback.** No in-bounds setting passes → best-bounded settings plus `cuts_for_event` rows ("settings
  exhausted; capacity needed → I7").
- **Comparison.** Each converter run as gfl_fw, gfm_droop or gfm_vsm at the recommended settings: RoCoF,
  nadir, qss, slack and saturation.
- **Output.** A `Recommendation`: settings per unit (current → recommended), case results before and after,
  the binding case, `achievable`, capacity rows, the comparison, and the label "heuristic screening".
- **Product (slice 5).**
  - Run on demand, not inside the campus run.
  - "Apply" writes the sidecar **and** the campus YAML island blocks (the run reads the YAML,
    `campus_electrical_service.py:1160-1175`) under the edit lock.
  - Apply marks the island study stale, and the hub too, because droop, H_v and τ_f change the I2b inertia
    and droop rows.
  - Chat tools: a read tool and a write tool with a confirmation card.
- **Demo note.** The template runs only `ups_bridged`, so GFM settings matter only once **seamless** is on.
  The template notes say a 30 MW BESS cannot form a seamless island, so the demo will most likely show "no
  in-bounds setting passes → capacity needed" first, and pass after I7 or a larger BESS. That is the honest
  result; it is not to be tuned away.
- **Tests (`tests/gridspine/test_gfm_tuning.py`).**
  - A passing island is left unchanged.
  - Closed forms: H_v for a RoCoF-only case, and droop for a qss case.
  - Bounds and the saturation cap hold.
  - Saturation with a fixed i_max gives `achievable=False`, with headroom equal to `cuts_for_event`.
  - Final settings pass `judge` on the shrunk limits at the study step.
  - E4 is never called.
  - The `simulate` call count stays bounded.
  - GFL → GFM improves RoCoF.

### 7.3 IEEE 2800 / NERC grid-code profiles

**What exists.**
- Profiles in `gridspine/templates/data/grid_codes.yaml`, loaded by `gridspine/templates/grid_codes.py`, carry four
  limit kinds:
  - `voltage_bands` by kV;
  - `q_range_demand` (Q/P_ref of the site's import capacity);
  - `rvc_limit_pct`;
  - `campus_voltage`.
- Each limit carries a clause and a source tag (`code | assumed | extracted`).
- The loader ignores unknown top-level keys, so a new field loads silently and nothing reads it.
- The GUI upload extracts and confirms only the four kinds.
- Island frequency limits (RoCoF, f_min/f_max, qss) live in the island requirements, not in the profile.

**What the standards say** (sources gathered 14 Oct; full table and URLs in the session's research notes):
- **IEEE 2800-2022 covers IBR plants on transmission and sub-transmission, not loads.** It applies where a TSO
  or ISO adopts it.
  - Reactive capability: ±32.87 % of the IBR's ICR (about ±0.95 pf) at the RPA (default: the POM).
  - Continuous voltage: 0.90–1.05 pu, with 1.05–1.10 for 30 min.
  - RoCoF ride-through: 5 Hz/s.
  - PFR: 5 % droop, 0.036 Hz deadband (secondary source).
- **NERC PRC-029-1 (FERC Order 909): IBR ride-through.**
  - Frequency: 58.8–61.2 Hz continuous; 299 s beyond that; may trip past 61.8 / 57.0.
  - Voltage (Table 2): 0.90–1.05 continuous, tiered below and above.
  - It covers BES IBR and non-BES IBR ≥ 20 MVA on a delivery system ≥ 60 kV. A behind-the-meter BESS or PV at a
    data center is **probably out of scope (unverified)**.
- **NERC PRC-024-3:** generator protection no-trip zones; a settings audit, not a load-flow check.
- **Large loads (data centers): no binding NERC ride-through standard yet.**
  - There is a Level 3 alert (May 2026).
  - Project 2026-02 drafts CLO-001/002/003 (interconnection, studies, modelling), not ride-through.
  - FERC ordered the standards filed by 31 Dec 2026.
  - Binding load ride-through today is regional: ERCOT NOGRR282 / NPRR1308 from 1 Aug 2026; PJM is filing.
- **Unverified:** the 2800 VRT tiers, the PRC-029 effective date, the 2800 FRT table beyond NERC's "in alignment
  with" statement, the BES exclusion for behind-the-meter IBR, and the ERCOT curve values.

**Design (slice 6).** One new profile, `us_ieee2800_nerc`, beside `eu_rfg_dcc_ce`, honest about what binds a data
center:

| Field | Value | Tag | Note |
|---|---|---|---|
| `voltage_bands` ≥ 69 kV | 0.90–1.05 continuous, with the new optional `v_max_30min` 1.10 | `code` (2800 cl. 7.2; PRC-029 Table 2) | the 30-min tier is reported, not enforced, by the existing checks |
| `voltage_bands` 0–69 kV | 0.90–1.10 | `assumed` | NERC sets no steady-state band; the TO's FAC-001 criteria do |
| `q_range_demand` | 0.329 (±0.95 pf) on the site's import | `assumed` | 2800/Order 827 state this for the IBR at ICR, not the load; the clause says so |
| `q_range_ibr` (new, optional) | 0.3287 of on-site IBR ICR at the POM | `code` (2800) | a small new check in `campus_reactive.requirement_from` / `connection.py`: the BESS+PV's Q capability against ±32.87 % of their rating |
| `rvc_limit_pct` | 3 % | `assumed` | no US code value found |
| `dynamic_requirements` (new, optional, data only) | 2800/PRC-029 FRT 58.8–61.2 Hz, the VRT table, RoCoF 5 Hz/s, PFR 5 %/0.036 Hz, PRC-024-3 zones, large-load VRT (pending, regional) | cited per row | listed in the results and report as **"dynamic study needed"** flags, never as passed checks |

- New fields are optional, so `generic_assumed`, the GUI template, extraction and confirmation keep working.
  Extraction of the new kinds comes later.
- Each new profile carries a 0 kV band (`test_campus_grid_code.py:429`).
- `test_connection.py:153`, which pins exactly two profiles, is updated to the three.
- The same `dynamic_requirements` list is added to `eu_rfg_dcc_ce` (RfG FRT/LFSM, RoCoF), so both codes report
  what a steady-state study cannot show.

### 7.4 Gatekeeper review (14 Oct): changes folded in

- **Slice 1:** name the check by build year; test that a single build active across several periods is not
  refused; test served = scaled demand − shed. The FCA ranking is slice 1b.
- **P5:**
  - P5-a becomes "COD year == the asset's first active period" (`staged_cod_not_active_period:<asset>`, giving
    both years).
  - Equity-funded stage capex goes into project and equity cash only; CFADS, DSCR, LLCR and PLCR are unchanged,
    with a test that DSCR in a stage year does not change.
  - `asset_retires_in_horizon` applies only inside the modelled periods.
  - Tax depreciates each stage from its own COD.
  - A lead before y0 is refused, not clipped.
- **GFM:**
  - Fixed horizons only (no `_settle_run`).
  - A hard cap on `simulate` calls, reported, with a test asserting the exact count.
  - The recommender runs as a background job on the solver's job/progress path.
  - The `_checks()` refactor must leave `judge` output unchanged on the frequency suite.
  - It merges after (or rebases on) I6's E4 fix in `frequency.py`.
- **Grid codes:**
  - `q_ibr_lead`/`q_ibr_lag` rows in `gridspine/schema/connection.py` CHECKS, not `campus_compliance`.
  - A known-key test for the built-in profiles; the loader warns on unknown keys.
  - `reported` status for `v_max_30min` and `dynamic_requirements`, kept out of every verdict and count.
  - The PRC-029 scope caveat goes in the profile notes.
  - A byte-identical connection-table test for the two existing profiles.
- **UX split (agreed with the UX session):**
  - Stage ids are `investment`, `grid_plan`, `reliability` and `operation` (label "Operation & control").
  - KPI payloads use its `ResultState` enum (`ready | zero | not_run | not_modelled | not_applicable | stale`)
    with `reason` and `action`, in place of a boolean Unavailable.
  - Flags render through its `Highlight`; an `Explain` callout with `origin: computed | assistant` is requested.
- **G5 (UX session):** Guided says "certified" while Expert says "Not solved yet". This is the stage 2 → 3 handoff
  (slice 10). How `ResultState` shows "the study ran, the project is not solved" is to be agreed with the UX
  session.
