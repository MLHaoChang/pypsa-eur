<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# pypsa-gui UX audit: the novice path from opening the app to an investment conclusion

Scope: `pypsa-gui/frontend/src` (React/TS) and `pypsa-gui/backend` (FastAPI), read-only. All paths below are relative to `/home/user/pypsa-eur/pypsa-gui/` unless noted. Quotes are verbatim UI copy.

**Persona:** an engineer or consultant who wants to answer "Should I invest in a BESS at my site, and what is the ROI?" They have little background in LP/PyPSA or in finance modelling.

**Verdict in one paragraph:** The app is a capable, carefully built **modelling workbench**. It does not provide an **investment study workflow**. The user has to know PyPSA's object model (bus, StorageUnit, `p_nom_extendable`, `capital_cost` as an annuity, `max_hours`, `marginal_cost` time series as a price) and assemble the question out of 15+ equal-weight panels. No surface states the question or the baseline, and nothing reports ROI, NPV, IRR or payback. There is no narrative conclusion in the UI. The adequacy/FMEA and Energy-Hub surfaces already contain a mature **"evidence with honesty"** pattern. The chat-only `build_study_report` and `start_campaign` tools and the Model Horizon guided steps are the reusable pieces a guided study workflow can generalise.

---

## 1. Information architecture

### 1.1 Routes (react-router, `frontend/src/routes.tsx`)

| Route | File | Purpose |
|---|---|---|
| `/login`, `/set-password`, `/reset-password` | `pages/auth/*.tsx` | Auth. The hero reads "Advanced modelling for the energy portfolio … from a single asset to an entire continent" (`pages/auth/AuthSplitLayout.tsx:63`). |
| `/` → `/projects` | `pages/ProjectsHomePage.tsx` | Workspace home: resume card, "Start a project" (4 cards), project list, import. |
| `/app` | `App.tsx` | The single workbench. Everything below lives here and is switched by UI state, not by routes. |
| `/admin/*` | `pages/admin/*` | Org/user admin (RequireAdmin). |

The workbench has **no deep-linkable routes**. The panel choice is `useUIStore.activeSlidePanel` (`store/uiStore.ts:42`), and the Results sub-tab is kept in localStorage.

### 1.2 Workbench layout regions (`App.tsx:560-671`)

- **Banners:** CrashRecoveryBanner and LockBanner.
- **AppHeader** (`layout/AppHeader.tsx`): project name, save state, and the smart **"Run LOPF"** button (`runLabel`, :557). The button enqueues into the solve queue.
- **ProjectTabs** (`layout/ProjectTabs.tsx`): browser-style tabs for open projects.
- **Zone 0, Sidebar** (`layout/Sidebar.tsx`, 1,938 lines). Three collapsible sections plus the Assistant:
  - **Project:** Project info, Save, Snapshots, Scenarios, Duplicate, Export bundle, Projects home, Workspace.
  - **Data:** Assets palette (~20 items in 6 groups: Network / Electricity / Hydrogen / Heat / Storage / Demand), Time Series.
  - **Simulation:** Solver Settings, Settings, Model Horizon, Capacity Bounds, Issues (badge), Results, Solve Queue, Planning → dynamics.
- **Zone 2, Canvas:** `TopologyCanvas.tsx` (React Flow, 3,419 lines) or `MapCanvas.tsx` (Leaflet), switched by `MapModeSwitcher`. `SnapshotPicker` is overlaid for the results playback.
- **Zone 4, BottomPanel** (`layout/BottomPanel.tsx`): 11 tabs. `Log, History, Buses, Lines, Transformers, Generators, Storage, Stores, Loads, Links, Carriers` (:37). These are spreadsheet grids of raw PyPSA columns.
- **Zone 3, PropertiesPanel** (`layout/PropertiesPanel.tsx`, 2,604 lines): the per-asset form. It is hidden while any slide panel is open.
- **Slide panel:** opens half-width beside the canvas, or full-screen for `results`, `timeseries`, `capacityBounds` and `gridspine` (`App.tsx:119`). It carries a breadcrumb eyebrow (PROJECT / DATA / SIMULATION / APPLICATION).
- **Zone 5, AssistantDock** (`components/AssistantDock.tsx` + `ChatPanel.tsx`, 3,027 lines): a persistent right dock. It is deliberately not a slide panel.
- **Global overlays:** StatusBar, CommandPalette (⌘K / ⌘P), ShortcutsHelp (`?`), RescaleDialogHost.

### 1.3 Top-level screens (slide panels, `App.tsx:99-139`)

