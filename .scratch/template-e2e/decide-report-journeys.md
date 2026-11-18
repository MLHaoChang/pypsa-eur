<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Decide and report journeys: findings

Assurance walk of results → decide/compare → report (E2E-TEMPLATES-1, DECISION-GAPS-1, HOLISTIC-1, SCALE-1). Master `0ab07339`, API :8000, Vite :5173, Playwright, assistant off, smallest settings ("Simulation runs per check" = 1; the UI has no budget or quick-profile control). Per-template detail: [walk-dc.md](walk-dc.md), [walk-other-templates.md](walk-other-templates.md). Gap issues: [20–28](issues/). Overlap with other sessions: 11, 12, 13, 14, 16 (same folder), 08 (assumed values), 05 (raw names), PR #1629 journeys (hand-off smoke only).

## Coverage

| Template | Run (smallest) | Guided results | Expert results | Report | Compare / what-if | Investment case |
|---|---|---|---|---|---|---|
| eh_datacenter | 33 s | yes | yes | docx + PDF | BLOCKED (23) | not run (22) |
| eh_microgrid | 17 s | yes | yes | yes | Compare "Solved 0" (23) | not run |
| eh_h2_hub | 33 s | yes | yes | yes | Compare "Solved 0" | not run |
| eh_ammonia_staged | 230 s | yes | yes | yes | Compare "Solved 0" | not run |
| eh_site (hospital) | 77 s | yes | yes | yes | not walked | not run |
| eh_ipp_hybrid | Guided has no run (27); Expert LOPF 12 s | dead end | yes | Expert only | not walked | run: NPV 29.3 M, IRR 10.9% |
| eh_datacenter_staged / microgrid_staged / h2_hub_staged | 77 / 113 / 69 s | yes | yes | yes | not walked | not run |
| eh_concentrator_staged | **not finished after 75+ min; held the single backend at 100% CPU, no Abort (see 12)** | not reached | not reached | not reached | – | – |

Not walked: the concentrator results (blocked by the run), compare/what-if beyond DC, microgrid, H2 and ammonia, the investment case on all but IPP, sweeps beyond the Expert lever table. "Comments": there is no in-app comment thread; edits return only through the Word "Upload edited copy".

## What agrees

Cost, LOLE/verdict and the top risk agree across Guided, Expert and the report on every template that ran (DC 34.7 M EUR, microgrid 3.47 M, H2 7.61 M, ammonia 115.7–116 M, hospital 43.5 M, DC staged 383 M, microgrid staged 35.5 M, H2 staged 120 M). The Guided "Run study" → results hand-off works if the user stays on section 9. Report build, preview and .docx/.pdf export work on all but IPP in Guided.

## Ranked gaps (issue ids)

1. **20 Size of what is built is not shown** (blocks decision). Only the report shows capacities, and they include a placeholder grid and a counted shortfall.
2. **23 What-if/compare blocked** (blocks decision). Child case cannot run in Guided; Compare "Solved 0".
3. **22 Improve and investment case have no manual path** (blocks decision).
4. **27 IPP has no Guided run; results hand-off is a dead end** (blocks decision).
5. **21 Verdict "straddles the goal" at the resolution floor** (misleads). Runs = 1 is accepted.
6. **24 Guided risk table ≠ Expert FMEA** (misleads); hospital critical share not in results.
7. **26 Assumed inputs unlabelled in results** (misleads; depends on 08).
8. **25 Expert not read-only during a Guided run; no Abort** (misleads; 12 for the concentrator).
9. **28 Non-electric units and copied wording** (misleads).
10. Cosmetic: raw names (05), "not run yet" badges on populated views, "step 3 (Goal)" pointing at section 8 (13).

## Run-progress and "Open results" hand-offs (RUN-READONLY-1, UX-FLOW-NEXT-1)

Guided: PASS on read-only and on the move to Results with "Next: Improve" (stay on section 9). Expert: GAP (25). IPP Guided: dead end (27). Progress text carries no step name or ETA.
