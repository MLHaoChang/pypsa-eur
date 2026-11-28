<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 50 — "Run the solve" does not show the solve queue's location refusal

Status: in-review (PR open)
Owner: unassigned
Size: S
Follows: 48 (PR #2099; raised by GK B3 (7) as a non-blocking follow-up)
Review: UX (33), session_01G7qCukHzPumnu4BGwTyCrM, for the wording

## What

#2099 refuses an unlocated project at the solve queue (`enqueue_solve`, `requeue_job`, and the worker re-check at dequeue) with the typed `site_location_missing`. The "Run the solve" button in the investment case's needs list (Guided and Expert) enqueues through that queue. When the refusal comes back, the user sees nothing: the journey `smoke-template-journey-investment.mjs`, run without the confirmed site anchor, polled `POST /api/results/investment_case` until its 300 s deadline instead of failing on a visible refusal.

## Reproduce

1. Create the `eh_datacenter` template (`POST /api/projects/from_template/eh_datacenter`); do not set the site coordinates.
2. Open it, go to Results & reports, click the investment case Run, then "Run the solve" in the needs list.
3. Expected: the location message ("set the site location"), pointing at the Latitude/Longitude drawer, in the same words the flow's `site_location_missing` item uses. Actual: no message; the case stays "needs a solved project".

## Acceptance

- Find where the queue's 422 (and a dequeue-time job failure with `condition == "site_location_missing"`) is dropped on the "Run the solve" path, and show the typed message there, in both Guided and Expert.
- Wording shared with the flow item (UX 33 reviews it); no new copy if the flow's is reusable.
- A browser journey step: the same journey without the anchor fails fast on the location message (the fixture keeps confirming the anchor for the others).