| Panel id | File | One-line purpose |
|---|---|---|
| overview | `pages/OverviewPanel.tsx` | "Project info": stat cards (buses, lines, links, capacity, LP **objective**), capacity mix, recent activity. |
| snapshots | `pages/SnapshotsPanel.tsx` | Saved project checkpoints ("Network snapshots"). This name is overloaded with time steps; CONTEXT.md itself calls it "the defect". |
| scenarios | `pages/ScenariosPanel.tsx` | Scenario tree (projects with a parent). Branch, queue solves for a subtree, pick two to compare. |
| compare | `pages/CompareView.tsx` (3,435 lines) | A-vs-B comparison of two saved projects, 10 tabs. Now mostly used as a rail inside Results. |
| timeseries | `pages/TimeSeriesManager.tsx` (2,064 lines) | Profiles by category: Loads `p_set`, Renewables `p_max_pu`, Conventional / DR / Links with `p_max_pu`/`p_min_pu`/`marginal_cost` sub-attributes. Upload, templates and synthetic shapes. |
| simparams | `pages/SolverSettings.tsx` (2,602 lines) | "Solver & mode", 5 tabs: General / Solver / Dispatch / Network / Add. Constraints. Economics, VOLL, ENS target, reserve margin, DSR, SCLOPF. |
| horizon | `pages/ModelHorizon.tsx` + `pages/modelHorizon/*` | **Guided steps** (see §2.4): Mode, Investment years, Economics, Snapshot window, Representative weeks, Snapshot weightings. |
| capacityBounds | `pages/CapacityBoundsEditor.tsx` | Per-technology and per-vintage min/max build limits. |
| issues | `pages/IssuesPanel.tsx` | Preflight errors and warnings, "View" jump-to-component, one quick-fix (zero-CO₂ carrier), last solve failure card. |
| results | `pages/Results.tsx` | 13 result tabs plus a docked Compare rail (§4). |
| solveQueue | `pages/SolveQueuePanel.tsx` | Background FIFO of saved-project solves. |
| gridspine | `pages/GridspinePanel.tsx` | "Planning → dynamics" study view: stage list, ranked extreme hours, assumptions ledger, PowerFactory hand-off and read-back. |
| workspace | `pages/WorkspacePanel.tsx` | Ownership, edit lock, members. |
| settings | `pages/LocalSettings.tsx` | API key, assistant model, app log. |

**Results sub-tabs** (`pages/Results.tsx:64-76`): Overview (multi-period only), Capacity Expansion, Dispatch (**the default**, `loadInitialTab` :57), Load Flow, Prices, Economics, Emissions, Curtailment, Lost load, **Adequacy**, Storage cycling, **FMEA**, Asset Detail.

**Adequacy tab sub-panels** (`pages/results/AdequacyTab.tsx`): AdequacyChips, CoptChips, ReserveMarginPanel, FrontierPanel, McPanel, LoopPanel, MarginLoopPanel, EhReferenceDesignPanel. That is 8 stacked surfaces on one scroll.

**Modals** (the `Dialog` primitive from `components/Dialog.tsx`, 14 usages in 12 files): NewProjectWizard (6 tabs: Blank / Template / File / Clone / Folder / Study), ConfirmDialog, VintagePeriodBoundsModal, RescaleDialog, AssignMembersDialog, ShortcutsHelp, CommandPalette, and dialogs inside ScenariosPanel, SnapshotsPanel, TopologyCanvas and ProjectTabs.

**Rough reachable-surface count:**
- 14 slide panels
- 13 Results tabs, plus 8 Adequacy sub-panels
- 10 Compare tabs
- 11 bottom-grid tabs
- 5 Solver Settings tabs
- 6 Model Horizon steps
- ~5 Time Series categories × up to 3 attributes
- ~8 modal types
- 1 Assistant dock and 1 command palette

That is roughly **80 or more distinct panels, tabs and modals**, all shown with equal visual weight. None of them is ordered or gated by the user's question.

**Dead code:** `pages/ResultsViewer.tsx`, `LoadProfileManager.tsx`, `GenerationStack.tsx` and `LoadEditor.tsx` are imported nowhere. They are only named in ChatPanel's panel alias map, and should be deleted or wired up before any redesign.

---

## 2. Onboarding and guidance

### 2.1 What exists

- **Projects home** (`pages/ProjectsHomePage.tsx:78-81`) has four start cards: "New project: Start from an empty network", "From template: Bundled starter networks", "Import from disk: .pypsaproj.zip or .nc", "Duplicate a project".
  - The copy speaks about the architecture, not about goals: *"Creating, importing and duplicating all live here rather than in the workbench — a project belongs to your workspace, not to whichever network happens to be open."* (:500)
- **Templates** (`layout/NewProjectWizard.tsx:55-63`): 3-Bus Tutorial ("Best starting point for learning PyPSA"), IEEE 14-Bus, Belgium Grid (PyPSA-Eur), and IEEE 39-Bus (gridspine).
  - **All four are transmission-grid test cases.** None is a site, microgrid, behind-the-meter, BESS or hybrid-PV template.
  - Footer copy exposes a dev path: *"Template networks are bundled in backend/project_templates/."*
  - `network.nc` files are gitignored and only built by `build-macos.sh`. In a `start.sh` or dev run, every card returns the 404 *"Template '3bus' is registered but its network.nc is missing — run project_templates/_build.py to regenerate it."* (`backend/routers/projects.py:1306-1309`). The "soon" badge exists but `available: true` is hard-coded.
- **Blank project empty state:** the canvas says *"No buses — import a network or add buses using the toolbar"* (`pages/TopologyCanvas.tsx:3149`). The Properties panel says *"Click a bus, line, or asset on the canvas to inspect it"* (`layout/PropertiesPanel.tsx:2531`). There is no "what do you want to study?" prompt.
- **Assistant launch greeting** (`components/ChatLaunchGreeting.tsx`): local, key-free orientation, e.g. "You're in X · N buses · N snapshots · Not solved yet / results are stale". Starter chips (`ChatPanel.tsx:1071-1096`) contain literal placeholders: *"List my projects and open project_name"*, *"Compare scenario_a vs scenario_b on total cost…"*, "Open the Results Economics tab", "Summarize the key results…". A missing API key is shown as a quiet offer ("Add an Anthropic API key to talk to me"). Without a key the copilot cannot answer questions or run its tools.
- **Missing entirely:** a product tour, coach marks, first-run checklist, glossary, a "workflow" or progress indicator, and example *studies* (as opposed to example networks). A grep for onboard/tour/glossary/walkthrough/novice/beginner/expert mode finds nothing in the frontend. The 7-step "typical workflow" exists only in `README.md` ("A typical workflow").

