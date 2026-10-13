# pypsa-gui: overall UX/UI assessment

**Date:** 2026-10-05. **Scope:** `pypsa-gui` on `origin/master` (`1d6ea4aea`), the React workbench and the Guided face, read-only. **Method:** the app was started locally (uvicorn, local mode, no API key; Vite dev server) and walked with Playwright/Chromium at 1440×900 and 390×844; 50 screenshots were captured (listed in the appendix, all under the session scratchpad `…/scratchpad/ux/`). Where the browser could not reach something, the finding cites `file:line` on master. The prior research threads (`2026-09-28-ux-audit-novice-path.md`, `2026-09-28-ux-patterns-guided-studies.md` on `origin/claude/edge-tool-ux-research-n0n2l6`), the Guided-mode spec (`docs/superpowers/specs/2026-09-27-guided-mode.md`) and the unification plan (`2026-10-05-one-investment-engine-two-faces.md`) were read first so this document does not repeat them; it cross-references them.

**Owner's ask:** make the app more user-friendly, simpler, more efficient to parameterise, and more beautiful in how things are arranged (sidebars, panels), for two audiences served by one implementation: the expert workbench and the guided, chat-assisted path.

---

## 1. Executive summary

1. **The shell is a three-sidebar sandwich that starves the work area.** At 1440 px the left nav (240), the properties panel (300) and the always-open assistant dock (380) leave the network canvas about 480 px wide, and every "half-width" slide panel (Solver settings, Model horizon, Project info, Scenarios, Issues) gets ~390 px, which breaks their own layouts: truncated titles ("Dat…", "Sc…", "Fi…"), stat cards whose values overflow their boxes, five-column KPI rows squeezed into 60 px columns (`20-expert-workbench.png`, `35-overview.png`, `32-horizon.png`, `37-scenarios.png`, `34-issues.png`). This is the single biggest "beauty" and "efficiency" problem and is a layout-policy fix, not a redesign.
2. **One study is set up in at least seven places** (Assets palette → Properties form → Time series → Model horizon → Solver settings (5 tabs, with the economic assumptions under *Dispatch*) → Capacity bounds → Issues → Run), all given equal weight in a 17-row sidebar. The sidebar mixes workspace actions, a parts bin, a long tail of tools, and an application-settings page under "SIMULATION".
3. **Guided mode is a good, honest first cut** (five step cards, plain words, "Let the assistant do this", a Guide tour, test-pinned copy), but it is a *reliability-only* hub-design wizard bolted beside the expert shell: the expert chrome (header stats, "Ctrl K", the dock, bottom grids, canvas) leaks through the moment the hub panel is closed (`16-guided-results-tabs.png`), there is no investment question in it yet (U3 of the plan), and without an API key the assistant that "guides each step" cannot answer at all.
4. **Parameterisation is consistent inside the Properties forms but inconsistent across entry points.** The edit form is well grouped (Topology / Capacity / Operation / Costs / Lifecycle, with "?" tips), but the creation forms expose a different field set and the wrong currency (`$/MWh`, `$/MW` in `CreationForm.tsx:61-62` and the bottom grid headers `BottomPanel.tsx:67`), a battery cannot be sized or costed at creation, the quick-add form opens at the *bottom* of a long properties panel, and there is no bulk/tabular edit except the raw PyPSA grids.
5. **Cost semantics are the main expert trap:** "Capital cost €/MW" is an annuity, "Overnight cost €/MW" silently overrides it, the global discount rate / lifetime / CO₂ price live under Solver settings → *Dispatch*, and four stale cross-references still point to panels that no longer exist (see §4.2). These are copy and placement fixes.
6. **Visual system is strong at the token level and uneven at the composition level.** The red-on-black brand, PageKit primitives, mono numerics and the dark ramp are coherent; but 82 % of the explicit font sizes in the UI are 9–11 px (1,539 vs 337 occurrences of 12–13 px), the canvas has three overlapping overlays (legend card, minimap, playback chip), the "Compact" density toggle makes no visible difference on the workbench, and in this capture the light theme flipped the sidebar and panels but not the header, tabs, canvas or dock (`62-light-workbench.png`).
7. **Feedback after the key action is weak:** a successful solve shows no toast and does not open Results; for about a second the header shows an amber *Abort* button next to an *Optimal* pill (`45-after-solve.png`); a foreground solve rewrites the Buses grid (PQ→Slack, sub-network 0/1/2), which looks like data corruption to a user.
8. **Two live defects found while clicking:** the Investment tab fires `GET /api/results/value_flows`, a route that does not exist, and surfaces two red "Frontend not built" toasts (`51-results-investment.png`; `api/commercial.ts:216`, IC-owned); and the literal `{'max_solves'}` placeholder in `MarginLoopPanel.tsx:349` is still on master.
9. **Accessibility is mid-level:** the stepper, dialogs and mode switch carry proper roles and `aria-current`/`aria-pressed`, inputs are mostly implicitly labelled via `<label>`, but the PageKit `Toggle` is a clickable `<div>` with no role or keyboard handling, there are only four `focus-visible:` styles in the whole app, and nothing is usable below ~1100 px: at 390 px the assistant dock covers the entire page (`70-narrow-home.png`, `71-narrow-guided-hub.png`).
10. **Recommended direction:** keep one shell, give it *two layouts* rather than two apps: a task-ordered sidebar for the expert (Model · Time · Economics · Run · Results · Reports), a study rail for the guided face, a right-hand "inspector" that hosts both the properties panel and the assistant as *tabs* (so only one right panel is open at a time), and a width policy where panels get a minimum of 560 px instead of a hard 50 %.

