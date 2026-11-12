<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Results: best system per architecture, winning-system-type map, grid-extension break-even

Status: needs-triage
Type: task
Gated: yes (GK B, UX)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

Three result views HOMER Pro users expect:
- **Categorized results:** the best system for each architecture (for example PV + BESS, PV + genset, genset only, grid only), side by side.
- **Optimal system type map:** over two sensitivity variables (for example fuel price and solar resource), which architecture wins in each cell.
- **Grid-extension break-even distance:** the line length at which extending the grid costs the same as the off-grid system.

GridSpine has the sensitivity sweep (`run_sensitivity_sweep`), pairwise compare and tornado, but not these three views.

## Likely shape

Built on the existing sweep and compare: one sweep per architecture (assets switched off), a results table, a 2-D map chart, and a break-even figure from a grid-extension cost per km. Check ui-modern (charts) and the business-case break-even code for reuse.

## Triage

Confirm on master, split frontend and backend slices if needed, name the files and reviewers, and set the model tier.
