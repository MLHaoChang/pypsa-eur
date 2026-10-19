<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-12 — representative-week templates (Energy Hub)

Status: ready-for-agent (RP-Q5 = A: calendar dates)
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-03
Requirements: R9 in [the spec](../spec.md)

Energy Hub templates solve a representative week, which apply refuses today. Phase 1's
refusal tells the user to upload a profile instead.

If RP-Q5 = A (calendar dates), RP-03's month-day-hour slice already covers dated weeks,
including several weeks with weightings. This ticket adds the template fixture tests and the
GUI copy. If RP-Q5 = B, it adds a week picker: per template week, the weather-year week of the
same season whose mean CF is closest to the season's mean, recorded in the meta as `sampled_weeks`.

Acceptance:
- `create_project_from_template` Energy Hub plus generate defaults: apply succeeds, 168 values,
  equal to the weather year's values for the template's dates (A) or for the recorded sampled
  week (B). `snapshot_weightings` are untouched.
- A solve of the template after apply stays optimal.
