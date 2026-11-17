# THEME-LIGHT-1: light default theme, dark as a toggle

Status: ready-for-agent

Owner decisions: THEME-LIGHT-1 (inspired light theme; light default, dark toggle;
WCAG AA pinned in tests), DENSITY-1 (Inter, 9–11px text), GC-TEXT-1, UI-MODERN-1.

## What exists today (base 6b559a5e)

- `src/index.css` already has two themes: `@theme` holds a light ramp, and
  `:root[data-theme="dark"], [data-pypsa-surface="brand-dark"]` overrides it with
  the red-on-black ramp. Token names are already neutral (`bg`, `bg-2`, `panel`,
  `ink-*`, `text`, `muted`, `accent`, `on-accent`, `border*`, `carrier-*`, `series-*`).
- `uiStore.storedTheme()` returns the stored value, else `'dark'`. `THEME_KEY` is
  only written by `setTheme`/`toggleTheme`, so a stored value is always an
  explicit user choice.
- `spa.html` ships `<html data-theme="dark">` and a boot script that switches to
  light only when `'light'` is stored.
- Sign-in (`index.html` = `public/login.html`) and `public/brand.css` are dark only
  (`--brand-black`, `--brand-red`, …).
- Projects home maps `--brand-*` onto the light tokens via
  `[data-pypsa-surface="brand-light"]`.
- Toggles: Sidebar (`layout/Sidebar.tsx:1145,1179`) and the command palette.
- Hard-coded colours: 857 literals in 137 files (`colour-inventory.md`).

## Token design

Each semantic token has a light and a dark value. Light lives on `:root`
(Tailwind `@theme`, so it is the fallback); dark lives under
`:root[data-theme="dark"]`. Existing names are kept, so no call site is renamed.

| Role (brief) | Token | Light (proposal) | Dark (today, unchanged) |
|---|---|---|---|
| surface | `--color-bg` | `#ffffff` | `#151112` |
| surface-2 | `--color-bg-2` | `#f8f7f7` | `#1b1617` |
| panel | `--color-panel` / `-2` | `#ffffff` / `#f4f1f2` | `#211b1c` / `#282122` |
| ink | `--color-text`, `--color-ink-900` | `#17181c` | `#f4eaea` |
| ink-dim | `--color-muted`, `--color-ink-500` | `#5a5f69` | `#b9a7a9` |
| accent | `--color-accent` | `#d0021b` | `#ff5252` |
| accent-on | `--color-on-accent` | `#ffffff` | `#14090a` |
| border | `--color-border` / `-2` / `-3` | `#ebe6e7` / `#d9d2d4` / `#bdb4b6` | unchanged |
| band (new) | `--color-band`, `--color-tint` | `#fbf3f4`, `#fbf4f5` | `#1b1617`, `#1e1718` |
| band gradient (new, raw) | `--band-gradient` | white → band, soft red radial | bg-2 → bg, red radial |
| chart series | `--color-carrier-*`, `--color-series-*` | unchanged unless extract clashes | unchanged |

The accent, band and gradient values are placeholders until the style extract
arrives. The light accent must reach 4.5:1 on `bg`, `bg-2`, `panel` and `band`,
and `on-accent` must reach 4.5:1 on it.

`public/brand.css` gains the same split: neutral names (`--brand-bg`,
`--brand-surface`, `--brand-ink`, `--brand-ink-dim`, `--brand-accent`,
`--brand-on-accent`, `--brand-line`, `--brand-band`, `--brand-band-gradient`, …)
with light on `:root` and dark under `:root[data-theme="dark"]`. The old
`--brand-black`/`--brand-red*` names stay as aliases until slice (d) moves the
projects home off them. `brand.theme.test.ts` is extended to pin light ↔ `@theme`
as well as dark ↔ dark block.

## Theme selection

Order: stored explicit choice → `prefers-color-scheme` → light.

- `uiStore.storedTheme()` falls back to `matchMedia('(prefers-color-scheme: dark)')`,
  then `'light'`. No schema bump: an explicit choice (either theme) is kept;
  users who never chose move to the new default.
