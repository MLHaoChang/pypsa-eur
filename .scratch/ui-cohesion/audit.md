<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# UI cohesion audit: do the sections read as one tool?

Date: 17 Nov 2026. Master `882d7114`. Owner decisions: UI-COHESION-1, THEME-LIGHT-1, DENSITY-1, UI-MODERN-1, VISUALS-1.
Brief: "make sure the user interface through the different sections of the tool is consistent and artistic similar in terms of design and fonts and background."

## What was shot

Real app (API :8000, Vite :5173), no mocks, signed in as the dev admin, viewport 1280×800, one solve per project.
`pypsa-gui/frontend/scripts/ui-cohesion-shots.mjs` took **300 screens** over three templates, plus 12 canvas states for the data centre (and wrote one row of computed styles per screen):

- three templates: **Data Center Energy Hub**, **Island Microgrid** and, because the brief asked for one more if a section looked different, **Industrial Hydrogen Hub**. The hub's sections use the same patterns as the other two; its only difference is a longer canvas legend. Findings below cite the data centre unless a template is named;
- **Guided**: the 10 sections of `.scratch/project-workflow/design.md` §2.1, plus Results Summary, Reliability (Adequacy) and FMEA;
- **Expert**: the canvas, 18 slide panels and 16 Results tabs;
- plus Home, Sign-in and the New project dialog, and the canvas states (selected node, Satellite, Hybrid, Site 3D); each in **light and dark**.

Only the shots a finding cites are committed (`shots/`, 800 px wide). The rest stay out of the repo; re-take with `node scripts/ui-cohesion-shots.mjs <outDir> eh_datacenter eh_microgrid eh_h2_hub`.
Scored against `docs/agents/review-ux.md`, `src/index.css`, `public/brand.css`, and `ui-ux-pro-max --domain ux` (consistency, empty states, loading) and `--stack react`.
Shots of screens that #1556, #1561, #1565, #1575/#1590, #1576, #1578, #1586 change show master; none of the findings below sits on a file those PRs change except where it says so.
Not covered: the chat dock in use (no `ANTHROPIC_API_KEY`), the Admin pages, Site 3D.

**Not flagged, by DENSITY-1:** 9–11 px text and Inter. Flagged: where a section *departs* from them (F2) or where Inter is not actually delivered (F1).

## What already holds together

- **Backgrounds.** The page surface is `rgb(255,255,255)` in light and `rgb(21,17,18)` in dark on **all 147 workbench screens per theme**; Home and Sign-in follow the theme too (checked by eye). A probe for large light grey surfaces on the dark screens found none; the light screens were checked by eye, with no dark block. The ui-modern audit's finding 9 (Home ignores the theme) is fixed.
- **Section header in Guided.** All ten sections use `SectionView` (`pages/hubDesign/SectionView.tsx:68`): number, 15 px/600 title, state dot, one summary line. Same on all three templates.
- **Tab bars in Expert Results.** Three levels of tabs share one underline style across 16 tabs.
- **Colour tokens.** One ramp (`index.css`, `brand.css`); Home, Sign-in and the New project dialog match the workbench.

## Findings, ranked by visible impact

### F1. The fonts are not shipped, and two monospace stacks render (high)

The brief says "fonts". The app declares Inter and JetBrains Mono but delivers neither: there is no `@font-face`, no `<link>`, no `@fontsource` dependency (`spa.html`, `public/brand.css`, `package.json`). Every shot here shows Inter only because this machine has it installed (`fc-match Inter` → `Inter-Regular.otf`). On a machine without it the text falls to `system-ui` (Segoe UI, SF, Roboto), and at 9–11 px the widths, and so every wrap and truncation, change.
The monospace is split too. `index.css:149-150` define `--font-family-sans/-mono`, which is not Tailwind v4's `--font-sans/-mono` namespace, so the `font-mono` utility resolves to `ui-monospace` (98 of 100 screens) while hand-written CSS (`index.css:404,428,448`) and `TopologyCanvas.tsx:221` use JetBrains Mono (32 screens). Numbers in the canvas and the dispatch tables are one face, the eyebrows and ids around them another. Two screens also render `ui-sans-serif` (the `font-sans` utility).
Shots: `shots/dc-expert-light-canvas.jpg` (JetBrains in the node labels, `ui-monospace` in the eyebrows), `shots/dc-expert-light-p-horizon.jpg`.
Responsible: `src/index.css` (`@theme`), `spa.html`, `package.json`, `public/login.html:60` (its own Inter stack).
Slice: **cohesion** (new).

