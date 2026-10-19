<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-04 — generate inside the Time Series Manager's Renewables tab

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-01, RP-02 (RP-Q2 = A: one click; RP-Q4 = A: Profile Studio becomes an Expert link from this tab, removed from the sidebar)
Requirements: R3 in [the spec](../spec.md)

Hand-off items E1b and E2, re-homed. In `TimeSeriesManager.tsx`, Renewables tab:
- per-row badges from the readiness feed (coordinates, profile source · weather year);
- a weather bar: source Seg (Synthetic / ERA5) and year, prefilled from RP-03, saved in project meta and asked once;
- "Generate defaults for all": one job with every ready generator, auto-applied, one undo step (RP-09), then the `generator_profiles` query refreshes;
- a missing-coordinates banner that lists the generators, with "Set coordinates" (inline bus x/y edit through the normal edit path) and "Upload instead" (focuses the existing upload zone);
- per-row "Generate" and "Tune…" (RP-05);
- a "Snapshots are local time in …" choice in the weather bar, defaulting to UTC, passed as `time_basis` on apply (R8);
- in Expert, an "Advanced: Profile Studio" link; the sidebar entry is removed (RP-Q4).

Acceptance:
- Vitest: banner text names `g_nocoord`; "Generate defaults for all" posts one generate with `from_generators` = the ready set only.
- Playwright (Chromium at /opt/pw-browsers, synthetic source): fixture `E2E Prof` plus one 0/0 generator. One click → the chart shows a profile for `g_wind`, the banner lists the 0/0 generator, Set coordinates → its row turns ready → Generate → profile shown. Screenshots saved under `.scratch/renewable-profiles/evidence/`.
- Undo after "all" restores every prior `p_max_pu` in one step.