- `spa.html` and `index.html` ship without a hard-coded `data-theme="dark"`; one
  boot script, same order, sets `data-theme` before first paint, and the
  pre-paint background follows it. `index.html` and `public/login.html` stay
  byte-identical.
- The Sidebar and palette toggles keep working; their copy reads
  "Light theme" / "Dark theme".

## Contrast tests (new `theme.contrast.test.ts`)

Parses `index.css` and `brand.css`, and for both themes asserts:
- 4.5:1 for text tokens (`text`, `ink-900…500`, `muted`, `legend`, `accent`, `danger`, `info`)
  on `bg`, `bg-2`, `panel`, `panel-2`, `band`, `tint`, and `accent-50` for `accent`;
- 4.5:1 for `on-accent` on `accent` and `accent-700`;
- 3:1 for `axis`, `focus` and `border-3` (UI components, WCAG 1.4.11);
- 4.5:1 for `on-status` and `bg` text on every status fill at 90–100 % opacity
  (`statusFill.guard.test.ts` keeps text-bearing fills on those foregrounds);
- the same pairs for the sign-in page's `--brand-*` tokens.
The existing chart guard (`chartThemeTokens.test.ts`) keeps the 3:1 and ΔE ≥ 10 pins.

## Slices (one PR each, PR-SIZE-1)

- **(a)** tokens in `index.css` + `brand.css`, selection order, boot scripts,
  contrast tests, sign-in page (`index.html`, `public/login.html`,
  `pages/auth/*`, 28 literals).
- **(b)** workbench shell and panels: `layout/`, `components/` (≈253 literals),
  band header on the top bar, tinted properties panel. `PropertiesPanel.tsx` and
  `AssetFields.tsx` only after the ui-modern 05d+11 PR merges; `ChatPanel.tsx`
  after #1556; `SolveQueuePanel.tsx` after #1561.
- **(c)** results, charts and maps (≈391 literals): Plotly layout colours read
  the tokens; map basemap and canvas colours get a light/dark pair.
  Business-case results only after #1559.
- **(d)** remaining pages, the projects home on the new `--brand-*` names, the
  rest of the sweep, and a guard test that fails on a new raw colour literal in
  `src/` outside the token files (an allowlist file for the few that must stay
  literal, such as canvas code that cannot read CSS variables).

## Mock

`mock/screens.html` + `mock/tokens.css` (proposal values), shot by
`mock/shoot.cjs` into `shots/`: workbench, projects home and results, light and
dark. Not app code.

## Comments

**Owner style extract (via coordinator, 2026-11-17).** The source's exact red and
typeface stay out. Light palette chosen from it: accent `#d0021b` (≥ 4.85:1 on
every light surface and band stop; white on it 5.67:1), deep `#a8000f`, ink
`#222222`, muted `#5c5c5c`, grey ramp `#4d4d4d / #737373 / #b3b3b3 / #d9d9d9 /
#f4f4f4`, band `#ffefef`, tint `#f4f4f4`. Charts: greys for series with red for
the one that matters lands in slice (c).

**UX plan GO @2442e503, conditions:**
1. Contrast tests cover status tokens (warn, success, danger, info), the chart
   axis and legend, and selection/focus rings (3:1 on surfaces and band), in both themes.
2. The band gradient is decoration only; no text on the radial's strongest
   stop, and the test uses the worst stop.
3. Every slice ships real-app shots in Guided and Expert, both themes; no mocks
   for (b)–(d); retake shots with a toast or crashed panel.
4. Toggle copy names the action in Sidebar and palette alike.
5. A carrier colour that clashes on white is fixed in its one token, with the
   pinned test updated; no per-chart overrides.
6. `index.html` and `public/login.html` stay byte-identical.
7. Re-send the final accent values; placeholders passed only the role review.