### F2. No type scale: headings drift by screen, and a few screens leave the density (high)

There are no `--text-*` tokens. The source has 18 arbitrary sizes (`text-[11px]` ×1,136, `[10px]` ×943, `[12px]` ×394, `[9px]` ×150, `[10.5px]` ×124, `[11.5px]` ×104, `[12.5px]` ×85, `[13px]`, `[9.5px]` … `[24px]`) plus Tailwind's `text-xs` ×571 and `text-sm` ×163 (14 px). First heading of each screen, in light:

| Screen | Heading |
|---|---|
| Home | h1 41.6 px, h2 24, h3 18 |
| Guided section | h2 15 px |
| Expert panel with `PageHeader` (Time series, Snapshot range, Solver, Overview) | h1 16 px |
| Expert panel without one (Scenarios, Issues, Reports, Workspace, Profiles, Gridspine; title is a 12 px span) | h2 12.5 px, no h1 |
| Settings | h3 14 px |
| Results (both modes) | h1 16 px; inner h2 13 px, h3 12–12.5 px |

So the same level of heading is 12, 12.5, 13, 14, 15 or 16 px depending on which section the user is in. Home and the New project dialog are the only screens at 14 px+ body (`ProjectsHomePage.tsx` 15× `text-sm`, `NewProjectWizard.tsx` 9×); Home also holds the chat dock at 9–10 px beside a 41.6 px title.
DENSITY-1 departures: asset labels on the canvas are **8 and 8.5 px** (`TopologyCanvas.tsx:715,732,743,768`), under the 9 px floor, on 7–11 % of the characters of the Expert screens that show the canvas (it sits behind most panels). The ui-modern audit's finding 2 (canvas opens un-fitted) makes them smaller still.
Shots: `shots/home-light.jpg` against `shots/dc-guided-light-s03.jpg`; `shots/dc-expert-light-canvas.jpg`.
Slice: **cohesion**.

### F3. Expert panels have two header systems, Guided a third (high)

`App.tsx:196` `FullPageTab` draws a breadcrumb: red 9 px mono eyebrow, `/`, a **12 px** title (`App.tsx:232`). A page that mounts `PageHeader` (`components/PageKit.tsx:32`) replaces it with a compact row whose title is **16 px**. Which one a panel gets is an accident of its file: of the 18 panels, 4 (Time series, Snapshot range, Solver, Overview) have the 16 px title and a subtitle, and 13 the 12 px breadcrumb; the 18th, Hub design, shows `GUIDED / Hub design`. The 16 px panels add a "· detail" to the eyebrow ("TIME · TIME SERIES", "PROJECT · OVERVIEW"), the others do not ("RUN", "REPORTS"), and the 16 px title is cut by the "Next: …" button ("Snapshot ran…"). Guided adds a third: `GUIDED / Hub design`, then a 15 px section title, and its Results page `HUB DESIGN · RESULTS / Reliability results` with a subtitle.
Shots: `shots/dc-expert-light-p-horizon.jpg` (16 px, truncated), `shots/dc-expert-light-p-issues.jpg` (12 px), `shots/dc-expert-light-p-settings.jpg` (12 px crumb over 14 px section headings).
Responsible: `App.tsx` (`FullPageTab`), `components/PageKit.tsx` (`PageHeader`, `PanelChromeContext`), each panel's own file.
Slice: **cohesion**. It meets the band header in THEME-LIGHT (b), so the two PRs must agree on the header (see the table below).

### F4. Guided section bodies use three different card styles; Section 7 and the Library are raw (high)

- Sections 3, 8 and 10 are cards: 8 px radius, `bg-panel`, padded, with a titled h2 (`pages/hubDesign/cards/*`).
- Sections 2, 4, 5 and 6 are `SectionHoldings` lists: 4 px, hairline table, no card (`pages/hubDesign/SectionHoldings.tsx`).
- Section 7 (`cards/CommercialCard.tsx`, `components/DocumentValuesPicker.tsx`) is bare browser controls: native selects and date inputs, lowercase labels (`Tariff ID`, `Charge type`, `Unit`), "Add period to Item energy" text links, and the only uppercase 10 px muted kicker in Guided ("IMPORT TARIFF"). It is headed "complete" on the data centre. Unchanged since the ui-modern audit's finding 3.
- Expert's Commercial library (`pages/economics/CommercialLibraryPage.tsx`) shows native radio buttons and "Choose File No file chosen" in both themes.
Shots: `shots/dc-guided-light-s03.jpg` (cards) / `shots/dc-guided-light-s04.jpg`, `shots/dc-guided-light-s05.jpg` (lists) / `shots/dc-guided-dark-s07.jpg`, `shots/mg-guided-light-s07.jpg` (Section 7); `shots/dc-expert-dark-p-library.jpg`.
Slice: **cohesion** (card recipe, form field styling for Section 7 and the Library).