### 2.2 Inline help, tooltips, units

- **Property tooltips** (`utils/propertyDocs.ts`, 260 lines) appear through the (?) `InfoTip` in `layout/properties/cardKit.tsx`. The file header states the intended audience outright: *"The primary audience is a power-systems engineer using the GUI, so we use PyPSA's terminology rather than re-explaining basics."* (:7-9)
- **Units exist on most inputs** but are inconsistent:
  - Generator create form uses **`$/MWh`, `$/MW`** (`layout/CreationForm.tsx:61-62`). Everything else is €.
  - The Properties "Capital cost" unit badge says **`€/MW`** (`PropertiesPanel.tsx:413,676`). Its own tooltip says *"Annualised investment cost per MW of added capacity (€/MW/yr)"* (`propertyDocs.ts:85`). A novice will type an overnight €/MW figure into an annuity field.
  - Asset Detail uses "EUR/a" while the rest of the app uses "€/yr".
- **Contextual reveal rules** (D22, `utils/attributeCatalog.ts`, tested in `utils/revealRules.test.ts`): ticking "Extendable" reveals `p_nom_min`/`p_nom_max`. This is the only field-level progressive disclosure.
- **`<details>` "Advanced" / "Methodology" disclosures** are used in SolverSettings, ModelHorizon StepShell, Economics and LoadFlow.

### 2.3 Level of expertise assumed (verbatim samples)

- **Button:** "Run LOPF". A hover tooltip explains it (`AppHeader.tsx:549-557`).
- **Properties labels:** "p_nom", "p_nom_min", "p_nom_max", "p_max_pu (static)", "p_min_pu", "e_nom (nominal)", "e_min_pu (SoE min)", "Cyclic SoE", "Max hours", "η store", "η dispatch", "Standing loss", "Build year", "FOM cost", "Overnight cost", "Committable", "Min up", "Sub-network", "v_nom₀ (HV)".
- **Creation form:** "P nom", "Max hours", "η store", "η dispatch". A battery **cannot be made extendable or given any cost at creation** (`CreationForm.tsx:65-73`).
- **Asset Detail metrics** (`backend/services/asset_results/registry.py:178-181`): "μ upper", "μ lower" (raw duals).
- **Solver Settings:** "Value of Lost Load (VOLL)", "ENS target" with unit "**‱ of demand**", "Per-zone ceiling multiple", "Reserve margin", "DSR volume", "Enable SCLOPF", "HiGHS user_objective_scale", "MIP rel. gap", "Crossover", "NumericFocus".
- **Discount-rate hint** (`SolverSettings.tsx:1494`): *"Used to annualise CAPEX via the annuity factor r(1+r)^L / ((1+r)^L−1). Applied to extendable assets whose overnight_cost is set…"*
- **Adequacy empty state** (`results/AdequacyTab.tsx:83-89`): *"Set `ens_cap_permyriad` in solver settings and re-solve"*. Settings labels this field "ENS target", not by the raw name.
- **Compare subtitles** (`CompareView.tsx:505`): *"Σ p_nom_opt × annuitised_capital_cost × ipw.years[P]. Matches the live Results panel."*
- **Model Horizon Mode step:** *"Enables PyPSA's multi-horizon LP…"* (`modelHorizon/StepMode.tsx:31`).
- **EH panel:** *"Runs an Energy Hub archetype pack through the reference-design pipeline (apply pack → ENS solve → frontier → MC LOLE certify → FMEA top-N → assemble…). Produces one `ReferenceDesignReport`…"* (`results/EhReferenceDesignPanel.tsx:547-553`).
- **README features:** "negative `efficiency2` for the cold side", "SCLOPF", "vintage", "foresight mode (overnight / myopic / perfect)".

### 2.4 The Model Horizon guided steps (the one existing wizard-like pattern)

Spec: `/home/user/pypsa-eur/docs/superpowers/specs/2026-08-12-model-horizon-guided-steps-design.md`. Plan: `.../plans/2026-08-12-model-horizon-guided-steps.md`.

Implementation:
- `pages/modelHorizon/StepShell.tsx`: numbered rail with `aria-current="step"`, a step title such as "Step 3 of 6 — Economics", and a controlled `<details>` "Advanced" disclosure that closes on every step change.
- `HorizonSummary.tsx`: the summary-first landing, one clickable sentence per step.
- `modelHorizonModel.ts`: pure `stepSummary`, `visibleSteps` and `isHorizonUnset`.

Design choices worth carrying forward:
1. **Two entry states derived from the model, not from UI state.** An unset horizon (`snap.count <= 1`) opens at step 1; otherwise the summary opens.
2. **A summary of current state as sentences**, e.g. "Multi-period, 3 investment years".
3. **Per-step Apply, not a transactional wizard.** Stated reason: *"that needs transactional support the backend does not have"*.
4. **Hide depth within a job, not whole jobs.** *"nothing here can be buried behind an 'advanced' flap on the grounds that it is rarely used"*.
5. **Visibility predicates:** single-period users see 4 steps and multi-period users see 6.
6. **Group steps by the model's structure** (period level vs timestep level) *"teaches the underlying model"*.

The spec explicitly rejected the SolverSettings tab pattern: *"six equal doors give a first-time user no ordering"*. That criticism applies to the whole app shell.

