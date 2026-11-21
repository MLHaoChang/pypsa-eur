<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A component edit through the network route reads as "Not solved yet"

Status: needs-triage
Model tier: sonnet.
Size: S.
Blocked by: none.
Gated: GK B.
Likely files:
- pypsa-gui/backend/routers/network.py (`_update_component`, the `PUT /api/network/<table>/{name}` routes)
- pypsa-gui/backend/services/site_scene/results.py (`_label`, which reads the solver state)

Found by SC-07 S6b (#1701), `tests/test_sc07_integrated_journey.py`.

## What happens

1. Solve the D07 oracle project through `POST /api/simulation/run`. The solver
   state is `completed` / `optimal`.
2. Edit an input through the ordinary route:
   `PUT /api/network/loads/office` with `{"name": "office", "bus": "elec", "p_set": 9.0}`.
3. The solver state is now `idle` with no condition, while the network still
   holds the solved series.
4. `results.gate` labels the closed gate `run_not_solved`. The scene results
   route and the chat read `site_scene_results` then say "Not solved yet, so
   there are no results."

The same edit through the chat (`update_component`, approved on its card)
keeps the solve: the gate reads `results_not_current` and the answer says
"Out of date: the inputs changed after the solve."

## Expected

After a solve, an input edit through any write path reads as out of date
(`results_not_current`, V69): the user did solve, and the words should tell
them to solve again, not that nothing was ever solved. Availability is already
right (every number unavailable); only the reason and its words are wrong.

## Acceptance

- After a completed solve, a `PUT /api/network/...` edit makes `results.gate`
  report `results_not_current`, as the chat edit does.
- The `_pending` manifest row `LC-D01.site_scene_results_after_route_edit`
  (fragment `pypsa-gui/backend/smoke/family_manifest.d/claude-sc07-s6b-journey.json`)
  is settled to covered by a test asserting that reason after the route edit.