### F5. No radius or surface tokens: three card radii on one screen (medium)

`index.css` has no `--radius-*`. Counted over the 300 screens, bordered boxes use 4 px (780), 8 px (358) and 10 px (325), with 7, 6 and 20 px stragglers. In source: `rounded` (4 px) ×1,200+, `rounded-lg` (8 px), `rounded-[10px]` (29 in `pages/`, 16 in `pages/results/`, 7 in `components/`), `rounded-xl`/`rounded-2xl` (12/16 px, 25 in `pages/`). Guided Results tiles and the Results tab cards are 10 px; Guided sections are 4 and 8 px; Home cards are 20 px. Shots: `shots/dc-guided-light-res-summary-settled.jpg` (10 px tiles) against `shots/dc-guided-light-s03.jpg` (4 and 8 px).
Slice: **cohesion**.

### F6. Results: empty states have no shared component, and Guided and Expert Results carry different frames (medium)

Six phrasings and four layouts for "this has not run": "Not run yet: the grid study has not produced results…" (bare text and an outlined button), "Not yet available. It can't be run from here yet." (11 uses), "The network has no process unit." (boxed), "No control results yet: run the island study…" (a Summary row), "Run a simulation to enable" (canvas pill), plus the Investment tab's tiny red chips. `SiteEmptyState.tsx` is the only empty-state component, and it serves the canvas. Guided Results is titled "Reliability results" with 3 tabs, Expert "Optimization results" with 7 and two sub-tab rows; the two share data and styles but not a frame.
Shots: `shots/dc-expert-light-empty-states.jpg` (Grid code, Island, Grid risk, Investment, top to bottom), `shots/dc-guided-light-res-summary-settled.jpg` against `shots/dc-expert-light-res-summary.jpg`.
Responsible: `pages/Results.tsx` (tabs, frame), `pages/results/*` (each tab's own empty text).
Slice: **cohesion** (one `EmptyState` with the Guided wording; the wording rules stay in `review-ux.md`).

### F7. Always-on Guided chrome is unchanged (medium)

On all 10 sections: the ten-step stepper is clipped at 1280 px (the next step is cut mid-label; `shots/dc-guided-light-s05.jpg`), the "Every step below works by hand… Set up the assistant" banner repeats, and the floating "Chat here" button covers the bottom-right of the content. Findings 6 and 8 of the ui-modern audit, still open on master.
Slice: **cohesion** (stepper, banner); the floating button is a layout call for UX.

### F8. Guided Results opens on grey placeholder blocks for 2–4 s (low)

The four KPI tiles show as empty grey rectangles on first open (`shots/dc-guided-light-res-summary-early.jpg`, taken at 1.4 s; `-settled` at 4 s). The ui-ux-pro-max rule is a skeleton shaped like the content, with `aria-busy`; this one is neither tile-shaped nor labelled. Expert's Summary showed tiles at the same wait.
Slice: **cohesion** (skeleton shape), if it fits.

### F9. Guided and Expert name the same section differently (high; EXPERT-GUIDED-1)

Same section, both modes, side by side (light, data centre). The Guided sections and the Expert sidebar share the ten flow names; the Expert panels do not:

| Section | Guided | Expert sidebar item | Expert panel: eyebrow / title (`App.tsx` `PANEL_META`) | What the page itself says |
|---|---|---|---|---|
| 1 Project & site | "Project & site" | Model Horizon | TIME / Model horizon | h1 "Snapshot range & weightings" |
| 5 Assets & sizing | "Assets & sizing" | Capacity Bounds | BUILD / Capacity bounds | none |
| 6 Time series & profiles | "Time series & profiles" | Time series… | TIME / Time series | h1 "Time series library"; Profile studio is BUILD |
| 7 Commercial & parties | "Commercial & parties" | Economics, Commercial library | ECONOMICS / Economics; PROJECT / Commercial library | none |
| 8 Scenarios & optimisation | "Scenarios & optimisation" | Solver Settings, Scenarios, Grid study | RUN / Solver settings; **REPORTS** / Scenarios; SCENARIOS & OPTIMISATION / Grid study | h1 "Solver & mode" |
| 9 Readiness and run | "Readiness and run" | Issues, Solve Queue | RUN / Issues; RUN / Solve queue | none |
| 10 Results & reports | "Results & reports" | Results, Grid study results, Reports | RESULTS / Results; REPORTS / Reports | h1 "Optimization results" (Guided: "Reliability results") |

- The eyebrow vocabulary (PROJECT, TIME, BUILD, RUN, REPORTS, ECONOMICS, STUDIES, RESULTS, APPLICATION) is a third taxonomy beside the sections; only the two campus panels use a section name. Section 8's three panels carry three different eyebrows, and the "Next: Readiness and run ›" button on the Scenarios panel names the section its eyebrow ("REPORTS") does not.
- One item has three names: sidebar "Solver Settings", panel meta "Solver settings", page h1 "Solver & mode".
- The Hub design panel opened in Expert still reads `GUIDED / Hub design` (`PANEL_META.hubDesign`, `App.tsx:152`): a mode name on an Expert screen.
- Looks differ for the same job: Guided's Goal card is neutral with a small "Save goal"; Expert's Solver & mode groups its options in red-bordered boxes and ends in a full-width red "Save settings" bar (`shots/pair-s08-guided-left-expert-right.jpg`). Guided lists state in words ("to check", "to do", "complete") on the stepper; the Expert sidebar shows a dot with no word in the shots, and the Expert Issues panel is "Errors 0 / Warnings 0 · Clear" against Guided's "0 to do, 6 to check" (`shots/pair-s09-guided-left-expert-right.jpg`). Time series: Guided lists rows with "Edit ›", Expert is a three-column library with a drop zone (`shots/pair-s06-guided-left-expert-right.jpg`); Section 1: `shots/pair-s01-guided-left-expert-right.jpg`.
- Units and money agree in the pairs that were checked (MW, MWh, `€39.8 M`, `kt CO₂`, `h/yr`). One inside both modes: the Summary shows `€39.8 M` in the cost tile and `€39.75 M` in the "LP objective" line under it, the same number at two precisions (review-ux: one money notation per section).
Responsible: `App.tsx` (`PANEL_META`, `FullPageTab`), `layout/Sidebar.tsx` (labels), `pages/hubDesign/sectionLabels.ts` (the flow names), each panel's own heading.
Slice: **cohesion** (one name per section and panel, taken from `sectionLabels.ts`; test that every `PANEL_META` entry's eyebrow is a flow section name or a stated exception).

### The canvases (owner extension): topology, map and site 3D

Shot in light and dark (`ui-cohesion-shots.mjs` with `CANVAS=1`): the topology canvas empty, with a node selected, and the map modes Satellite and Hybrid and Site 3D. Not judged: the map basemap (the tiles are external and blocked in the sandbox, so the basemap is blank white in both themes), and the 3D scene itself (the templates have no site coordinates; only its empty state was shot). The map and scene colour code (`MapCanvas.tsx` 24 literals, `site3d/assetLibrary.ts` 22, `context.ts` 8) is in the inventory, not in a shot.

- **CV1. The canvas does not use the app's carrier tokens, and red means three things (high).** Chart code reads `--color-carrier-*` (`components/charts/chartTheme.tsx:90`, pinned by `chartThemeTokens.test.ts`), but the canvas draws from its own hex tables (`utils/assetTypes.ts` 22 literals, `utils/carrierCatalog.ts` 30, `pages/topologyEdges.ts` 22). The gensets (carrier gas) are red `#dc2626` on the canvas (`assetTypes.ts:98`), while the gas token is blue `#0369a1` (`index.css:101`) and charts use the token. Red is therefore the generator colour, the accent (primary button, "Each" toggle, active step) and the danger colour at once. review-ux.md already says "a carrier colour has one token, and canvas hex maps are pinned to it by a test" (#1336); the canvas is the part that is not. Shots: `shots/dc-canvas-selected-light-left-dark-right.jpg`.
- **CV2. A selected node is the same red as an unselected generator (high).** Selection is a 2 px border in the node's own colour plus a soft glow (`TopologyCanvas.tsx:686,693,881,890`). On the data centre the selected `grid_supply` and the unselected gensets are both red outlines; in dark the fills are near-black and the outline is the only cue. Selection should not depend on the carrier colour. This is a real choice: see Open choices, question 3.
- **CV3. The three canvases have three empty states (medium).** The topology canvas says nothing (the right panel says "Click a bus, line, or asset on the canvas to inspect it"); the map shows a centred white card with a red primary and a secondary button ("No bus has a location yet"); Site 3D shows bare centred text with a red text link ("This project has no 3D site yet"). Shots: `shots/dc-expert-light-canvas-hybrid.jpg`, `shots/dc-expert-light-canvas-site3d.jpg`. → cohesion item 5 (one `EmptyState`).
- **CV4. Canvas type is below the floor and a fourth heading style (medium).** Asset labels 8 and 8.5 px (F2); the legend heads ("BUSES & LINES · VOLTAGE", "LINKS · CARRIER", "ASSETS · TYPE") are 9 px uppercase tracked grey, a third kicker style beside the red mono eyebrow and Properties' red bold uppercase headings ("CONNECTED TO", "GENERATOR PROPERTIES"). In `shots/dc-canvas-selected-light-left-dark-right.jpg` the node labels are under 9 px at the default zoom.
- **Held up:** the canvas, legend card, toolbar and "Blank / Satellite / Hybrid / Site 3D" switch share the app's surfaces, radius and hairlines; the canvas background and dots follow the theme (`--color-canvas`, `--color-canvas-dot`); Site 3D's background follows the theme; the "No bus has a location" card is themed correctly in dark.
Slice: CV1 colours → **(c)** (canvas colours get a light/dark pair and read the carrier tokens, with the existing pinned test extended to `assetTypes.ts`); CV2 → **cohesion** once the owner picks; CV3 → **cohesion** item 5; CV4 → **cohesion** item 2.

### Colour findings (ride THEME-LIGHT)

- **K1. React Flow attribution badge is a pale grey box on the dark canvas**, its text unreadable (`shots/dc-expert-dark-canvas.jpg`, bottom-right). It is React Flow's default style, left unthemed beside `<Background>`/`<Controls>` (`pages/TopologyCanvas.tsx:3134-3142`). Style it with tokens in both themes. → (c)
- **K2. Chart palettes are chosen per page.** Capacity expansion draws one orange and one charcoal bar in light, orange and pale pink in dark; the energy-flow Sankey is grey-blue, the heatmaps blue-grey, dispatch its own set (shots: `shots/dc-expert-light-res-capex.jpg`, `shots/dc-expert-dark-res-capex.jpg`, `shots/dc-expert-light-res-flows.jpg`; inventory: `CapacityExpansion.tsx` 8 literals, `Dispatch.tsx` 34, `energyFlows.ts` 17, `StorageHeatmap.tsx` 5, `ResultsViewer.tsx` 38). The plan already says "greys for series with red for the one that matters"; this is the evidence that the pages disagree today. → (c)
- **K3. Red carries four jobs.** `--color-accent` is the primary button, the active step and tab, the link ("Open here ›", "Next: …") and the eyebrow, and `--color-danger` is red too: `#d0021b` against `#b91c1c` is only ΔE 11.6 in light (29.9 in dark, where danger is pink). A red "Open here ›" beside a red error row reads as one thing. Proposal for UX, not a decision: keep red for the one primary action and the active state, and give links and eyebrows the ink colour with an underline or "›". → shell in (b), pages in (d)
- **K4. Top bar and sidebar have no band; Home has one.** THEME-LIGHT (b) already plans the band header. Home's glass cards (`rgba(250,250,250,.7)`) and the workbench's flat white panels are the other half of the same gap. → (b) top bar, (d) Home
- **Dark theme.** No other large light surface on dark and no dark one on light. The inventory's 857 literals did not show up as a visible defect outside K1 and K2 on these screens; the guard test in (d) is what keeps it so.

## Which slice takes which finding

| Finding | Slice | Why there |
|---|---|---|
| F1 fonts not shipped, two mono stacks | cohesion | No colour; `index.css` `@theme`, `spa.html`, `package.json` |
| F2 type scale, canvas labels under 9 px | cohesion | No colour; `TopologyCanvas.tsx` text only |
| F3 panel headers | cohesion | Structure and type. Coordinate with (b)'s band header: the band is the header's background, so build the one header first or in the same PR |
| F4 section bodies, Section 7, Library | cohesion | Card recipe and form controls |
| F5 radius and surface tokens | cohesion | No colour |
| F6 empty states, Results frame | cohesion | Wording and layout |
| F7 stepper, banner, floating button | cohesion | Layout |
| F8 skeleton | cohesion (if it fits) | Loading state |
| F9 Guided and Expert name sections differently | cohesion | Wording and eyebrows; `PANEL_META` |
| CV1 canvas colours off the carrier tokens | (c) | Canvas colours; extend the #1336 pin |
| CV2 selection looks like a carrier colour | cohesion | A design choice: question 3 below |
| CV3 three canvas empty states | cohesion | Item 5 |
| CV4 canvas type and legend heads | cohesion | Item 2 |
| K1 attribution badge | (c) | Canvas colours |
| K2 chart palettes | (c) | Plotly and canvas series colours |
| K3 red for four jobs | (b) shell, (d) pages | Link and eyebrow tokens: shell first |
| K4 band on the top bar, Home cards | (b), (d) | The plan's band header |

The cohesion slice is `.scratch/ui-cohesion/issues/01-cohesion-slice.md`: F1–F7, F9 and CV2–CV4, about 1,500 changed lines, which is the PR-SIZE-1 limit, so it ships in two PRs, (A) fonts, type and radius tokens, canvas labels, then (B) headers, names, cards, empty states, chrome and canvas selection. F8 is optional. The issue file says where the split falls.

## Open choices (HOLISTIC-1)

Each is a real choice for the whole tool, not for one screen. The recommended option goes into the cohesion slice unless the owner picks another.

1. **One panel-title size.** (A) 15 px, the Guided section title, with a 9 px eyebrow, so Guided, panels and the band header share one title size; no new size (recommended). (B) 16 px, what `PageHeader` and Results use today; adds a second heading size beside Guided's 15. (C) 12 px, what 13 panels use; panel titles then sit below the Guided section title.
2. **What colour are links and eyebrows?** (A) Ink with an underline or "›" for links and eyebrows; red only for the primary action and the active state (recommended: fixes the accent/danger overlap, ΔE 11.6 in light). (B) Keep accent red for all of them. (C) The deep red `#a8000f` for links, accent red for actions only; separates them a little, still red.
3. **How is a selected canvas node shown?** (A) A 2 px ink ring plus a soft accent halo, the same on every carrier (recommended: independent of the carrier colour, works in dark). (B) Keep the carrier-coloured border, thicker, add a corner check. (C) Fill the node with its carrier tint only; weakest in dark.
4. **What does a panel's eyebrow say?** (A) The flow section it belongs to ("SCENARIOS & OPTIMISATION", "READINESS AND RUN"), as the "Next:" buttons and the Guided stepper already do (recommended). (B) Keep the area words (RUN, REPORTS, TIME…) and add the section name to the sidebar only. (C) Drop the eyebrow; the title alone.

**Owner answers (17 Nov 2026, via the coordinator; UX agreed):** UI-COHESION Q1 = (A) 15 px title with a 9 px eyebrow. Q2 = (A) ink with underline or "›" for links and eyebrows; red only for the primary action and the active state. Q3 = (A) 2 px ink ring plus accent halo on every carrier. Q4 = (A) the eyebrow is the flow section name. The UX conditions are in `issues/01-cohesion-slice.md`.

## Status of the ui-modern audit's findings (9 Nov, master `64686f9d`)

| # | Finding | On `882d7114` |
|---|---|---|
| 1 | Decision screens have no chart | Not rechecked; owned by VISUALS-1 / #1565 |
| 2 | Canvas opens un-fitted | Still at 0.5 zoom; labels at 8 px (F2) |
| 3 | Section 7 is a raw form | Still raw (F4) |
| 4 | Walls of "assumed" rows | Improved: Section 3 and 8 now fold them into an "Assumptions · N defaults" card. Microgrid Section 5 still shows seven long rows |
| 5 | Charts do not read as one system | Still (K2) |
| 6 | Stepper clipped | Still (F7) |
| 7 | Four sections are a sentence and a link | Fixed: they list their rows (`SectionHoldings`); now a style gap (F4) |
| 8 | Always-on chrome | Still (F7) |
| 9 | Home ignores the theme | Fixed |
| 10 | Loading and empty states | Partly (F6, F8) |

## How the numbers were taken

Computed styles per screen from the running page: the first font family of every visible text node weighted by characters, the share at each size, the first opaque background up from the main region, the first four headings, and the border radius of every bordered box over 4,000 px². `facts.jsonl` is written beside the shots. The 8 and 8.5 px labels were found by listing the visible elements under 9 px.