---

## 2. What works well (keep it)

- **Projects home as the front door** (`01-home-first-run.png`): clear hero, five start cards, one primary CTA. The copy is architecture-flavoured but the structure is right.
- **Properties panel for a bus** (`22-properties-bus.png`): a read-only summary (voltage, carrier, control), a "Capacity summary", "Connected assets" cards with counts, and "Add to this bus" quick-add chips. This is the best "parameterise in place" pattern in the app and should be the model for everything else.
- **Asset edit form** (`23c-properties-generator-edit.png`): grouped sections, units in labels, "?" tips from one catalogue (`utils/propertyDocs.ts`), contextual reveal of `p_nom_min/max` when *Extendable* is ticked (D22 rules), "global (7.0 %)" placeholder showing the inherited discount rate.
- **Results → Economics and Capacity Expansion** (`51-results-economics.png`, `51-results-capex.png`): the StatCard strip with formula sub-lines, carrier filter, CSV/SVG export on every chart, ADR-0001 "—" for not-established.
- **Adequacy / FMEA honesty pattern** (`51-results-adequacy.png`, `51-results-fmea.png`): every panel states its own empty case and the next action, provenance badges on rows, "no number here is comparable to a statutory standard" caveats. The prior audit already catalogued this as the reusable pattern.
- **Guided hub design** (`10-guided-workbench-after-template.png`, `11-guided-hub-start.png`, `12-guided-hub-goal.png`, `15-guided-tour.png`): numbered rail with done/current/blocked states and `aria-current="step"`, one card per step with ≤ 3 choices, ✓/! rows with "?" terms, "Ask about this" and "Let the assistant do this" on every card, a Guide tour whose text comes from the same backend catalogue the assistant reads (`components/GuidedTour.tsx:1-10`). The "Where the numbers come from: synthetic illustrative data…" provenance line on the Start card is exactly the right instinct.
- **Mode switch and toasts**: `Guided | Expert` segmented control with `aria-pressed`, a toast that says what changed ("advanced panels hidden, ask the assistant for any of them"), remembered per browser, first-run defaults to Guided.
- **Command palette and keyboard**: ⌘K with 36 entries, ⌘P projects, ⌘J assistant, ⌘S/⌘Z, `?` help, Esc closes panels (`60-command-palette.png`, `61-shortcuts-help.png`).
- **Design tokens**: `public/brand.css` as the single source of truth with documented contrast ratios, a theme test that fails on drift, `color-scheme: dark` for native controls, mono `tnum` numerics.

---

## 3. Findings by area

Severity: **High** = blocks or misleads a core task for one of the two audiences; **Medium** = costs time or trust every session; **Low** = polish.

### 3.1 Information architecture

