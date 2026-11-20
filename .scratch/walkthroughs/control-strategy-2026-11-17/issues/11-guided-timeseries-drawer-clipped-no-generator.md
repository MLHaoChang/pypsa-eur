<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11: Guided section 6 "Edit ›" opens the whole series library, clipped, with no generator

Status: ready-for-agent
Impact: low
Templates: Data Center Energy Hub (all templates)
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Data Center Energy Hub, Guided › 6 Time series & profiles. The card lists 3 loads and 2 generators, each with "Edit ›".
2. Press "Edit ›" on it_load.

## Actual

- The right drawer opens the full "Time series library": category tabs, the load list, the "Drop a joint electricity profile file" box, the expected-format help and the "Uploaded profiles" table.
- At drawer width the names clip ("it_l…", "co…") and the table loses its right columns.
- The load generator (archetype, peak or annual, preview, Apply) is not offered. It is only reachable from section 4 › it_load › "Edit ›".

## Expected

"Edit ›" on a load in section 6 opens that load's series editor (`LoadSeriesEditor`: archetype, upload, preview, Apply), the same editor section 4 opens. "Open time series & profiles in Expert ›" stays the way to the library.

## Breaks

- `.scratch/timeseries-generators/spec.md:19-29`: generators in Guided and Expert.
- `review-ux.md`: "One entry point per feature".
- DENSITY-1 does not cover clipped names.
