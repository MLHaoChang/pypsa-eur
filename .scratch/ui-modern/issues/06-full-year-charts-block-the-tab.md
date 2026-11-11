<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Full-year time-series charts block the Results tab

Status: in-review (PR on claude/ui-modern-06-full-year-charts; reviewer UX)
Type: bug
Model: Sonnet. Reviewer: UX.

Found while building ui-modern 02 slice 3. Measured on master `e4766504` with the
`eh_ipp_hybrid` template (8760 snapshots), Expert > Results > Capacity expansion,
Vite dev build, headless Chromium. The scripts are
`.scratch/ui-modern/shots-02/tools/settle.mjs` (DOM mutations and long-task time
per second after a tab opens) and `profile.mjs` (CPU profile, top self time).

## What happens

| Tab | Opens in | While idle |
| --- | --- | --- |
| Operation > Dispatch > Full timeframe | 12.9 s | a 10 s blocking redraw (92,799 DOM mutations) every 10 s: the tab never responds |
| Curtailment | 1.1 s | a 0.8–1.7 s redraw (15,464 mutations) every 5 s |
| Costs & prices | 1.7 s | a 0.4 s redraw (72 mutations) every 5 s |

## Causes

1. **Recharts measures every x-axis tick.** A category `XAxis` over 8760 snapshots
   measures each label with `getBoundingClientRect` to decide which ticks fit. In
   the Curtailment profile that call was 77 % of the time. Recharts caches 2,000
   string sizes, fewer than a year of hourly stamps, so every draw measures again.
2. **The whole Results tree re-renders every 5 s** with unchanged data. Suspected
   source, not confirmed: `useProjectMismatchDetection` polls `/api/network/meta`
   every 5 s and reads `isFetching`, so its host re-renders twice per poll. Tabs
   whose memos key on a fresh object each render (`resolveRange(...)` returns a new
   `{ from, to }`) then rebuild their series and redraw their charts.

## What is already fixed

Slice 3 (the hour-by-day map) fixed Curtailment only: the window object is kept
per window and the over-time chart element is kept while its series is the same.
Full year, idle: 0.1 s and 46 mutations per 5 s. `Curtailment.test.tsx` guards it.

## What to build

- Give full-year time-series charts explicit x-axis ticks (or a numeric `interval`),
  so Recharts does not measure 8760 labels. One shared helper next to `CHART_AXIS`.
- Stop the 5 s re-render reaching the Results tabs: confirm the source with the
  React profiler, then narrow the subscription or memoise the tab body.
- Dispatch > Full timeframe: keep each group's chart element while its series is
  the same, as Curtailment does.

Done when `settle.mjs` on `eh_ipp_hybrid` reports the three tabs idle (no long task
over 200 ms after the first 5 s) and Dispatch > Full timeframe opens in under 3 s.

## Comments

### 2026-11-11: triage

Checked on master: `useProjectMismatchDetection` still polls every 5 s and no shared explicit-ticks helper exists next to `CHART_AXIS` in `frontend/src`. Verdict: ready-for-agent.

- Blocked by: none. Do not touch `.scratch/ui-modern/issues/` files other than this one (open PRs #1343, #1353, #1359 edit its neighbours).
- Likely files: `pypsa-gui/frontend/src/hooks/useProjectMismatchDetection.ts`, the shared `CHART_AXIS` module, the Dispatch full-timeframe, Curtailment and Costs & prices tabs, their tests. Reviewer: UX.
- The Done-when in the body is the acceptance, measured with `shots-02/tools/settle.mjs` on `eh_ipp_hybrid`.

### 2026-11-11: built

Measured with `settle.mjs` on `eh_ipp_hybrid` (Vite dev build): Dispatch > Full timeframe opens in 1.2 s (was 12.4 s) and every later second is idle; Curtailment and Costs & prices have no long task after 3 s.

- Source of the re-render confirmed: `useProjectMismatchDetection` read `isFetching` in `App`, so `App` and the Results tree re-rendered twice per poll. The hook now lives in `ProjectMismatchWatcher`, a component that renders nothing; a test counts the parent's renders across polls. (Hook-in-`App` was confirmed by that test and by the idle mutations stopping, not by the React profiler.)
- `timeAxis(n)` (next to `CHART_AXIS`) gives a series over 200 rows a numeric `interval`, so Recharts measures no label. `thinRows` draws a full year at 1500 whole rows (first, last, the largest row of each bucket), for chart `data` only; CSV and totals use the full rows.
- Dispatch keeps `range` per window and each group's main chart element while its series is the same.
- Not done: LoadFlow, LostLoad and other full-year axes still use the default ticks.