| # | Finding | Sev | Evidence | Recommendation |
|---|---|---|---|---|
| IA-1 | **Three fixed side panels + a 50 % slide panel starve the centre.** Sidebar 240 px, Properties 300 px, Assistant dock 380 px (`App.tsx:685`), slide panels are `w-1/2` of what is left (`App.tsx:634,663`). At 1440 px a slide panel is ~390 px and the canvas ~480 px. | High | `20-expert-workbench.png`, `31-solver-general.png`, `35-overview.png` (five StatCards at ~60 px each, title "Dat…"), `32-horizon.png` ("Hourly (h)", "Single period" overflow their cards), `37-scenarios.png`, `34-issues.png`, `38-reports.png` | Replace the `w-1/2` rule with `min(50%, max(560px, …))`; when a slide panel opens, auto-collapse the dock to its 40 px launcher unless the user pinned it; treat Properties and Assistant as two tabs of one right inspector (see §5). |
| IA-2 | **The sidebar is a flat list of 17 destinations in three sections** (PROJECT 7 rows, DATA 2 rows + a 20-item palette, SIMULATION 9 rows) plus MODE and PREFERENCES pinned at the bottom (~200 px). The study order (model → time → economics → run → results) is not visible; "Settings" (app settings, eyebrow APPLICATION in `App.tsx:118`) sits under SIMULATION (`Sidebar.tsx:1312`); "Planning → dynamics" is listed for every project but dead-ends on non-study projects (`39-gridspine.png`). | High | `21-expert-assets-palette.png`, `38-reports.png`, `39-gridspine.png` | Re-section by task (§5): *Build* (canvas tools, assets), *Time* (horizon + time series), *Economics* (costs, finance, tariffs, library), *Run* (solver, issues, queue), *Results*, *Reports*; move app settings to the user menu; show "Planning → dynamics" only for study-kind projects. |
| IA-3 | **Two kinds of "settings" and two kinds of "snapshot".** Solver settings has five tabs (General / Solver / Dispatch / Network / Add. Constraints) and the economic assumptions (discount rate, inflation, default lifetime, CO₂ price) are under *Dispatch* (`31-solver-dispatch.png`); "Network snapshots" (checkpoints) vs "168 snapshots" (time steps) still collide in the status bar and sidebar (`36-snapshots.png`). | Medium | `31-solver-dispatch.png`, `36-snapshots.png`, `SolverSettings.tsx:1537` | Move economic assumptions to an *Economics* page beside Capacity bounds and the Investment editors; rename checkpoints to "Checkpoints" (CONTEXT.md already calls the overload "the defect"). |
| IA-4 | **Clicking the canvas closes the open panel** (capture-phase mousedown, `App.tsx:580-596`). A user editing Solver settings who clicks the canvas to look at a bus loses the panel; Properties is hidden while any panel is open. | Medium | `App.tsx:574-596` | Keep panels open on canvas clicks; let Properties coexist with a half panel (inspector tab). |
| IA-5 | **Results is 13 tabs in a horizontally scrolling strip**; Investment, FMEA and Asset Detail are off-screen at 1440 px and there is no answer-first page. The default tab is Dispatch. | Medium | `50-results-default.png`, `51-results-investment.png`, `Results.tsx:66-80,546` | Group into four: *Summary* (verdict/KPIs, new), *System* (capex, dispatch, load flow, prices, emissions, curtailment, lost load, storage), *Reliability* (adequacy, FMEA), *Investment* (economics, investment case, asset detail). Open Summary after a solve. |
| IA-6 | **Guided mode leaks the expert shell.** Spec §3.5 leaves canvas, bottom grids and Properties "unchanged"; closing the hub panel drops a novice onto the raw canvas with 11 spreadsheet tabs and a bus grid (`16-guided-results-tabs.png`). The header still shows "3 buses · 0 lines · 12 assets", "Search components…", "Ctrl K". | High (guided) | `16-guided-results-tabs.png`, spec §3.5 | In Guided, the centre should be the study rail; the canvas becomes a read-only "Site map" card, bottom grids are hidden, and the header stat chips become one project chip. |
| IA-7 | **Duplicate entry points**: New project from the home page card, the header button, the sidebar "+" tab, the palette and the assistant card; Compare from Scenarios and from Results' rail; Save in three places (header icon, sidebar Quick actions, palette). Not harmful, but each adds a row to the sidebar. | Low | `01-home-first-run.png`, `20-expert-workbench.png` | Keep header + palette; drop the sidebar "Save" row (autosave toggle already sits in the project card). |
| IA-8 | **Wizard "Study" tab speaks gridspine** ("UC window", "Extreme hours per criterion (k)") in the same dialog as "Blank" and "From template". | Low | `03-wizard-study.png` | Move the dynamics study behind a "More…" row or into the Planning → dynamics panel. |

### 3.2 Parameterisation efficiency

| # | Finding | Sev | Evidence | Recommendation |
|---|---|---|---|---|
| P-1 | **Creation and edit forms disagree.** Creating a generator from the palette asks 9 fields with `$/MWh`, `$/MW` (`CreationForm.tsx:51-62`); editing shows `€/MWh`, `€/MW` plus FOM, overnight, lifetime, build year (`PropertiesPanel.tsx:418-436`). A battery cannot be made extendable or costed at creation (`CreationForm.tsx:65-73`, `26-sidebar-add-battery.png`). Bottom grid headers read `MC ($/MWh)`, `CC ($/MW)` (`BottomPanel.tsx:67`, `24-bottom-generators.png`). | High | files cited | One field catalogue per component (the D22 `attributeCatalog` already exists): creation = the catalogue's *required + key* fields, edit = all groups. Fix the currency to € everywhere (one `CURRENCY` constant). |
| P-2 | **Cost model needs a decision, not a tooltip.** "Capital cost €/MW" is an annuity (tooltip says €/MW/yr), "Overnight cost" silently recomputes it, "Discount rate" per asset vs global in Solver settings → Dispatch. | High (expert trust) | `23c-properties-generator-edit.png`, `31-solver-dispatch.png`, `propertyDocs.ts` | Show a cost *mode* segmented control per asset: "Annuity (€/MW/yr)" or "Overnight (€/MW) + lifetime + rate → computed annuity shown read-only". The IC Library defaults pack (plan §3 rule 4) is where the source/year provenance should render. |
| P-3 | **Quick-add is at the bottom of a long panel.** "Add to this bus" chips are the last section of the bus view; the inline form opens below them and requires scrolling (`25-quick-add-generator.png`). The Properties list view for a category renders every asset's full property list inline (5 generators × 12 rows, `23-properties-asset-list.png`). | Medium | screenshots | Pin "Add to this bus" under the bus header; show assets in a category as compact rows (name, p_nom, cost, extendable) with a chevron to expand one. |
| P-4 | **No bulk edit except raw PyPSA grids.** The bottom grids do support fill-across and paste, but the columns are raw PyPSA names, and the grid is 11 tabs × up to 46 columns (`24-bottom-generators.png`). | Medium | `BottomPanel.tsx:37`, `24-bottom-generators.png` | Promote the grid to a "Table" view of the inspector with the catalogue's labels/units, column presets (Capacity · Costs · Operation · Lifecycle) and multi-select edit from the canvas. |
| P-5 | **Validation surfaces engine codes and developer prose.** `gen_zero_costs`, "2 generator(s) have capital_cost, overnight_cost, and marginal_cost all == 0. Result will be indeterminate." (`34-issues.png`, `validation_service.py:1761`, `IssuesPanel.tsx:314`). Stale fix paths: `CapacityBoundsEditor.tsx:186,421` and `validation_service.py:987` say "Snapshots → Multi-period" (now Model horizon → Mode); `SolverSettings.tsx:1537` says "Toggle Multi-investment periods in General" (no such toggle); `SolverSettings.tsx:1651` says lost load lives in "Results → LoadFlow" (there is a Lost load tab); `AdequacyTab.tsx:104` tells the user to set `ens_cap_permyriad` (UI label is "ENS target"). | Medium | files cited | Give each issue code a plain title + a *Fix* button that opens the right panel at the right field (the Issues panel already has "View"); make the catalogue the single source for both label and path; delete the stale strings. |
| P-6 | **Defaults have no provenance in the expert face.** The Guided Start card says where numbers come from; the expert form shows `0` for capital cost with no hint that a library default exists. | Medium | `23c-properties-generator-edit.png` vs `11-guided-hub-start.png` | When the IC generic defaults pack lands (U1 a), render a "default · source · year" chip under cost fields and a "Reset to library default" action. |
| P-7 | **Time series is a three-column page squeezed into ~780 px** because the dock stays open on full-screen panels; its tab row carries sub-attribute hints (`p_max_pu × p_nom`) as tab labels. | Medium | `30-timeseries.png` | Collapse the dock on full-screen panels; label tabs "Loads / Renewables / Conventional / Demand response / Links" and move the attribute hint into the page body. |
| P-8 | **A foreground solve rewrites the Buses grid** (control PQ→Slack, sub-network blank→0/1/2) and the Properties "Control" field shows the solver's choice as if the user set it (`63b-light-solver.png`, `25-quick-add-generator.png`; spec §2.1 note 4 calls it out of scope). | Medium | screenshots | Either restore the columns after a foreground solve (the `preserve_bus_topology` wrapper exists) or present them as read-only "solver-derived" values. |