Limits: there is no completeness state on the rail (no checkmarks or "needs attention"), and the steps still use raw vocabulary ("Snapshot weightings", "objective").

### 2.5 Validation and preflight phrasing

**Preflight** (`backend/services/validation_service.py`, 2,450 lines) returns error/warning `Issue(code, component, name, msg)`. The Issues panel shows each issue with a "View" jump. The sidebar polls every 30 s for a badge, and a failed fetch shows "?" rather than 0 (ADR-0001).

Messages are precise but written in PyPSA/solver language:
- *"snapshot_weightings['objective'] has 3 NaN/inf value(s)."*
- *"SCLOPF doesn't support `transmission_losses=True` — PyPSA's secant-loss formulation isn't wired into the BODF-based contingency LP."*
- *"Disable `run_ac_pf_after_lopf`…"*
- *"promote snapshots to multi-period under Snapshots → Multi-period first"*. This is a stale navigation path; the toggle now lives in Model Horizon → Mode.
- *"PyPSA cannot solve unit-commitment AND capacity-expansion in the same generator."*

Stale cross-references also appear in UI copy:
- `SolverSettings.tsx:1568` says *"Toggle Multi-investment periods in General"*, but General has no such toggle.
- The VOLL note (:1685) says lost load surfaces *"in the Results → LoadFlow tab"*, but a dedicated "Lost load" tab exists.

**Good plain-language examples:**
- `backend/services/timeseries_qa.py`: *"…The solve will succeed and the result will be silently meaningless — check the column that was uploaded."*, *"…usually a misplaced decimal point — and the peak is what sizes the fleet"*, *"…this series may be in kW"*.
- `backend/services/failure_taxonomy.py`: *"No feasible solution — …demand exceeds available generation + import capacity in some hour… Relax the binding limit — or set a Value of Lost Load (VOLL)…"*. After a failed solve the Issues panel opens automatically and a toast appears (`AppHeader.tsx:626-638`).

**Missing checks a BESS novice needs:**
- Storage in a 1-snapshot model: a new blank project has the single default "now" snapshot. No warning says storage cannot shift energy.
- No extendable asset, so "nothing to optimise".
- No price signal at the storage bus.
- Capital cost that looks like an overnight figure entered in the annuity field.

---

## 3. The FMEA / adequacy / EH guidance pattern (the part "done a bit")

### 3.1 Surfaces

- **`pages/results/AdequacyTab.tsx`**
  - Header copy: *"The engines answer different questions about the same system — where they disagree is the diagnostic, not a bug."*
  - An invariant: **no early return**. Every panel mounts and "states its own empty case in its own words".
  - Panels are ordered as an **analysis order**: standard that bound → screening → firm-capacity convention → frontier → sampler → loops → EH package.
- **`adequacy.tsx` `AdequacyChips`:** chips such as "standard: ENS cap", "ENS 1.2 / cap 3.4 MWh", "shed-hours", "binding period(s)", and a warning chip "zones unpopulated". Each carries a **fidelity tooltip**: *"LP proxy (deterministic, perfect foresight, one realisation) — a relative diagnostic, NOT comparable to a statutory reliability standard."*
- **Input guard:** `ensTargetWarning` flags an implausible input and explains the consequence: *"…A generous target yields a cheap-looking, badly under-built plan."*
- **`RESERVE_MARGIN_CAVEAT`** (`adequacy.tsx:64`): one shared string, shown both where the value is entered and where it is read back, because *"two copies of a caveat drift"*. Text: *"A met margin is NOT a met reliability target… Run the sequential Monte Carlo on the resulting plan for a number that can."* The caveat ends with a next action.
- **`FmeaTab.tsx`**
  - The worksheet mixes engine rows (regenerated) with expert class-D rows (persisted in a sidecar). Each row has an **engine/provenance badge** (`EngineBadge`, :37) with fidelity tips.
  - The only editable cell is mitigability, and it survives re-solves.
  - Empty state gives the next action: *"No failure modes yet. Computed rows appear once generators carry outage data (Properties → Adequacy); expert rows can be added below."*
  - Partial-result honesty: *"The contingencies never reached are absent, not harmless."* and *"The sweep's closing re-solve did not restore your plan… Re-run your solve."*
- **`MarginLoopPanel.tsx` / `LoopPanel.tsx`**
  - Status chips: met / unreachable / budget_exhausted / failed / aborted (`backend/services/adequacy/coupling.py:412-518`).
  - A "confident" chip carries the tooltip *"The 95% CI upper bound also clears the target…"*.
  - Ceiling, solve and "restored"/"NOT restored" chips; a backend-authored `verdict` sentence.
  - The pre-run cost disclosure states that no result is not the same as a zero result: *"Nothing below is a result of zero: there is no result. A run costs one probing solve plus up to max_solves full capacity expansions…"*
  - Bug: the literal `{'max_solves'}` at `MarginLoopPanel.tsx:346` renders the placeholder text "max_solves" instead of a number.
- **`EhReferenceDesignPanel.tsx`** (collapsed by default under "Reference design ▸")
  - **Archetype picker** with blurbs ("Strong grid / Weak-flexible / Off-grid"). Run and Abort buttons.
  - Headline chips: ENS cap, achieved, Cost@target, LCOE, LCOH, MC LOLE, and a **verdict** ("certified" / "certification failed" / "LOLE reported (no target)" / "certification not established").
  - **Completeness chips** per section (`target, certification, cost, frontier, sizing, redundancy, levers, dtc, fmea_top, tea, gates, multi_energy`), each `ok | not_established | skipped` (spec `docs/superpowers/specs/2026-09-14-eh-reference-design.md` §4).
  - A "Dynamics gate" block (SCR pass/fail, EMT recommended). Sibling tables with CSV export. Abort-partial messaging.
