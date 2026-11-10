<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Energy chart kit

Status: ready-for-agent
Blocked by: 01
Type: feature
Model: Opus for the shared theme API, Sonnet for each chart. Reviewer: UX.

The issue is cut into slices (PR-SIZE-1). Slices 1 to 5 are done in their PRs; the
rest are ready for an agent and build on their API.

## Slice 1: shared chart theme (this PR)

From the screenshot audit (`.scratch/ui-modern/audit.md`, findings 5 and the
chat-button part of 8). One Recharts theme every chart uses,
`pypsa-gui/frontend/src/components/charts/chartTheme.tsx`:

- `carrierColour(carrier, i)`: carrier colour tokens (`--color-carrier-*`, light + dark in `index.css`); unknown carriers get `--color-series-1..8`. `colourForCarrier` in `results/shared.tsx` delegates to it; a saved SVG resolves the tokens.
- `CHART_HEIGHT.sm|md|lg`, `CHART_TICK` (mono, tabular figures; DENSITY-1 sizes kept); unit-labelled axes stay `yAxisLabel`.
- `seriesDash(i)`, `directLabel(name, i)`, `thickestIndex(rows, key)`: series that differ by more than hue.
- `<ChartFrame>`: one header (title, the chart's number in mono, hint, actions), then the chart, then the source line (solve, window, period, plus a chart's own basis such as "weighted × 52.14") and an "Out of date" mark when the dispatch is stale. Source and freshness come from `<ChartSourceProvider>`, set once in `Results.tsx`. `<ChartSourceLine>` is the footer alone, for cards that keep their own header.
- Applied to Results > Capacity expansion > Build and > Operation (Dispatch, Energy flow, Load shape, Storage cycling, Lost load). Those scroll views leave room below the last chart for the "Chat here" launcher.

Done when the existing Results charts above use the theme, `chartTheme.test.tsx` covers the API, dark and light shots show before and after, and the PR merges with the UX reviewer's OK.

## Slice 2: duration curve (done in its PR)

`pypsa-gui/frontend/src/pages/results/DurationCurve.tsx`: `<DurationCurve>` on `ChartFrame`, and `durationRows()`, which sorts each series on its own over % of hours. A series is `{ key, name, colour, csv }`; the first is solid, later ones dashed and named on the line; `mean` draws the labelled reference line. Used by Dispatch (load and residual load) and Prices (system mean price). The file sits in `pages/results/` so `chartStandard.test.tsx` guards it (it is in `THEMED`). The two-run price duration curve in Compare (`CompareView.tsx`) keeps its own chart.

## Slice 3: hour-by-day heatmap (done in its PR)

`pypsa-gui/frontend/src/pages/results/HourDayHeatmap.tsx`: `<HourDayHeatmap>` on `ChartFrame`, one column per calendar day and one row per hour of day, so a year is 365 × 24 cells. `hourDay.ts` holds the data side: `hourDayGrid()` (mean of sub-hourly snapshots; `null` where there is no snapshot, drawn hatched), `heatScale()` (one scale over the grids shown together; `fromZero` for quantities that start at zero), `heatStrength()`, and `spansPeriods()` with the `PICK_ONE_PERIOD` sentence for an all-periods view. The key writes both ends of the scale as text; the hover readout and the CSV carry every value. It is in `THEMED` in `chartStandard.test.tsx`.

Used by Prices ("Price by hour and day", lowest to highest price), Curtailment ("Curtailment by hour and day", from zero) and Dispatch ("Storage SoC by hour and day · <carrier>", one per storage carrier, all on 0–100 % of energy capacity; a carrier with no capacity on record uses the highest state of charge in the window and its key says so). The maps cover storage units, not stores. In a multi-period run 100 % is the final cumulative capacity. The older `StorageHeatmap`, the Load shape heatmaps and Curtailment's area chart move to the theme in slice 6.

Full-year runs: see `06-full-year-charts-block-the-tab.md`.

## Slice 4: Energy-flow Sankey (done in its PR)

`EnergyFlowsTab.tsx`: the Sankey sits in a `ChartFrame` ("Energy flow · <period>") with the total delivered or lost as its header number, the shared SVG and CSV actions, `ChartZero` for a window where no energy moved, and the frame's source line. The strip above keeps the conservation chip, the period picker, the losses share and the undelivered marks. The Value flows Sankey in Investment keeps its own card until slice 6.

For slice 6 (UX, #1354): on a multi-period run with "Aggregated" selected the diagram shows the first period (title "Energy flow · 2027") while the source line says "all periods". Left as on master in slice 4.

## Slice 5: tornado (this PR)

`pypsa-gui/frontend/src/pages/results/TornadoChart.tsx`: `<TornadoChart>` on `ChartFrame`, one row per driver on one value axis. A bar runs between the results at the driver's low and high input and is split at the labelled base-case line: the danger token below it, the success token above, with a key under the plot saying so. `baseWord` is the page's word for the base case; the Robustness check passes "Centre", the word its verdict uses. Each end is written out ("Low 4.9 %: € 4.3 m (+€ 808 k)"), the lower result on the left; the header carries the largest swing and its driver; the axis names the unit once. A dashed zero line is drawn only when zero is in range, named by its "0" tick on the axis below (the base case is named above the plot, so the two never collide). Reference lines are drawn across the bars only, never through a row's text. A case with no result reads "High case: not established", draws no bar on that side and never a bar at 0. `domain` fixes the axis so two options side by side share one scale (no second chart is wired yet). SVG and CSV (driver, input low and high, unit, NPV low and high, swing). It is in `THEMED` in `chartStandard.test.tsx`.

Used by Decision hub > Robustness check (`pages/decision/Robust.tsx`), which keeps the row order of `sortTornado` (largest swing first, an unknown swing last), the pending and skipped lists, the status and the per-driver notes. The chart has no source line there: the method sentence above the plot is the study's basis.

## Later slices
6. **The rest of Results on the theme**: Prices, Economics, AC power flow, Investment, Aggregated overview (they already get the tokens and ticks through the shared constants, not `ChartFrame`).
7. **Reports reuse the exact chart specs** the user saw.

Each new type has a unit test and shots in both modes.
