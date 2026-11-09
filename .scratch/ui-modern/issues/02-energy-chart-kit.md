<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Energy chart kit

Status: needs-triage
Blocked by: 01
Type: feature
Model: Opus for the shared theme API, Sonnet for each chart. Reviewer: UX.

## What to build

One Recharts theme used by every chart: carrier colour tokens (`--color-carrier-*`), unit-labelled axes, tabular mono numbers, shared axes and order across base and sensitivity cases, a source run/window line and an out-of-date mark (VISUALS-1). Then the chart types energy studies expect:
- stacked dispatch;
- duration curve;
- 8760 hour-by-day heatmap (price, state of charge, curtailment);
- energy-flow Sankey;
- tornado/delta for sensitivities.

## Done when

The existing Results charts use the theme, each new type has a unit test and shots in both modes, and reports reuse the same chart specs.
