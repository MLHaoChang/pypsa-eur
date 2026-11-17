<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Cohesion slice: one type scale, one header, one card, one empty state

Status: ready-for-agent
Gated: UX
Type: implementation (frontend, no colour)
Owner decisions: UI-COHESION-1 (the non-colour items go into one slice sized for one PR), DENSITY-1 (9–11 px text and Inter stay), THEME-LIGHT-1 (colour is not in this slice).
Source: `.scratch/ui-cohesion/audit.md`, findings F1–F7 (F8 optional).
Model: Sonnet. Start after #1565 (ui-modern 05d+11) and #1556 merge; they touch `PropertiesPanel.tsx`, `AssetFields.tsx` and `ChatPanel.tsx`, which this slice does not need.

## What to do

The same level of heading is 12, 12.5, 13, 14, 15 or 16 px depending on the section; the card is 4, 8 or 10 px; Expert panels have two header systems; "not run yet" is worded six ways; and Inter is declared but not shipped. Make each of those one thing. Use the app's existing words: no new copy except the one empty-state line each tab already has.

1. **Ship the fonts (F1).** Self-host Inter (variable) and JetBrains Mono as woff2 (`@fontsource-variable/*` or files under `public/fonts/`; both are SIL OFL, so confirm the licence line in `REUSE.toml`), with `font-display: swap`, loaded by `spa.html`. Rename the `index.css:149-150` tokens to Tailwind v4's `--font-sans` / `--font-mono` in `@theme`, so `font-sans` and `font-mono` utilities, the hand-written classes (`index.css:404,428,448`) and `TopologyCanvas.tsx:221` all resolve to the same two faces. `public/login.html` keeps its own stack but names the same first font. The app must still render offline (the desktop build).
2. **Type tokens (F2).** Add `--text-*` tokens for the sizes in use (9 micro, 10, 11 body, 12 lead, 13 h3, 15 h2, 16 h1) as `@theme` names that do not shadow Tailwind's `text-xs`/`text-sm` (so the 734 existing uses stay put). Give headings one recipe each (`SectionTitle`, `PanelTitle`, `BlockTitle` in `components/PageKit.tsx`) and move the cited screens onto it: Guided `SectionView` (15), panel titles (16 or one agreed size, see 3), inner section headings (13, not 12/12.5/14), the Settings h3s. Raise the canvas asset labels from 8 and 8.5 px to 9 px (`TopologyCanvas.tsx:715,732,743,768`). Do not touch the 9–11 px body text. Leave Home's and the New project dialog's display sizes alone unless UX asks; they are a landing page.
3. **One panel header (F3).** Make `FullPageTab`'s breadcrumb and `PageHeader`'s compact row the same component: eyebrow (group, no per-page "· detail"), title at one size, optional subtitle, "Next: …" and Close. The title never truncates before the "Next: …" button (the button wraps under or shrinks). Migrate the 4 `PageHeader` panels and the 13 breadcrumb panels. The header's background is one token so THEME-LIGHT (b)'s band header can take it over.
4. **One card (F4, F5).** Add `--radius-control` (4 px) and `--radius-card` (8 px); replace `rounded-[10px]`, `rounded-xl` and `rounded-2xl` on cards under `pages/hubDesign/`, `pages/results/` and the Guided Results tiles with the card radius. Give `SectionHoldings` (Sections 2, 4, 5, 6) the same card surface as Sections 3 and 8 (`bg-panel`, 8 px, padding). Style the Section 7 tariff form and the Commercial library's controls with the shared `Field`/`Btn`/`Seg` from `PageKit.tsx`: no native radio or "Choose File" text, labels in the panel's own word map, the "IMPORT TARIFF" kicker in the same h3 style as other blocks. Layout and wording stay as they are; this is field styling, not a redesign of Section 7.
5. **One empty state (F6).** Add `components/EmptyState.tsx` (a title line, one sentence, at most one action, `role="status"`), and use it for the Results tabs in the audit's shot: Grid code, Electrical design, Island, Grid risk, Investment. Keep each tab's existing words; make the layout one. Pin the shape with a test that renders each tab's empty case.
6. **Guided chrome (F7).** The ten-step stepper fits at 1280 px (scroll the active step into view, or fold the labels of inactive steps to the number and keep the label for the active one). The "Every step below works by hand…" banner shows once per session, not on every section visit (it already has a close; remember it for the session). Leave the floating "Chat here" button for UX to decide.
7. *(optional, F8)* Skeleton for the four Guided Results KPI tiles: tile-shaped, in an `aria-busy` status region, after about 200 ms (review-ux.md rule).

## Done when

- A shot script run (`node scripts/ui-cohesion-shots.mjs <dir> eh_datacenter eh_microgrid`) on the PR's head shows: one h2/h3 size per level across Guided sections, panels and Results (`facts.jsonl` headings column); one card radius (4 px controls, 8 px cards) in `cardRadii`; one monospace family and Inter loaded from the app, not the system (`document.fonts.check('11px Inter')` true with no system Inter: say how it was checked); no text under 9 px.
- The PR carries before and after shots in Guided and Expert, light and dark, for the screens it moves (the audit's `shots/` names), plus the New project dialog and Home unchanged.
- Unit tests: the fonts and tokens parse out of `index.css` and `spa.html` (the contrast and `brand.theme` tests keep passing); `EmptyState` per tab; the header component renders the same row from `FullPageTab` and from a `PageHeader` panel.
- No colour value is changed. THEME-LIGHT (b)–(d) own colour; the PR names any literal it had to touch.

## Likely files

`pypsa-gui/frontend/src/index.css`, `spa.html`, `package.json` (fonts); `src/components/PageKit.tsx`, `src/App.tsx` (`FullPageTab`), the 17 panel files in `pages/` and `pages/economics/`; `src/pages/hubDesign/SectionView.tsx`, `SectionHoldings.tsx`, `cards/CommercialCard.tsx`, `StepRail.tsx`; `src/components/DocumentValuesPicker.tsx`; `src/pages/economics/CommercialLibraryPage.tsx`; `src/pages/Results.tsx`, `src/pages/results/*` (the five tabs); `src/pages/TopologyCanvas.tsx` (labels only); a new `src/components/EmptyState.tsx`.

## Size and split

About 1,200 changed lines without shots, one PR (PR-SIZE-1), reviewer area UI. If the UX reviewer or the PR's own count says it is over, split here and no other way: **(A)** items 1, 2, 4's tokens (fonts, type and radius tokens, the canvas labels); then **(B)** items 3, 4's cards and forms, 5, 6, 7. (B) starts after (A) merges.

## Conflicts to watch

- THEME-LIGHT (b) restyles the same header and `layout/` files with colour. Merge order: this slice's header (item 3) before (b), or (b) uses the header token from item 3. Tell the (b) builder when this lands.
- #1565 changes `PropertiesPanel.tsx`/`AssetFields.tsx`; #1556 `ChatPanel.tsx`; #1561 `SolveQueuePanel.tsx`, which the Solve queue panel header (item 3) reaches only through `FullPageTab`. Do not edit those three files.
- Not checked when this was written: whether any open PR changes `Results.tsx` or `pages/results/*`. Before item 5, compare each open PR's changed files with the Likely files below.