- **`GridspinePanel.tsx`:** an ordered **stage list** with per-stage state tags (pending/running/done/failed/aborted), progress counts ("n/N hours"), and an **assumptions ledger** with provenance counts: "measured / datasheet / assumed".

### 3.2 The chat-only report layer (the most mature "conclusion" machinery, with no UI)

`backend/services/adequacy/study_report.py` (the `build_study_report` chat tool) returns:
- `objective`, `campaign`
- `sections[]`, each with its own engine and fidelity
- **`required_disclosures`**: "sentences your prose MUST contain"
- **`not_established`**: "a report that omits what it did not measure reads as though it measured it"
- **`evidence_gaps`**: things that undermine the whole document, which "belong BEFORE the numbers"
- `writing_note`

`start_campaign` (`backend/services/chat_tools_schema.py:1063`) opens "one solve budget across a whole chain of studies" and asks the model to "state the objective in the user's terms". `explain_investment` (:856) returns a `binding_constraint` (not_solved / not_extendable / at_upper_bound / at_lower_bound / not_built / interior) plus `reading_notes`. One of those notes is the essential ROI caveat, *"an extendable asset at an interior optimum earns approximately zero net profit BY CONSTRUCTION"* (`backend/services/chat_tools.py:4252`). **The note never appears in the Economics tab or in Asset Detail.**

### 3.3 Reusable pattern (to generalise to the whole tool)

1. **Objective first:** state the question in the user's terms (the `start_campaign.objective`).
2. **Archetype or template pick with a one-line blurb:** the EH `ARCHETYPES`.
3. **An ordered stage pipeline with per-stage status:** pending/running/done/failed/aborted/skipped (EH pipeline, gridspine `Stages`).
4. **Completeness ledger:** `ok | not_established | skipped` per report section. "Not run" is never shown as zero (ADR-0001 generalised).
5. **Verdict chip plus one verdict sentence:** met / unreachable / certified / not established, tone-coded.
6. **Fidelity and provenance badge on every number:** engine + fidelity tooltip; the measured/datasheet/assumed ledger; expert rows that "never impersonate an engine".
7. **Honesty notes co-located with the number, one shared string for input and readback, each ending in a next action.** Examples: RESERVE_MARGIN_CAVEAT, ensTargetWarning.
8. **Pre-run cost disclosure and budget:** the solve count, "holds the network throughout", Abort that "stops at next stage boundary", partial-result messaging.
9. **Restore guarantees:** "restored" vs "NOT restored — the network you are holding is the last iterate".
10. **Report assembly:** `required_disclosures` / `not_established` / `evidence_gaps` before numbers. It is currently chat-only and should become a first-class UI "Findings" page.

Weaknesses of the current implementation, which make it the wrong visual model to copy verbatim:
- It is extremely dense. The Adequacy tab has 8 stacked panels in 10-11 px text.
- The chip labels are engine vocabulary ("m*", "‱", "COPT", "DtC", "fmea_top: skipped", "SCR").
- The EH panel is hidden at the bottom of a Results sub-tab.
- Completeness chips show raw section ids.
- None of it is reachable before a solve exists in the IA, and it is reliability-only, with no financial ROI at all.

---

## 4. Results and reporting: from solved results to a conclusion

**After a successful solve:**
- The Run button shows "Optimal". Result queries are invalidated.
- Results does **not** open automatically and **no success toast** appears (`AppHeader.tsx:611-622`). A failed solve does auto-open Issues.
- The Results header shows *"Optimization results — Capacity expansion, dispatch, load flow, prices, and emissions from the last solve."* plus `condition · solve_time` in mono (`Results.tsx:420-431`).
- **There is no headline or answer card.** The default tab is **Dispatch**, and last-used is remembered.

**KPI panels:**
- **Dispatch** (`results/Dispatch.tsx:1316-1420, 2535-2599`) has per-carrier KPI strips: total demand, generation by type, storage charged/discharged, curtailment, lost load, OPEX, "CAPEX (annuitised)". The hints are formulas, e.g. *"Σ p_t × weighting over thermal generators"*.
- **OverviewPanel** has a StatCard for the LP "Objective".
- **Economics** (`results/Economics.tsx`) shows per-asset annualised revenue, charge cost, VOM, fixed cost, net profit and LCOE/LCOS, grouped Thermal / Renewables / Storage / Converters. A `<details>` "Methodology" gives the formulas.
  - Storage "revenue" is discharge × **nodal marginal price** (the LP dual), not tariff savings.
  - **No NPV, IRR, payback, ROI, cash-flow or lifetime view anywhere.** A grep for npv/irr/payback/roi across backend and frontend matches only one comment.
- **Asset Detail** (`results/asset/*`) lists per-asset metrics: capture price, revenue, net profit, LCOE, and "μ upper/lower". It exports XLSX and PNG.

**Compare:**
- CompareView is two saved, solved projects side by side (10 tabs) with Δ values such as "Gen cost", "OPEX", "CAPEX", "LCOE" (`CompareView.tsx:1796-1808`).
- It opens from Results ("Compare" rail) or from Scenarios ("Compare these two"). Adequacy and FMEA have no compare tab; they alias to lost_load and overview.
- There is **no "with vs without BESS → savings → payback" view**. The user must derive savings by mentally subtracting OPEX + CAPEX deltas across two projects.

