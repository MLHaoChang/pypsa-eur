<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-05 — schema-driven tuning panel

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-04
Requirements: R4 in [the spec](../spec.md)

- Backend: `GET /api/profiles/schema?technology=wind|solar` lists fields with type, unit, bounds, preset value and provenance. It is derived from `presets.py` plus a per-technology field list, so it is not hand-copied into the frontend.
- Frontend: a collapsible "Tune parameters" panel in the Renewables tab. Wind fields: turbine preset, hub height, power-curve derate, cut-out, losses. Solar fields: panel preset, tilt, azimuth, tracking, inverter efficiency, losses. Changed fields are marked, with "Reset to preset". Expert JSON sits behind "Advanced".
- The synthetic source honours the losses and derate fields, so tuning is visible offline, in CI.

Acceptance:
- Schema route: every preset field appears with a unit, and bounds reject out-of-range values with 422 `invalid_params`.
- Vitest: changing tilt marks the field as changed; Reset clears it; Generate sends `params` with only the changed fields.
- Engine: synthetic wind with `losses: 0.1` → mean CF is 0.9 × the baseline, exactly.