### 3.3 Visual design

| # | Finding | Sev | Evidence | Recommendation |
|---|---|---|---|---|
| V-1 | **Type scale is too small for a work tool.** 1,539 explicit sizes at 9–11 px vs 337 at 12–13 px across `src/**/*.tsx`; sidebar rows 13 px, chips 10 px, Adequacy prose 11 px, stat eyebrows 9 px. | Medium | grep counts; `51-results-adequacy.png` | Define a 5-step scale (11 / 12 / 13 / 15 / 22) in `@theme` and ban raw `text-[9px]` outside chips. Body/table text 12–13 px. |
| V-2 | **Canvas overlays collide.** The legend card (voltage bands, carriers, asset groups) sits over the top-left of a 480 px canvas; the minimap overlaps the bottom panel edge; the "Results · Run a simulation to enable" pill and the playback bar overlap the minimap (`20-expert-workbench.png`, `45-after-solve.png`). | Medium | screenshots | Legend as a collapsible chip (open on hover), minimap only above 900 px canvas width, playback bar docked into the bottom panel header. |
| V-3 | **Double headers.** Every slide panel shows "SIMULATION / Solver settings · Close" and then "SIMULATION · SOLVER / Solver & mode / subtitle" (`31-solver-general.png`, `30-timeseries.png`). ~120 px of chrome before content. | Low | screenshots | Merge the panel breadcrumb and PageHeader into one row. |
| V-4 | **Density toggle has no visible effect** on the workbench (`64-compact-workbench.png` vs `20`), because it only changes table row padding. | Low | `index.css:197-203` | Either drive sidebar row height, card padding and font size from the density vars, or remove the toggle from the sidebar footer. |
| V-5 | **Light theme applied inconsistently in this capture**: sidebar, Properties, slide panels and status bar switched, while the header, project tabs, canvas, bottom panel and dock stayed dark (`62-light-workbench.png`, `63b-light-solver.png`). The header is `bg-bg` (`AppHeader.tsx:791`) so this may be a transition/ordering effect rather than pinned colours; it needs a check. | Low | screenshots | Verify `data-theme` propagation after a programmatic theme change; add a visual-regression test per theme. |
| V-6 | **Empty states are good; loading states are absent.** Panels pop in fully formed or blank; Results tabs show nothing while fetching. | Low | observation | Add skeleton rows to StatCard strips and tables (PageKit already owns the primitives). |
| V-7 | **Status vocabulary drifts**: header pill "Optimal", status bar "Completed", assistant "Solved — the results match the network as it stands", Abort button visible beside the Optimal pill for ~1 s (`45-after-solve.png`, `AppHeader.tsx:27,955-998`). | Medium | screenshot | One status word set (Idle / Queued / Running / Solved / Failed / Stale) rendered from one selector. |
| V-8 | **Chrome brand red is used for accent, selection, destructive and warning badges alike** (red Issues badge "1" for a *warning*, red delete icon, red CTA). The tokens file explains the trade-off; in practice the Issues badge reads as an error. | Low | `34-issues.png` sidebar badge | Badge colour by severity (warn amber, error red). |

### 3.4 Guided experience