**Export:**
- Chart/table CSV and SVG/PNG.
- Project bundle (.pypsaproj.zip), NetCDF, Excel, CSV zip, MATPOWER (`Sidebar.tsx:429-433`).
- FMEA worksheet CSV, EH sibling tables CSV.
- **No report generation, PDF, executive summary or shareable read-only result link.** Workspace sharing exists (members/lock) but is project-level.

**Copilot (AssistantDock + `backend/services/chat_tools*.py`):**
- 113 tools covering component CRUD, time series, solver config, `run_simulation`, results, all adequacy studies, projects/scenarios, `compare_scenarios` (headline KPIs + `delta_b_minus_a`), UI navigation (`ui_open_panel`, `ui_select_component`, `ui_open_asset_detail`), imports/exports, `export_chat_summary` (md/txt of the chat), `reconstruct_network_from_image`, `apply_demand_from_excel`, `explain_investment` and `build_study_report`.
- It is the **only component that can produce a narrative conclusion**, and only for reliability (the study report) or per-asset "why this size" (`explain_investment`).
- It needs an API key, and profile setup is super-admin only (CHATBOT.md).
- The starter chips are generic with literal placeholders.
- The conclusion lives in chat scrollback; the only persisted form is `export_chat_summary`.

---

## 5. Inputs

**Costs.** Each asset shows four cost fields side by side, "Marginal cost €/MWh", "Capital cost €/MW", "FOM cost €/MW/yr" and "Overnight cost €/MW", plus an optional per-asset "Discount rate" (`PropertiesPanel.tsx:411-418, 674-679, 1349-1354, 872-876`).

- **Precedence is implicit.** Per the tooltip, if overnight_cost is set, *"the solver recomputes capital_cost = overnight_cost × annuity(discount_rate, lifetime) + fom_cost. Leave empty to use the capital_cost you typed directly."* So the form holds two sources of truth for one quantity.
- **The global discount rate, inflation, default lifetime and CO₂ price live in Solver Settings → Dispatch** (`SolverSettings.tsx:1476-1525`), far from the asset.
- **BESS specifically:** a StorageUnit has only a **per-MW** capital/overnight cost. The energy (MWh) cost is folded in via fixed `max_hours`. There is no €/kWh field and no way to co-optimise power and energy without building Store + Link manually.
  - The Store card uses €/MWh, so a novice meets two BESS modelling idioms with different cost bases.
- **Snapshot-share trap:** the tooltips warn *"The model charges it for the share of a year your snapshots represent, so a one-day model pays 1/365 of it"*. That is correct but invisible unless hovered.

**Profiles.** TimeSeriesManager has categories Loads (`p_set · MW`), Renewables (`p_max_pu × p_nom · MW`), Conventional / Demand Response / Links, each with sub-toggles `p_max_pu` / `p_min_pu` / `marginal_cost` (`TimeSeriesManager.tsx:55-114`).
- Upload is per asset with a single-column .csv/.xlsx, or a bulk template download.
- Synthetic shapes are available (double-peak residential, industrial).
- QA warnings come from `timeseries_qa.py`.
- The chat can `apply_demand_from_excel`.

**Tariffs and prices.** **No tariff concept exists.** A grep for tariff, import price, feed-in and retail finds nothing.
- An electricity purchase price has to be modelled as a "Conventional" **Generator** at the site bus with a `marginal_cost` profile described as *"fuel-price traces, market scenarios"* (`TimeSeriesManager.tsx:86-87`).
- Export/feed-in revenue, demand (capacity) charges, network fees, time-of-use bands, and import/export limits as a "grid connection" have no first-class inputs.

**Scenarios.**
- `POST /api/projects/{base}/scenarios` copies the whole project (CONTEXT.md). ScenariosPanel's create dialog has name, description ("What's different from the base?") and a type (baseline / scenario / stress, `utils/scenarioType.ts`).
- Variation is **manual**: branch, switch to the child, edit fields, save, queue the solve, then compare two at a time.
- **No parameter sweep or sensitivity UI** exists outside the adequacy frontier and loops. BESS size, price or capex sensitivity cannot be run.

**Multi-period.** Model Horizon step 1 "Mode" (*"Enables PyPSA's multi-horizon LP…"*), then Investment years, then Economics (objective weighting, auto-discount, PV preview; "Advanced": per-carrier load scalers, per-period CAPEX budgets "Budget M€"). Related settings:
- Foresight (overnight / myopic / perfect) is in Solver Settings.
- Per-vintage bounds are in Capacity Bounds and in VintagePeriodBoundsModal.
- `docs/pitfalls-myopic-and-cost-reporting.md` documents cost-reporting traps that a novice would fall into silently.

**PyPSA-internal fields exposed raw:**
- Properties: `p_nom`, `p_nom_min/max`, `p_max_pu`, `p_min_pu`, `e_nom`, `e_min_pu`, `e_max_pu`, `e_initial`, `q_set`, `s_nom`, `v_nom₀/₁`, `sub_network`, `r`/`x`/`b (per km)`, `committable`, `efficiency2` (multi-port links), `build_year`, `lifetime`, and Adequacy "FOR / EFORd / MTTR".
- Bottom grids: raw column names.
- Solver: `user_objective_scale`, `ens_cap_permyriad` (in copy), `run_ac_pf_after_lopf` (in validation), `solver_options`.
- Results: "μ upper/lower", "p_nom_opt".

---

## 6. Existing UX design principles in the codebase

