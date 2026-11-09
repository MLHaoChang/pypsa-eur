<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Screenshot audit of the main screens (ui-modern issue 01)

Date: 9 Nov 2026. Master 64686f9d. Owner decisions: UI-MODERN-1, DENSITY-1, VISUALS-1, REVIEW-VERIFY-1.

## What was shot

- Two projects, both created from their template: the **Data Center Energy Hub** (`dc-`) and the **Island Microgrid** (`mg-`).
- **Guided**: Home, the New project dialog, all 10 sections of the hub-design guide, and the verdict after Run study.
- **Expert**: the network canvas in each map mode, every sidebar panel, and every Results tab and sub-tab, after one dispatch run (the data centre also after the grid checks).
- Each in **dark and light**. Viewport 1280×800, downscaled to 800 px wide. 210 shots in `shots/`, named `<project>-<mode>-<theme>-<screen>.jpg`.
- Scored against `docs/agents/review-ux.md` and `ui-ux-pro-max` (`--domain ux` for empty and loading states, `--domain chart` for time series and KPI).

**Not covered: the chat dock in use.** No `ANTHROPIC_API_KEY` is set here, so the assistant stays off. Only its "needs an API key" state is in the shots (right panel of `home-dark.jpg`). Assistant cards, choice cards and result cards were not seen. Ask for a second pass with a key before 02–04 are scoped around the chat.

**Not flagged, by DENSITY-1:** the 9–11 px text and Inter.

## Findings, ranked by visible impact

### 1. The decision screens have no chart (high)

Guided Results, the screen the user is sent to for the answer, shows a verdict sentence, one cost tile, a ranked list and four status tiles. It has **0 charts**, in both projects. Expert has 12 to 14 charts, but they sit three levels down (Results > Capacity expansion > Operation) and none is on the Results Summary or the Reliability tab, which is a wall of prose and collapsed accordions.
VISUALS-1 asks for one chart per question with the number in its title; today the question "did it pass?" has none.
Shots: `dc-guided-dark-verdict-fresh.jpg`, `mg-guided-dark-verdict-fresh.jpg`, `dc-expert-dark-res-summary.jpg`, `dc-expert-dark-res-reliability.jpg`, `dc-expert-light-res-reliability.jpg`.
Lookup: `--domain chart` says a stat card for under four points and a line for a time axis; both exist in the app, only not here.

### 2. The network canvas opens un-fitted (high)

The canvas opens at zoom 0.5 and offset, so generator nodes sit under the left toolbar, the bottom nodes are cut by the table below, and labels render at about 5 px (10.5 px at 0.5). That is a zoom artefact, not the DENSITY-1 text size. The right 25 % is a permanent empty Properties panel ("Click a bus, line, or asset…"). Fit-to-view exists as a button but is not applied at load.
Shots: `dc-expert-dark-canvas.jpg`, `dc-expert-light-canvas.jpg`, `mg-expert-dark-canvas.jpg`, `dc-expert-dark-cv-select.jpg`.
Related: the carrier and voltage colours (red generators, blue grid bus, green PV, purple battery) are explained only by a collapsed LEGEND chip, so the first view has colours without a key. On the map modes the legend panel and the "No bus has a location yet" card overlap (`dc-expert-dark-cv-hybrid.jpg`, `mg-expert-light-cv-hybrid.jpg`). The Site 3D tab opens on a text-only empty state (`dc-expert-dark-cv-site3d.jpg`).

### 3. Guided section 7 is a raw form (high)

Commercial & parties shows the import-tariff editor as bare fields with lowercase key names (`id`, `name`, `kind`, `unit`, `measured on`), a row of six "Add … item" text links and unstyled tables. It is headed "complete" while its own text says "The project has no import tariff: build one". The microgrid version has 21 selects on one screen. Every other Guided section is a card with a sentence; this one is a different product.
Shots: `dc-guided-dark-s07.jpg`, `dc-guided-light-s07.jpg`, `mg-guided-dark-s07.jpg`.

### 4. Walls of "assumed" rows with raw keys (high in the microgrid)

Sections 3 and 5 stack up to seven orange warning rows. Each carries the same warning icon as a real problem, two links ("Open here ›", "Open in Expert") and engineering keys in the text (`p_max_pu 0`, `tau_f`, `RoCoF`, `island_constraints`). The data centre has 5 such rows, the microgrid 15. The microgrid's Connection & grid section is headed "not needed" and still lists four.
Shots: `dc-guided-dark-s03.jpg`, `mg-guided-dark-s03.jpg`, `mg-guided-dark-s05.jpg`.
Rule: `review-ux.md` "One entry point per feature, one bulk action, no duplicate buttons" (two links per row, no accept-all).

