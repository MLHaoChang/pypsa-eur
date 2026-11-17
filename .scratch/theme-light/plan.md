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
| accent | `--color-accent` | deck red (extract) | `#ff5252` |
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
- 3:1 for `axis` and `border-3` (UI components, WCAG 1.4.11);
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