| # | Finding | Sev | Evidence | Recommendation |
|---|---|---|---|---|
| G-1 | **The assistant is central to Guided but is dead without a key.** Every card's "Let the assistant do this" and the greeting chips are enabled while the dock says "Add an Anthropic API key to talk to me" (`10-guided-workbench-after-template.png`). | High | screenshot | First-run in Guided: if no key, show a single setup card in the centre (not the dock) and gate the delegate buttons with the same reason; or make the hub cards fully usable without the assistant (they nearly are). |
| G-2 | **Guided answers only the reliability question.** Start → Site → Goal (hours of shortfall) → Results → Improve. There is no "Should I invest in X?" question card, no tariff/finance step, no verdict in € terms. The plan's U3 covers this; the IA here should anticipate it with a question picker on the Start card. | High (roadmap) | `11-guided-hub-start.png`; plan §5 U3 | Start card = question cards (Reliability hub · Investment case · Data-centre power) + templates; the rail steps come from the question. |
| G-3 | **Expert chrome leaks** (see IA-6) and the Guided sidebar still shows "Autosave off", a Save row and "Projects home" under three section labels for four rows. | Medium | `10-guided-workbench-after-template.png` | Guided sidebar = study rail + "Open in Expert"; autosave on by default in Guided. |
| G-4 | **Jargon is handled by a regex table** (`plainWords.ts`) that rewrites engine phrases after the fact ("LOLE" → "expected shortfall"). It works for the catalogued strings but any new backend phrase ships raw. | Medium | `pages/hubDesign/plainWords.ts` | Move plain-language titles to the backend catalogue the tour already uses (`backend/data/guides/*.json`), one entry per finding/issue code, and treat the regex as the fallback. |
| G-5 | **Confirmation cards** exist for write-tier tools (spec §6) and are the right shape; what is missing is a *summary of what will change* in the user's units before "Confirm". | Low | spec §6.6 | Render the tool arguments through the same label/unit catalogue as the forms. |
| G-6 | **Hand-off between faces has no "diff".** Switching Guided → Expert reveals everything; there is no marker on the fields the guided defaults filled. | Medium (roadmap) | plan §3 rule 5 | When the ledger lands, show a "default / customised / changed by expert" chip on every ledger-backed field in both faces. |

### 3.5 Accessibility basics

| # | Finding | Sev | Evidence | Recommendation |
|---|---|---|---|---|
| A-1 | PageKit `Toggle` is a `<div onClick>` with no `role="switch"`, no `tabIndex`, no key handler. | High | `components/PageKit.tsx:182-206` | Make it a `<button role="switch" aria-checked>`. |
| A-2 | Only four `focus-visible:` rules in the app; Leaflet focus outlines are suppressed globally (`index.css:327-333`); keyboard users get the browser default or nothing. | Medium | grep | One `:focus-visible` ring token (`--ring-accent` exists) applied in `index.css` to `button, [role=button], input, select, a`. |
| A-3 | Labels: inputs are mostly wrapped in `<label>` (cardKit `NumInput`, PageKit `Field`), which is fine; but only 8 of 229 `<input>` carry `aria-label`/`id`, so icon-only inputs (search boxes, chat composer) need checking. Icon-only buttons in the header carry `title` but not always `aria-label`. | Medium | grep; `AppHeader.tsx` | Audit icon buttons; add `aria-label` where the visible text is an icon or emoji (the dock toolbar uses emoji glyphs 🔊 ⚙ 🆕 📎 📋 🎙). |
| A-4 | Live regions: 7 `aria-live` uses; solve status changes (Running → Optimal) are not announced. | Low | grep | Announce status pill changes. |
| A-5 | Contrast: brand tokens document AA ratios; muted text (`#b9a7a9` on `#211b1c` ≈ 7:1) is fine, but 9–10 px mono eyebrows at `ink-400` (`#8b7679`) are ≈ 4:1 and below AA for small text. | Low | `index.css` | Lift eyebrows to `ink-500` or 11 px. |
| A-6 | No responsive layout: at 390 px the dock (fixed 380 px) covers the whole page (`70-narrow-home.png`, `71-narrow-guided-hub.png`); the spec declares "no mobile layout" a non-goal, but tablets and half-screen windows are a realistic consultant setting. | Medium | screenshots | Below 1100 px: dock becomes an overlay sheet, sidebar collapses to the icon strip, Properties becomes a drawer. |

### 3.6 Live defects noticed in passing

| # | Defect | Owner | Evidence |
|---|---|---|---|
| D-1 | Investment tab calls `GET /api/results/value_flows`; no such route exists (only `/api/simulation/commercial/value_flows`), so the SPA catch-all answers 503 "Frontend not built. Run `npm run build`." and two red toasts appear in dev. | IC (`api/commercial.ts:216`) | `51-results-investment.png`, `backend/main.py:1347` |
| D-2 | Literal `{'max_solves'}` renders as the word "max_solves" in the reserve-margin loop disclosure. | shared | `pages/results/MarginLoopPanel.tsx:349` |
| D-3 | Investment completeness chips show raw ids: `bill: not_established`, `participants: not_established`, `conservation: not_established`. | IC | `51-results-investment.png`, `InvestmentTab.tsx:26-33` |
| D-4 | Abort button shown with an Optimal pill for ~1 s after a solve; Solve Queue badge "1" lingers. | shared (`AppHeader.tsx`) | `45-after-solve.png` |
| D-5 | Stale copy paths (P-5): `CapacityBoundsEditor.tsx:186,421`, `SolverSettings.tsx:1537,1651`, `validation_service.py:987`, `AdequacyTab.tsx:104`. | shared | files |

---

## 4. Proposed target IA (one shell, two layouts)

### 4.1 Shell