### 5. Charts do not read as one system (medium-high)

Eight charts in Operation, each with its own palette, height and header. Five carry the identical title "System dispatch × 52.14 weighted". The Expansion CAPEX chart draws one bar across the full width for one asset. The floating chat button covers the right end of the lowest chart on screen. The waterfall and the cost tiles above it repeat the same totals.
Shots: `dc-expert-dark-ops-build-1.jpg`, `dc-expert-dark-ops-operation-0.jpg`, `dc-expert-dark-ops-operation-1.jpg`, `dc-expert-dark-ops-operation-2.jpg`, `dc-expert-light-ops-operation-0.jpg`, `dc-expert-dark-ops-costs-0.jpg`.
Lookup: `--domain chart` asks for direct series labels and line styles, not hue alone; the dispatch stacks use hue only.

### 6. Guided stepper is clipped (medium)

At 1280 px the ten-step stepper overflows and cuts the next step mid-label ("5 A"). The active step is often half off-screen (`dc-guided-dark-s07.jpg` starts at "Loads & demand").
Shots: `dc-guided-dark-s03.jpg`, `dc-guided-dark-s09.jpg`, `mg-guided-dark-s05.jpg`.

### 7. Four of ten Guided sections are a sentence and a link (medium)

Carriers, Loads, Assets and Time series each show "N assets. Anything this section still needs is listed above…" and an "Open … in Expert ›" link. Without the assistant, this is the only way in, and it switches the mode. The sections look finished (green tick) but offer nothing to do.
Shots: `dc-guided-dark-s02.jpg`, `dc-guided-dark-s04.jpg`, `dc-guided-dark-s05.jpg`, `dc-guided-dark-s06.jpg`.

### 8. Always-on chrome (medium)

The floating "Chat here" button covers the bottom-right of every screen (charts, tables, the canvas). In Guided the "Every step below works by hand… Set up the assistant" banner repeats at the top of all 10 sections and returns on each visit (it has a close button).
Shots: any Expert or Guided shot; `dc-expert-dark-ops-operation-1.jpg` shows the button over a chart.

### 9. Home ignores the theme (medium)

Home and the New project dialog stay dark when the workspace is light, and the theme toggle lives only inside a project. Light users see a dark landing page, then a light app.
Shots: `home-light.jpg`, `newproject-light.jpg`, then `dc-guided-light-s03.jpg`.

### 10. Loading and empty states (medium)

- On opening the microgrid the Grid connection select first reads "Strong — import whenever needed" under "Checking the site…", then flips to "None — the site runs as an island". A wrong value is shown while loading (`--domain ux`: Loading Indicators, "preserve layout focus").
- Results Summary rows have blanks: the Reliability row has no text and Control says "No control results yet" in plain grey (`dc-expert-dark-afterlopf.jpg`).
- A dispatch run in Expert turns the Guided verdict "Out of date" with a banner plus chips on each tile (`dc-guided-light-s10.jpg`). It is correct, but three out-of-date marks on one card is loud for one fact.

### 11. Wording rules from review-ux.md (low visible impact, many places)

- Em dashes join clauses throughout Guided ("complete — 9 asset(s)", "to check — grid_import, 40 MW", "Strong — import whenever needed"); the rule is a colon.
- "9 asset(s)", "load(s)" plural patterns.
- Raw keys in user text, as in finding 4.
Shots: `dc-guided-dark-s03.jpg`, `dc-guided-dark-s04.jpg`, `dc-guided-dark-s05.jpg`.

## Held up well

- In the shots reviewed, light and dark both render with readable contrast; the Guided cards, tiles and tables share one token set.
- The "Out of date" freshness wording matches `ResultState`.
- Units are on the cost tiles and chart axes (`€34.7 M`, `EUR (annualised)`, `MW`).
- Expert's sidebar groups (Set-up, Model, Question & run, Results & hand-off) match the Guided step order.

## Suggested groupings for 02–04

The coordinator re-scopes these. Findings that belong together:

- **Results visuals**: 1, 5 (one chart per question, shared chart styling). Overlaps VISUALS-1 and DASHBOARD-1.
- **Canvas and chrome**: 2, 6, 8, 9.
- **Guided forms and prompts**: 3, 4, 7, 10, 11.

## How the shots were taken

Playwright (Chromium) against the local API and Vite, signed in as the dev admin. Theme set with the in-app toggle (the browser colour scheme is ignored). Chat panel in its "no API key" state. The Expert pass ran one dispatch (Run LOPF); the data centre also ran the grid checks and Run study in Guided first, so its Guided Results shots after the Expert solve show the "Out of date" state. The `-verdict-fresh` shots are the fresh verdicts taken before it.
