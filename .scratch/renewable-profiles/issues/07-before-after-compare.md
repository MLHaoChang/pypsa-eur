<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-07 — before/after comparison

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-06 (works without it for current vs pending)
Requirements: R6 in [the spec](../spec.md)

`POST /api/projects/{name}/profiles/compare {a, b}`, where each side is `current:<generator>`, `pending:<job>[:<point>]` or `shaped:<job>`. It returns the stats for both sides and their deltas: FLH, CF, peak, mean-peak hour-of-day, P10/P50/P90, monthly means, a 100-point duration curve and the peak week. Every number is computed server-side.
UI: a compare drawer in the Renewables tab, with an overlay line and a duration-curve chart (follow the dataviz skill), opened before Apply and after Shape.

Acceptance:
- Comparing a profile with itself → all deltas are 0.
- A known synthetic pair → the FLH delta equals a hand calculation in the test.
- Playwright: Generate → Compare shows two curves and the FLH numbers from the route; screenshot saved.
- The chat tool `compare_profiles` returns the same payload.