**GK B2 plan GO, slice (a), conditions:**
- T1. No THEME_SCHEMA bump; the comment says why; stored 'dark' + schema 2 on a light OS opens dark.
- T2. Boot script sets data-theme for both outcomes; the pre-paint background
  matches; schema migration lines byte-identical across copies.
- T3. The parity test runs the real script text from each HTML file over
  {stored light/dark/none/garbage/throwing storage} × {OS dark/light/no
  matchMedia/throwing}; a mutant in either copy goes red.
- T4. The contrast test parses the shipped CSS, composites alpha tokens, and
  fails on a token it cannot resolve; shown red with a low-contrast token.
- T5. firstRunOrder and isLegacyKey stay green and unchanged in meaning; no
  storage write added at import.
- T6. The PR body lists surfaces that still hard-code dark colours.
- T7. The PR body confirms GC-TEXT-1.

## UI-COHESION-1 findings for slices (b)–(d) (appended 2026-11-17)

Source: `.scratch/ui-cohesion/audit.md` (300 real-app screens: Guided, Expert, Results and Home, light and dark, three templates). The audit found few colour defects: page surfaces are `#ffffff` / `#151112` on all 147 screens per theme, and no large surface contradicts its theme. Four items belong to these slices; everything else is in the cohesion slice (`.scratch/ui-cohesion/issues/01-cohesion-slice.md`), which does not change a colour value.

| Item | Slice | What and where | Shot (`.scratch/ui-cohesion/shots/`) |
|---|---|---|---|
| K1 | (c) | The React Flow attribution badge is a pale grey box on the dark canvas, its text unreadable. Style `.react-flow__attribution` with tokens in both themes; it sits beside `<Background>`/`<Controls>` at `pages/TopologyCanvas.tsx:3134-3142`. | `dc-expert-dark-canvas.jpg` (bottom right) |
| K2 | (c) | Chart palettes are chosen per page: Capacity expansion is one orange and one charcoal bar in light, orange and pale pink in dark; the Sankey is grey-blue; heatmaps blue-grey. The plan's "greys for series, red for the one that matters" is the fix; the files are `CapacityExpansion.tsx`, `Dispatch.tsx`, `energyFlows.ts`, `StorageHeatmap.tsx`, `ResultsViewer.tsx`. | `dc-expert-light-res-capex.jpg`, `dc-expert-dark-res-capex.jpg`, `dc-expert-light-res-flows.jpg` |
| K3 | (b) shell, (d) pages | Red has four jobs: primary action, active step/tab, link ("Open here ›", "Next: …") and eyebrow, and `--color-danger` is red too. `#d0021b` against `#b91c1c` is ΔE 11.6 in light (29.9 in dark). Proposal for the UX reviewer, not a decision: links and eyebrows in ink with an underline or "›", red kept for the primary action and the active state. Shell first (`layout/`, `components/`), pages in (d). | `dc-guided-light-s03.jpg`, `dc-expert-light-p-issues.jpg` |
| K4 | (b) top bar, (d) Home | Home has the band gradient and glass cards; the workbench top bar and sidebar are flat white. The band header already planned for (b) closes the gap; Home moves onto the neutral `--brand-*` names in (d). | `home-light.jpg` against `dc-guided-light-s03.jpg` |

Two couplings with the cohesion slice:

- **The panel header.** Expert panels have two header systems (a 12 px breadcrumb in `App.tsx` `FullPageTab`, a 16 px `PageHeader` in `components/PageKit.tsx`). (b)'s band header lands on that one component, so the cohesion slice's header (item 3) goes first, or (b) takes the header's background token from it. Whichever merges second rebases onto the first.
- **Radius and card surface.** The cohesion slice adds `--radius-card` / `--radius-control` and changes `rounded-[10px]` on cards. (b)–(d) must not add new `rounded-[Npx]` cards while it lands.

Shots for (b)–(d) per UX condition 3 can reuse `pypsa-gui/frontend/scripts/ui-cohesion-shots.mjs`: `THEMES`, `MODES`, `ONLY` and `WAIT` pick what it takes, against the real API.