- **Brand/theme:**
  - `public/brand.css` is the source of truth, guarded by `frontend/brand.theme.test.ts` (`index.css:14-17`).
  - The "brand-dark" surface is used for the projects home, and red is both the accent and the destructive signal (`index.css:66`).
  - Product name "PyPSA Studio" and a "Portfolio Optimizer" design handoff (`components/PageKit.tsx:1-7`).
  - PageKit primitives: PageHeader (eyebrow/title/subtitle/actions), PageBody, PageSection, StatCard, Field, Toggle, Btn, Seg, BarList.
  - Density (comfortable/compact) and theme are togglable from the command palette.
- **Modal a11y primitive:** `components/Dialog.tsx`, spec `docs/superpowers/specs/2026-07-28-modal-a11y-primitive-design.md`, used by 12 files.
- **"Unavailable vs zero":**
  - ADR-0001 (`docs/adr/0001-unresolvable-figures-ship-as-null.md`): "zero is a legitimate, meaningful result… a defaulted zero is indistinguishable from a real zero". ADR-0003 adds no generic wrapper.
  - Applied throughout: the sidebar issues badge shows "?", Economics uses `COST_UNAVAILABLE`, loop panels say "Nothing below is a result of zero: there is no result".
  - Inconsistency: Dispatch KPIs render "—" for a near-zero lost load or curtailment (`Dispatch.tsx:2556`), which re-merges zero and unavailable.
- **Toasts and undo:** `react-hot-toast`, `utils/toasts.tsx` (`confirmToast`, `showUndoToast`, `vintageCloneToast`), server-side undo (⌘Z, `backend/services/undo_service.py`), and a History tab with coloured action tags.
- **Command palette and keyboard:** ⌘K (all), ⌘P (projects), ⌘J (assistant), ⌘S, ⌘Z, Esc, `?` (`components/ShortcutsHelp.tsx:6-13`). The palette items are navigation ("Open results panel", …), not tasks.
- **Progressive disclosure:**
  - D22 reveal rules (extendable reveals bounds).
  - `<details>` "Advanced" in StepShell and SolverSettings; "Methodology" in Economics.
  - Model Horizon hides depth within steps.
  - The EH panel is collapsed by default.
- **No novice/expert mode, persona switch or "simple view"** anywhere. The only audience statement (`propertyDocs.ts:7-9`) targets power-systems engineers.
- **Explicit IA critique already on file:**
  - FMEA spec §8.1: *"The app's model is project → solve → results. A sweep is project → N solves → aggregate results. That has no slot in the current IA: a sweep is not a result, it is a study that produces results."* (`docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md:660-672`).
  - The Model Horizon spec's rejection of "six equal doors".
  - These are the two strongest in-repo arguments for a "Study" layer.

---

## 7. Top 15 friction points for "Should I invest in a BESS at my site, and what is the ROI?" (ranked)

1. **No study or question entry point; the app starts from a network, not a decision.** Home offers blank/template/import/clone; the workbench offers ~80 equal-weight surfaces. The BESS question has nowhere to be stated, so the user must infer a 7-step modelling recipe that lives only in the README.
   Files: `pages/ProjectsHomePage.tsx:78-81`, `App.tsx:99-139`, `layout/Sidebar.tsx:1227-1360`, `README.md` ("A typical workflow").
2. **No ROI, NPV, IRR, payback or cash-flow metric anywhere.** Economics reports annualised net profit and LCOS against LP dual prices. The investment question cannot be answered in-app.
   Files: `pages/results/Economics.tsx` (methodology :949-996), `backend/services/economics.py`, `backend/services/asset_results/registry.py:198-214`.
3. **The zero-profit-by-construction trap is shown to the model but not to the user.** An optimally sized (interior) BESS shows ~0 net profit, which a novice reads as "no ROI". The explanation exists only as a chat reading note.
   Files: `backend/services/chat_tools.py:4250-4258` vs `pages/results/Economics.tsx`, `pages/results/asset/AssetDetail.tsx`.
4. **No baseline vs with-BESS construct.** ROI needs a counterfactual. The user must branch a scenario manually, delete or disable the BESS, solve both, then read OPEX/CAPEX Δ across 10 compare tabs and compute savings by hand.
   Files: `pages/ScenariosPanel.tsx`, `pages/CompareView.tsx:1796-1808`, `pages/Results.tsx:84-102`.
5. **No tariff or grid-connection model.** Import price, export/feed-in price, demand charges, network fees and connection limits have no inputs. The user must know to fake grid purchase as a "Conventional" Generator with a `marginal_cost` time series.
   Files: `pages/TimeSeriesManager.tsx:55-114`, `layout/Sidebar.tsx:135-180` (palette has no "Grid connection").
6. **The cost entry model is PyPSA's, and the unit badge is wrong.** Capital cost (annuity, "€/MW" badge but actually €/MW/yr), Overnight cost, FOM and a per-asset discount rate sit side by side, with implicit precedence. The global discount rate and lifetime are hidden in Solver Settings.
   Files: `layout/PropertiesPanel.tsx:411-418, 674-679`, `utils/propertyDocs.ts:85-90, 153-158`, `pages/SolverSettings.tsx:1476-1525`.
7. **A BESS cannot be sized from the creation form, and the energy cost is not separable.** The Battery create form has no Extendable box and no cost fields. StorageUnit prices only €/MW, with energy fixed via `max_hours`, so power/energy cannot be co-optimised without a Store+Link build.
   Files: `layout/CreationForm.tsx:65-73, 170`, `layout/PropertiesPanel.tsx:643-679`.
