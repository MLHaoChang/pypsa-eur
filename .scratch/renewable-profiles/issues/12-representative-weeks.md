<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-12 — representative-week templates (Energy Hub)

Status: done (RP-Q5 = A: calendar dates; backend G-a #694, window line G-b takeback, date fix claude/rp12-window-line)
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

## Evidence

- `pypsa-gui/backend/tests/test_profile_template_week.py` (G-a): `test_a_template_week_takes_the_weather_years_own_dates`
  builds `eh_h2_hub` and `eh_microgrid` from the template route, then generates and applies each wind and solar asset.
  It checks 168 values equal to the weather year's own dates and leaves `snapshot_weightings` unchanged.
  `test_the_template_still_solves_after_apply` (live_solve) keeps `eh_h2_hub` optimal.
- "Generate defaults" in the acceptance: the template series are `network_native`, so Generate defaults skips them by
  design (G-b "Decisions already made"); the test generates per asset.
- GUI copy: `WeatherBar.tsx` window line, "Your model covers 1–7 Jul (168 h): a generated profile takes these dates
  from 2025." `WeatherBar.test.tsx` pins `TZ=Europe/Berlin`. The earlier line parsed the route's dates in local time
  and showed the model year ("2030-06-30 to 2030-07-07").

## Comments