```
┌ Header: [Projects / Project name ▾]  [Guided|Expert]  [Run ▾ status]  [⌘K] [user] ┐
├ Left rail (expert: sections; guided: study rail) ┬ Centre ──────────┬ Inspector ─┤
│                                                  │                  │ Props|Chat │
└──────────────────────────────────────────────────┴──────────────────┴────────────┘
   Status bar: project · saved · snapshots · solve status (one vocabulary)
```

- **One right-hand inspector** with two tabs, *Properties* and *Assistant*, 380 px, collapsible to a 40 px strip. The assistant keeps its own state when hidden (it already does). Only one right panel exists, so the centre is never narrower than ~800 px at 1440.
- **Centre** hosts the canvas *or* a page. Pages are full width by default; "beside the canvas" is an explicit split the user drags (persisted), minimum 560 px.
- **Panels never close on a canvas click**; Esc and the Close button do.

### 4.2 Expert face: left rail by task

```
BUILD        Canvas tools · Assets (palette as a popover, not a 20-row list) · Table
TIME         Model horizon · Time series
ECONOMICS    Costs & lifetimes (per-asset cost mode) · Library & defaults · Tariffs & contracts · Finance inputs
RUN          Solver · Issues (badge by severity) · Queue
RESULTS      Summary · System · Reliability · Investment
REPORTS      Reports · Checkpoints · Scenarios & compare
────────────
(bottom) Project card (name, autosave) · Guided ↔ Expert · Theme · Help
```

- "Settings" (API keys, models, log) moves to the user menu; "Workspace" (members, lock) to the Projects home project card; "Planning → dynamics" appears only for study-kind projects.
- Solver settings becomes *Run → Solver* with two tabs (Solve · Advanced); clustering, AC-PF chaining and solver options are Advanced; the economic assumptions leave it.
- Results → *Summary* is a new answer-first tab: verdict chips (solved · condition · cost · reliability · investment case if present), the three KPIs that moved, "what changed since last solve", links into the other groups.

### 4.3 Guided face: study rail

```
STUDY  ● Question     (Reliability hub · Investment case · Data-centre power …)
       ● Site         (what you have: connection, critical load, strength, outages)
       ● Key choices  (the ≤ 8 parameters this question is sensitive to; everything else "from library defaults ▸")
       ● Run          (one button; plain progress; cost disclosure)
       ● Verdict      (headline sentence, 3 KPIs vs do-nothing, biggest risks, what was not established)
       ● Improve      (findings → "Let the assistant do this" → confirmation card)
       ● Report       (assumptions appendix listing every default used, export)
       ─────────────
       Open in Expert ↗     Ask the assistant (opens inspector → Assistant)
```

- Centre shows the current card; a compact *Site map* card (read-only canvas thumbnail) sits under it so the model is never invisible.
- No bottom grids, no header stat chips, no "Search components"; the header shows the project name, the mode switch and the status pill.
- Every ledger-backed value carries a provenance chip (library default · you changed it · changed in Expert), and the same chip appears on the field in the Expert form (plan §3 rule 5).
- Without an API key: the cards still work by hand; "Let the assistant do this" is replaced by "Set up the assistant" once, centrally.

---

## 5. Roadmap

