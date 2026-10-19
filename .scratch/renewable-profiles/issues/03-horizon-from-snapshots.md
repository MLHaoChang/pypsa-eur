<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-03 — horizon and weather year from the snapshots

Status: needs-triage
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-Q3, gatekeeper review of the rule change
Requirements: R2 in [the spec](../spec.md)

- Default weather year: the snapshot year if it is a completed past year, else the last completed year. Used by the routes, the chat tools and the GUI when no `year` is given, and reported as `year_source: snapshots|default`.
- Default resolution: derived from the snapshot step. For regular multi-hour steps, `assign_profile` takes the mean of the hourly series over each step.
- Partial-year horizons, if RP-Q3 = slice: align by month-day-hour onto the window, with the leap rules unchanged.
- Update the rule table in `apply.py` and spec §4. This is a data-write change, so send the gatekeeper a plan first.

Acceptance:
- 2023 hourly snapshots, no year given → `weather_year == 2023`, `year_source == "snapshots"`.
- Snapshots in a future year → last completed year, `year_source == "default"`.
- 3-hourly snapshots over 2023 → 2920 values, each the mean of its 3 hours; today this is refused (RED).
- Snapshots for Jun–Aug 2023 → 2208 values equal to the weather year's Jun–Aug hours (RP-Q3 = slice); today this is refused (RED).
- The non-leap→leap refusal is unchanged.
