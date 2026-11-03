<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-17 — Project PoC X/R input

Status: ready-for-agent
Type: task
Blocked by: AS-16

Spec: [spec.md](../spec.md). Follow-up (1) of AS-16 (#1025, owner decision AS11B-RVC-1, "A + GK A's condition"); merged follow-ups, owner decision FILL-10-2.

## Scope

Today the grid-readiness scorecard always uses `flicker_inputs.DEFAULT_XR` (4, assumed) for the point-of-connection X/R. The RVC row marks itself assumed and applies the X/R-free bound guard (`only_xr`), and the flicker row's GPU-envelope unit uses the same default.

- A project setting `poc_xr` (the PoC X/R the network operator gives), stored in `metadata.json` `settings` like `backup_autonomy_h` (#1024). It has one validator in `services/project_settings.py`: a finite number in (0, 100], a bool refused. It is written by `PATCH /api/projects/{name}/settings` (null clears it) and read by `ProjectInfo.settings`. Unset or invalid means none: the default is `None`, not 4.
- RVC row (`_rvc_row`) and its after-battery value (`_rvc_forms`): with `poc_xr` set, the voltage form uses it. X/R no longer marks the row assumed, and the bound guard is dropped, since that guard exists only while X/R is assumed (AS11B-RVC-1). The note says "X/R <v> (entered for the project)". The row still carries `voltage_form_pct` and `xr_free_bound_pct`.
- Flicker row: `fi.envelope_unit(..., xr)` takes the project X/R, and the note's X/R wording follows. The row stays assumed because tan φ_swing is assumed.
- With no `poc_xr`, every row is byte-identical to today.

Out of scope: a UI field (no settings UI exists for `backup_autonomy_h` either; the API carries it). The sizing loop's `SizingInputs.xr` default.

## Acceptance

- [ ] Tests first:
  - the settings validator accepts and refuses the right values;
  - PATCH round-trips the setting and null clears it;
  - an RVC case that warns at the assumed X/R passes with an entered X/R whose voltage form is under the limit, with `assumed` no longer driven by X/R;
  - the entered X/R changes the voltage-form value;
  - the flicker envelope unit's `xr` follows the input;
  - with no input, the scorecard output equals today's.
- [ ] GK A OK; CI green.

## PRs

## Comments