Ownership legend (plan §5): **IC** = `services/commercial|library|finance`, `pages/results/investment/*`, `InvestmentTab.tsx`, `api/finance.ts`, `api/commercial.ts`; **GS** = `services/study/*`, `pages/decision/*`, `api/decisionStudies.ts`, `utils/decisionVocabulary.ts`; **shared hot files** (additive edits, merge before PR) = `App.tsx`, `layout/*`, `store/uiStore.ts`, `services/validation_service.py`, `chat_tools*.py`, `main.py`; **free** = everything else (PageKit, index.css, Results.tsx tab table, pages/*.tsx not listed, hubDesign/*).

### 5.1 Quick wins (≤ 1 day each)

| # | Item | Files | Owner |
|---|---|---|---|
| Q1 | Panel width policy: `w-1/2` → `max(560px, 50%)`; auto-collapse the dock when a slide panel opens (keep the user's pin). | `App.tsx:634,663,685`, `AssistantDock.tsx` | shared (`App.tsx`) – coordinate; one-line change |
| Q2 | Fix the Investment tab 503 (`/results/value_flows` → the real route or add the route). | `api/commercial.ts:216` or `routers/results.py` | **IC** |
| Q3 | Currency: `$` → `€` in `CreationForm.tsx:61-62` and `BottomPanel.tsx:67`; one `CURRENCY` constant. | `layout/CreationForm.tsx`, `layout/BottomPanel.tsx` | shared (`layout/*`) |
| Q4 | Delete the five stale cross-references (D-5) and the `{'max_solves'}` literal (D-2). | `CapacityBoundsEditor.tsx`, `SolverSettings.tsx`, `AdequacyTab.tsx`, `MarginLoopPanel.tsx`, `validation_service.py:987` | free + shared (`validation_service.py`) |
| Q5 | Battery creation: add Extendable + cost fields to `storFields` (reuse the edit form's keys). | `layout/CreationForm.tsx:65-73` | shared (`layout/*`) |
| Q6 | After a successful solve: toast "Solved · €X · open Results" and open Results → Summary (or Economics until Summary exists); fix the Abort/Optimal overlap by deriving both from one status selector. | `layout/AppHeader.tsx:611-640,955-998` | shared (`layout/*`) |
| Q7 | Investment chips: plain labels ("Bill", "Participants", "Energy balance") with ok/not-established tone. | `pages/results/InvestmentTab.tsx:26-33` | **IC** |
| Q8 | Issues: plain title per code + severity-coloured sidebar badge. | `pages/IssuesPanel.tsx`, `layout/Sidebar.tsx:1330` (badge colour) | free + shared |
| Q9 | A11y: `Toggle` → `<button role="switch">`; global `:focus-visible` ring; `aria-label` on emoji/icon buttons in the dock toolbar. | `components/PageKit.tsx:182`, `index.css`, `components/ChatPanel.tsx` | free |
| Q10 | Move "Settings" out of SIMULATION into the user menu; hide "Planning → dynamics" for non-study projects. | `layout/Sidebar.tsx:1312,1364`, `layout/UserMenu.tsx` | shared (`layout/*`) |
| Q11 | Canvas overlays: legend collapsible, minimap hidden under 900 px canvas width, playback bar into the bottom-panel header. | `pages/TopologyCanvas.tsx`, `components/SnapshotPicker.tsx` | free |
| Q12 | Merge panel breadcrumb + PageHeader into one row (saves ~60 px per panel). | `App.tsx` `FullPageTab`, `components/PageKit.tsx` | shared (`App.tsx`) |

### 5.2 Medium (2–5 days each)

| # | Item | Files | Owner |
|---|---|---|---|
| M1 | **Right inspector**: Properties and Assistant as tabs of one 380 px panel; Properties stays available while a slide panel is open. | `App.tsx`, `components/AssistantDock.tsx`, `layout/PropertiesPanel.tsx`, `store/uiStore.ts` | shared – do after U1/U2 merges, or as the first PR both sessions rebase on |
| M2 | **Task-ordered sidebar** (§4.2): six sections, Assets as a popover palette, project card + mode + theme in the footer. | `layout/Sidebar.tsx` (2,035 lines; split into `sidebar/*Section.tsx`) | shared (`layout/*`) |
| M3 | **Results regrouping** into Summary / System / Reliability / Investment with a new Summary tab (verdict chips, three KPIs, "since last solve"). | `pages/Results.tsx:66-80`, new `pages/results/SummaryTab.tsx` | free; Summary's investment chip reads IC's `finance_case` facade (plan §6) |
| M4 | **Cost mode control** (annuity vs overnight) on generator / storage / store / link forms, computed annuity shown read-only; economic assumptions page under ECONOMICS. | `layout/properties/*`, `layout/PropertiesPanel.tsx`, `pages/SolverSettings.tsx` (remove the block at ~1476-1525), new `pages/EconomicsSettings.tsx` | shared (`layout/*`); the defaults chip waits for IC U1(a) |
| M5 | **Field catalogue drives creation forms**: creation = required + key fields from `utils/attributeCatalog.ts`; labels/units/tips from one place; compact asset rows in the category list. | `layout/CreationForm.tsx`, `utils/attributeCatalog.ts`, `layout/PropertiesPanel.tsx` | shared (`layout/*`) |
| M6 | **Type scale + density**: 5-step scale in `@theme`, density vars applied to sidebar rows/cards, lint rule against `text-[9px]`. | `index.css`, sweep of `text-[9…11px]` | free (mechanical, large diff – land between the two sessions' PRs) |
| M7 | **Guided shell** (IA-6/G-3): in Guided, hide bottom grids and header stat chips, show a Site map card under the current step, autosave on. | `App.tsx`, `layout/AppHeader.tsx`, `layout/BottomPanel.tsx`, `pages/hubDesign/HubDesignPanel.tsx` | shared + free (hubDesign) |
| M8 | **Assistant-less Guided** (G-1): central setup card when no key, delegate buttons disabled with the reason, chips hidden. | `components/ChatPanel.tsx`, `pages/hubDesign/shared/CardShell.tsx` | shared (`ChatPanel`) + free |
| M9 | Responsive breakpoints (< 1100 px): dock as overlay sheet, sidebar icon strip, Properties drawer. | `App.tsx`, `AssistantDock.tsx`, `Sidebar.tsx` | shared |
| M10 | Validation messages: title/path/fix per code from a backend catalogue (extend `data/guides/*.json`); "Fix" opens the field. | `validation_service.py` (additive), `pages/IssuesPanel.tsx` | shared (`validation_service.py`; note IC U1 f adds `commercial/preflight.py`) |

### 5.3 Structural (≥ 1 week; sequence with the unification plan)

| # | Item | Depends on | Owner |
|---|---|---|---|
| S1 | **Question-first Guided face** (§4.3): question picker on Start, rail generated from the question, Key-choices card bound to the GS ledger, Verdict and Report steps, provenance chips in both faces. | plan U2 (compile/adapter), U3 | **GS** (pages/decision, study tools) with hubDesign shell changes (free) |
| S2 | **Economics section in Expert** hosting IC's editors (TariffBuilder, LibraryBrowser, Contracts, Finance inputs) as pages instead of Results sub-tabs; "Open in Expert" from Guided lands here with defaults flagged. | U1 follow-up (defaults pack, commercial root affordance) | **IC** (editors) + shared (sidebar slot) |
| S3 | **Table view** (bulk edit with catalogue labels, column presets, multi-select from canvas) replacing the 11 raw grids. | M5 | shared (`layout/BottomPanel.tsx` → `pages/TableView.tsx`) |
| S4 | **Report as a first-class page** for both faces: sections with completeness, assumptions appendix listing every default used, AI prose marked for review, export. | S1, IC `InvestmentCaseReport` | GS (DecisionReport) + IC (report sections) |
| S5 | **Deep-linkable routes** (`/app/:project/results/summary`, `/app/:project/study/goal`) so the assistant's `ui_open_panel`, the tour and support links share one addressing scheme. | M1–M3 | shared (`routes.tsx`, `uiStore.ts`) |

**Sequencing note.** Q2, Q7 are IC-only and can ship now. Q1, Q3, Q5, Q6, Q10, Q12 touch `App.tsx`/`layout/*`, which the plan lists as shared hot files: land them as one small "shell polish" PR *before* U1's follow-up PR, or right after U2, never in parallel with either. M1/M2 (the inspector and the sidebar) are the structural shell change; the cleanest moment is immediately after U2 merges and before U3 starts, so U3's Guided work (S1) builds on the new shell. M6 (type scale) is a mechanical sweep that will conflict with everything; do it in a quiet window with both sessions rebased.

---

## 6. Appendix

### 6.1 Screenshot index (scratchpad `…/scratchpad/ux/`)

| File | What it shows |
|---|---|
| `01-home-first-run.png` | Projects home, first run, dock open with the API-key prompt |
| `02-wizard-blank.png`, `03-wizard-clone.png`, `03-wizard-study.png` | New project dialog tabs |
| `10-guided-workbench-after-template.png` | Guided default: hub design at Site, after creating the data-centre template |
| `11-guided-hub-start.png`, `11-guided-hub-site.png`, `12-guided-hub-goal.png` | The Start / Site / Goal cards |
| `15-guided-tour.png` | The Guide tour, step 1/6 |
| `16-guided-results-tabs.png` | Guided with the hub panel closed: the expert canvas and grids leak through |
| `20-expert-workbench.png` | Expert default layout at 1440×900 (canvas ~480 px) |
| `21-expert-assets-palette.png` | Sidebar palette expanded |
| `22-properties-bus.png`, `25-quick-add-generator.png` | Bus properties; quick-add form at the bottom of the panel |
| `23-properties-asset-list.png`, `23c-properties-generator-edit.png` | Category list (inline full property lists); generator edit form |
| `24-bottom-generators.png` | Bottom grid with `MC ($/MW…` header |
| `26-sidebar-add-battery.png` | Battery creation form (no cost, no extendable) |
| `30-timeseries.png` | Time series page squeezed beside the dock |
| `31-solver-general.png`, `31-solver-solver.png`, `31-solver-dispatch.png`, `31-solver-network.png`, `31-solver-addconstraints.png` | Solver settings tabs at ~390 px; economic assumptions under Dispatch with stale copy |
| `32-horizon.png` | Model horizon summary, stat cards overflowing |
| `33-capacity-bounds.png` | Capacity bounds with the stale "Snapshots → Multi-period" path |
| `34-issues.png` | Issues panel: raw code, truncated title |
| `35-overview.png`, `36-snapshots.png`, `37-scenarios.png`, `38-reports.png`, `39-gridspine.png`, `40-solve-queue.png`, `41-settings.png`, `42-workspace.png` | The remaining slide panels at half width |
| `45-after-solve.png` | One second after a solve: Abort button + Optimal pill, no results opened |
| `50-results-default.png`, `51-results-capex.png`, `51-results-economics.png`, `51-results-prices.png`, `51-results-loadflow.png`, `51-results-adequacy.png`, `51-results-fmea.png`, `51-results-investment.png`, `51-results-asset.png` | Results tabs |
| `60-command-palette.png`, `61-shortcuts-help.png` | Palette and shortcuts |
| `62-light-workbench.png`, `63-light-properties.png`, `63b-light-solver.png` | Light theme capture (partial flip) |
| `64-compact-workbench.png`, `65-sidebar-collapsed.png` | Density toggle (no visible change); icon-strip sidebar |
| `66-dock-collapsed.png` | Mode-switch toast ("Guided mode on — advanced panels hidden…") |
| `70-narrow-home.png`, `71-narrow-guided-hub.png` | 390 px: the dock covers the page |

### 6.2 How the app was launched (for repeatability)

`PYPSAGUI_LOCAL_MODE=1 PYPSAGUI_APP_DATA_DIR=<scratch> PYPSAGUI_PROJECTS_ROOT=<scratch> PYTHONPATH=<repo>:<repo>/pypsa-gui/backend /root/.venv-pypsa-gui/bin/python -m uvicorn main:app --port 8000` from `pypsa-gui/backend`, `node_modules/.bin/vite --port 5173` from `pypsa-gui/frontend`, Playwright loaded from `/opt/node22/lib/node_modules/playwright` with `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, as `scripts/smoke-guided.mjs` does. The Energy-Hub templates build from code, so they work in a dev checkout; the four classic templates still need `project_templates/_build.py`. The hub *study* (ENS solve → frontier → MC) was not run; Results and Improve cards were therefore seen in their blocked state only.

### 6.3 Not assessed

Map canvas (Leaflet) interactions, the Compare rail, Admin pages, auth flows, the chat with a live model (no key), and the IC/GS branch UIs (`pages/results/investment/*`, `pages/decision/*`) which are not on master.
