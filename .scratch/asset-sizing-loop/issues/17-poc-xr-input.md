<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-17 — Project PoC X/R input

Status: done
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

- [x] Tests first:
  - the settings validator accepts and refuses the right values;
  - PATCH round-trips the setting and null clears it;
  - an RVC case that warns at the assumed X/R passes with an entered X/R whose voltage form is under the limit, with `assumed` no longer driven by X/R;
  - the entered X/R changes the voltage-form value;
  - the flicker envelope unit's `xr` follows the input;
  - with no input, the scorecard output equals today's.
- [x] GK A OK; CI green.

## PRs

- #1035 (claude/as17-poc-xr-input), merged
- AS-17b (claude/as17b-sizing-poc-xr): the sizing loop's `SizingInputs.xr` reads `poc_xr` (FILL-10-2 merged follow-up)

## Comments
- 2026-11-03, GK A plan verdict: GO with conditions.
  1. Which PoC: the note names the PoC the X/R is applied to ("X/R <v> (entered for the project), at PoC <bus>"). With more than one `eh_poc` bus, the note says the entered X/R is applied to the governing (lowest S_k'') PoC. Tested with two PoCs.
  2. Guard semantics: the guard drops only when X/R is entered. `assumed` still follows tan φ, the limit, the fault level and the envelope. Test: entered X/R with tan φ assumed → no guard, row still assumed. The existing X/R-unset guard test stays; the entered case is a new test.
  3. Validation: a finite float in (0, 100]. Refuse 0, negative, NaN, inf, a bool and a string at the request model (422), and read a stored invalid value as None. Each case is tested, and so are the PATCH round-trip and null-clears. If a chat tool reaches the write, the same validation runs there.
  4. Staleness: anything that caches or fingerprints grid-readiness or flicker outputs keys on `poc_xr`, or the PR body says nothing caches.
  5. Byte-identity: with no input, the scorecard JSON (every row, including flicker and after-battery) equals master's on a fixture project.
  6. Hand calc: X/R 1.5 (cos ψ 0.5547, sin ψ 0.8321) at 250 MVA, tan φ 0.1. `xr_free_bound_pct` is unchanged by X/R.
  7. Flicker: `envelope_unit` uses the entered X/R, and the note's provenance follows it. Pst moves in the hand-checked direction. The infrequent RVC tier is unchanged.
  8. API surface: run the assistant inventory, route-parity and control-matrix `--check`s, `test_route_chat_disposition`, and the project-settings route tests.
  9. Release note: an entered X/R replaces the assumed 4 and lifts the X/R guard. No UI field yet.
- 2026-11-03, GK A (10) AS-17b plan verdict (the sizing loop reads `poc_xr`): GO with conditions.
  1. Provenance: the loop result carries `xr` and `xr_source` ∈ {request, project, assumed}; the Pst check's note names it ("X/R 2.5 (entered for the project)" / "X/R 4 (assumed; …)", reusing `fi.XR_ASSUMED`). Check any frontend type or golden test that pins the result shape.
  2. Precedence: request > project > assumed. The request X/R and the project X/R both go through `_check_inputs`; a stored invalid value → 4.0.
  3. Callers: both `SizingInputs` sites in `routers/compare.py` (the 422 pre-check and `_intent_scenario`) take the resolved dict; a test fails a mutant that resolves only one.
  4. Consistency: with `poc_xr` 2.5, the scorecard's flicker envelope X/R equals the loop's Pst X/R.
  5. Hand check: `pst_step_mva` at X/R 1.5 vs 4 with both P_res and Q_res > 0, values stated.
  6. Tie-break (d): index order made explicit, no behaviour change; docstring plus a non-alphabetical two-PoC test. Name sort declined.
  7. Caching: `intent_jobs` keeps the last job only and nothing fingerprints inputs; stored inputs, if any, show the resolved X/R.
  8. Trial merge: test_intent_comparison_endpoint, the sizing-loop suites, test_grid_readiness_poc_xr, test_route_chat_disposition, and the parity / control-matrix checks if the route's response model changes. Release note and housekeeping.