8. **The default horizon is a single "now" snapshot and nothing warns about storage.** A novice who adds load, a price and a BESS and presses Run optimises one hour. Model Horizon opens at "Mode: multi-investment periods", not "what year and what resolution".
   Files: `pages/modelHorizonModel.ts:215-220`, `pages/ModelHorizon.tsx:660-673`, `backend/services/validation_service.py` (no such check).
9. **Jargon everywhere, and the stated audience is the expert.** "Run LOPF", p_nom, p_max_pu, η store, ‱, VOLL, ENS, SCLOPF, μ upper, "vintage", "objective". The docs file says it uses PyPSA terminology "rather than re-explaining basics". There is no glossary.
   Files: `utils/propertyDocs.ts:7-9`, `layout/AppHeader.tsx:557`, `backend/services/asset_results/registry.py:178-181`, `pages/SolverSettings.tsx:1689`.
10. **No conclusion, report or shareable output in the UI.** After a solve: no auto-open, no success toast, default Dispatch tab, no headline card. Export produces data files only (CSV/XLSX/NetCDF/bundle). The only narrative (`build_study_report`, reliability-only) is chat-only.
    Files: `layout/AppHeader.tsx:611-622`, `pages/Results.tsx:57, 420-431`, `backend/services/adequacy/study_report.py`, `layout/Sidebar.tsx:429-433`.
11. **Templates do not match the persona, and can be broken outside the packaged app.** All four are transmission test grids, and the footer exposes a repo path. In `start.sh`/dev runs the unbuilt `network.nc` returns a developer 404 ("run project_templates/_build.py").
    Files: `layout/NewProjectWizard.tsx:55-63, 328`, `backend/routers/projects.py:1304-1310`, `build-macos.sh:86-93`.
12. **No sensitivity or uncertainty workflow for the investment case.** There is no sweep over capex, price spread, BESS size or discount rate. Each variant is a hand-edited scenario copy queued one by one. The adequacy frontier and loops are the only multi-solve studies, and they are reliability-only.
    Files: `pages/ScenariosPanel.tsx:520-616`, `pages/SolveQueuePanel.tsx`, `pages/results/FrontierPanel.tsx`.
13. **Stale or wrong in-app cross-references and copy defects erode trust:**
    - "Toggle Multi-investment periods in General" (no such toggle): `SolverSettings.tsx:1568`
    - VOLL results "in the Results → LoadFlow tab": `SolverSettings.tsx:1685`
    - "promote snapshots to multi-period under Snapshots → Multi-period": `validation_service.py:986`
    - "Set `ens_cap_permyriad`" (UI label is "ENS target"): `AdequacyTab.tsx:85`
    - literal "max_solves" placeholder: `MarginLoopPanel.tsx:346`
    - `$` vs `€`: `CreationForm.tsx:61-62`
14. **The good guidance patterns are buried at the end of the analysis, and scoped to reliability.** Verdicts, completeness, fidelity badges and honesty notes live in Results → Adequacy (8 stacked panels) and a collapsed "Reference design ▸" panel. They apply only after a solve, only to reliability, and use engine vocabulary as chip labels ("fmea_top: skipped", "m*", "COPT", "DtC").
    Files: `pages/results/AdequacyTab.tsx`, `pages/results/EhReferenceDesignPanel.tsx:536-720`.
15. **The copilot is the only guide, and it is gated and generic.** It needs an API key or a super-admin profile. Starter chips have literal placeholders ("open project_name", "scenario_a vs scenario_b"). There is no BESS or ROI starter, no campaign/study UI mirroring `start_campaign`, and conclusions stay in chat scrollback.
    Files: `components/ChatPanel.tsx:1071-1096`, `components/ChatLaunchGreeting.tsx`, `backend/services/chat_tools_schema.py:1042-1075`, `CHATBOT.md` ("Setup").

**Also noted (lower rank):**
- Properties/bottom panel hide whenever a slide panel is open (`App.tsx:645`).
- "Snapshot" is overloaded (saved checkpoint vs time step), which CONTEXT.md itself calls "the defect".
- Four orphan page modules: ResultsViewer, LoadProfileManager, GenerationStack, LoadEditor.
- Dispatch "—" for near-zero values contradicts ADR-0001.
- Solve success gives no feedback beyond the pill.

---

## Appendix: what a guided study workflow can reuse directly

- **Stepper chrome and summary-first landing:** `pages/modelHorizon/StepShell.tsx`, `HorizonSummary.tsx`, and pure predicates in `modelHorizonModel.ts` (visibility, summary sentences, "unset" entry rule).
- **Stage status list:** `GridspinePanel.tsx` `Stages`, and the EH `completeness` enum `ok | not_established | skipped`.
- **Verdict / fidelity / provenance chips:** `adequacy.tsx` (AdequacyChips, fidelity tips), `FmeaTab.tsx` (EngineBadge), gridspine ledger (measured/datasheet/assumed).
- **Shared caveat strings:** RESERVE_MARGIN_CAVEAT, and input guards with consequence copy (`ensTargetWarning`).
- **Report contract:** `backend/services/adequacy/study_report.py` (objective, sections with fidelity, required_disclosures, not_established, evidence_gaps). Pair it with `explain_investment`'s `binding_constraint` + `reading_notes` for an investment-case equivalent.
- **Campaign/budget:** `start_campaign` / `end_campaign` (chat tools), a natural backend for a "Study" object with an objective and a solve budget.
- **Primitives:** PageKit (PageHeader/StatCard/Seg), the Dialog a11y primitive, toasts/undo, the command palette, the ADR-0001 null semantics.
