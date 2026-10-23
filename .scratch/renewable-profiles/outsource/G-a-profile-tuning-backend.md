<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent (split from brief G on 2026-10-23 by owner decision; written against `origin/master` `bcd81b268`, plan-reviewed by the gatekeeper)

# Outsourced task G-a: profile tuning and compare, the backend

Branch: `outsource/profile-tuning-backend` · one PR against `master` · PR body starts with `Outsourced task G-a`

This is the backend half of a feature whose GUI (a Tune dialog and a compare view) is a separate task, G-b, done after this one merges. **Build no frontend here.** G-b builds on the routes, payloads and tool you add, so keep their shapes exactly as specified. The task has a **core** you must finish and one **stretch** item (S1) you do only if time is left. If you run short, open the PR with the core done and say S1 is not done.

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. The tickets: `.scratch/renewable-profiles/issues/05-tuning-panel.md` (RP-05), `07-before-after-compare.md` (RP-07), `12-representative-weeks.md` (RP-12). Background: `04-timeseries-manager-integration.md`, `06-calibration-engine.md`, and the spec `.scratch/renewable-profiles/spec.md`, requirements R4, R6, R8, R9 and decisions RP-Q3 = A, RP-Q4 = A, RP-Q5 = A.
3. Domain words: `pypsa-gui/CONTEXT.md` l.155-198 (**Coordinate profile**, **Profile source**, **Profile preset**).
4. The chat harness contract, because you add one tool: `pypsa-gui/backend/harness/README.md`, and its inventory note at l.654-660.
5. Backend code you build on:
   - `pypsa-gui/backend/services/profile_gen/presets.py` l.194-203 (`resolve_params`, the one funnel for preset + overrides; the router's request check and the job runner `job.py::_resolve_params` l.162-168 both call it);
   - `services/profile_gen/data/profile_presets.yaml` (the presets and their per-field provenance);
   - `services/profile_gen/engine.py` l.215-283 (`generate_profile`; the source call is l.259, aggregation l.260-261);
   - `services/profile_gen/synthetic.py` (the offline source; it ignores every param except `seed` today);
   - `services/profile_gen/atlite_source.py` l.84-97 (where the turbine, panel, orientation and tracking go to atlite);
   - `services/profile_gen/shape.py` l.73-91 (`profile_stats`: FLH, CF, P10/P50/P90, peak, peak hour);
   - `services/profile_gen/apply.py` l.1271 (`prepare_profile`, apply's pure half: it aligns a series onto the snapshots and writes nothing; returns `.aligned`, `.warnings`, l.1464-1469) and l.303-337 (`_hourly_window`);
   - `services/profile_gen/current.py` l.159 (`series_from_current`) and `services/profile_gen/readiness.py` l.42-52 (`profile_kind`);
   - `pypsa-gui/backend/routers/profiles.py`: routers l.112-113, request models l.127-160, `_preset_params` l.383, `_error_kind` l.431, the refusal tables l.441-487, `/presets` l.501, `/estimate` l.532, `/generate` l.598, `_foreground_ctx` l.715, `_dry_run`/`_preview_week` l.754-806, `_pending_artifact` l.410, `_weather_answer` l.1444, `/readiness` l.1490, `/apply` l.1499, `/apply-batch` l.1568, `/shape` l.1673.
6. Test style models: `tests/test_profile_readiness.py` (route + chat tool, l.117-127), `tests/test_profile_batch_apply.py` l.20-62 (a project fixture and `_job`, which runs a real `profile_gen` job), `tests/test_profile_weather_bar.py`, `tests/test_profile_gen_snapshot_window.py`, `tests/test_project_flow.py::_create` l.100 (a project from a template), `tests/test_eh_staged_hubs.py::_solve` l.166-188 (a template solve).

## What is already done (do not rebuild)

Checked against `origin/master` `bcd81b268`:

- **RP-04 is done apart from "Upload instead".** #616 (readiness badges, missing-location banner), #623 (weather bar, per-row Generate, wind/solar in "Generate defaults for all"), #624 (Profile Studio Expert-only).
  - "Set coordinates" exists as **"Set the bus coordinates ›"** (`RenewableReadiness.tsx` l.64-68). It opens the bus in Properties, in place in Guided (`openBusCoordinates` l.44-48). This replaced the ticket's inline x/y edit on purpose: the file header says fixing goes where the value lives. Do not add an inline editor.
  - `time_basis` is settled by #623: the weather bar shows the basis read-only with "Edit in Connection & grid ›" (`WeatherBar.tsx` l.54-60). Apply resolves it from the project's commercial timezone (`apply.py` l.222-252, the gatekeeper's option C). No "snapshots are local time in …" picker is to be built.
  - Still missing: the banner's **"Upload instead"** action. It is stretch item S3.
- **RP-03** (#606) and **RP-09** (#605) are done. The month-day-hour slice handles any hourly window: `apply.py::_hourly_window` l.303-337, tested by `tests/test_profile_gen_snapshot_window.py` (a study week l.103, two weighted weeks l.128, per-period windows l.217) and `test_profile_gen_apply.py::test_a_template_week_takes_an_hourly_profile_and_refuses_a_daily_one` l.572. The daily/weekly refusal already names the fix (`apply.py` l.293-299).
- **RP-06** (#432) is done: `shape.py` and `POST /profiles/shape`. There is no GUI for shaping and no `shape_generated_profile` chat tool; both are RP-08, not this task.
- **What RP-12 still lacks:**
  - no test builds a real Energy Hub template and applies a generated profile to it. The tests above hand-build a week;
  - no GUI words tell the user that a sampled model takes those dates from the weather year.

  Two facts make the template test harder than the ticket says:
  - every Energy Hub template puts its buses at 0/0, which reads as "no location": `eh_templates.py` l.132-134, 662-664, 776-778;
  - their wind/solar generators carry the network's own `p_max_pu`, so readiness says `network_native`, and "Generate defaults for all" and per-row Generate leave them alone (`renewableGeneration.ts::readyRows` l.20-23).

  So the test must place the bus and apply explicitly; see T-R1 and "Decisions already made".
- **Not done at all:** RP-05 (no schema route; the synthetic source ignores losses and derate; tuning is a raw JSON textarea in Profile Studio only) and RP-07 (no compare route, no `compare_profiles` tool, no compare view). The apply dry run already returns a one-week old → new preview (`_preview_week`), which you will not change.

## Goal in plain words

The backend for tuning a wind or solar profile and comparing it with what the generator carries now:

- a schema route lists each technology's tuning settings with their preset values, units, bounds and sources;
- tuning keys travel in the existing `params` and are validated and mapped in one place, `resolve_params`; losses and derate now apply to every source, the synthetic one included;
- a read-only compare route and chat tool, `compare_profiles`, put two profiles on the model's snapshot clock and return full-load hours, capacity factor, quantiles, monthly means, duration curves and a peak week, with their differences, all computed by the server;
- the weather answer names the window a sampled model (an Energy Hub week) takes from the weather year.

Separately, the RP-12 remainder: a test proves that an Energy Hub template's representative week takes the weather year's own dates.

## User-facing outcome

- **The assistant** can read a before/after comparison with `compare_profiles`, and `generate_renewable_profile` / `preview_renewable_profile` accept the tuning keys in their existing `params`; out-of-range values come back as `invalid_params`.
- **The GUI does not change in this PR.** G-b adds the Tune dialog, the compare view and the weather bar's window line on top of these routes.
- Untuned generation, per-row Generate, "Generate defaults for all" and Profile Studio behave exactly as today.

## Files to touch

### Core

**Backend**

- `pypsa-gui/backend/services/profile_gen/tuning.py` (new). One table per technology:
  - **Fields** (key, label, unit, type, min, max, options, `sources`, and the path into preset params; ranges are under "Decisions already made"):
    - wind: `hub_height` (m, 20–200, atlite), `cut_out` (m/s, 15–35, atlite), `derate` (fraction 0–0.5, both sources), `losses` (fraction 0–0.5, both sources);
    - solar: `tilt` (°, 0–90, atlite → `orientation.slope`), `azimuth` (°, 0–360, atlite → `orientation.azimuth`), `tracking` (`none` | `horizontal`, atlite → `tracking`), `inverter_efficiency` (0.8–1.0, atlite → `panel.inverter_efficiency`), `losses` (0–0.5, both sources).
  - **Functions:**
    - `schema(technology, preset)`: the field list with each field's `preset_value` and `provenance`. These are read from the preset's params and provenance; a missing value is `null` with the provenance "not used by the synthetic source", and `losses`/`derate` default to 0 with "tool default: none applied". The YAML has no `cut_out` key: its `preset_value` is the largest `V` in the preset's power curve with `POW > 0` (25.0 for `vestas-v90-3mw`, whose curve ends `(25.0, 3.0), (25.01, 0)`). Never copy values by hand;
    - `validate(technology, params)`: raises `InvalidTuningError(ProfileGenError)` for an out-of-range or wrong-type tuning key. It names the key, the value and the bounds;
    - `apply_to_params(technology, params)`: moves `hub_height` into a copy of `turbine`. For `cut_out`, it keeps the curve points with `V < cut_out` **and** `V ≤` the preset's cut-out (the largest `V` with `POW > 0`), then appends `(cut_out, POW of the last kept point)` and `(cut_out + 0.01, 0)`. The second condition matters when `cut_out` is above the preset's: without it the preset's own `(25.01, 0)` would be the last kept point, and `cut_out: 30` would append POW 0. It sets `orientation` and `panel` the same way. It leaves `losses`/`derate` top-level for the engine, and records the flat keys as `tuning` and the atlite-only keys a synthetic preset cannot take as `tuning_not_applied`;
    - `post_source(frame, technology, params)`: `cf = min(cf, 1 − derate) × (1 − losses)`, column-wise, for every source. It is not called at all when neither `losses` nor `derate` is set (not even with 0), so untuned output is byte-identical (B-T8).
- `services/profile_gen/presets.py`: in `resolve_params`, after the merge, call `tuning.validate` and `tuning.apply_to_params`. Keys that are not tuning keys pass through unchanged, as today: the chat's `params` overrides keep working. Never mutate the shipped preset (`test_resolve_params_applies_overrides_without_mutating_preset` must still pass).
- `services/profile_gen/engine.py`: call `tuning.post_source` on `frame` directly after `source_fn(...)` (l.259), before aggregation, for the renewable family only. Skip the call entirely when neither `losses` nor `derate` is in the params. Record `tuning` and `tuning_not_applied` in `meta` only when present; untuned meta keeps exactly today's keys.
- `services/profile_gen/synthetic.py`: no change. The engine applies losses and derate after any source, so the synthetic source honours them (RP-05's "visible offline").
- `services/profile_gen/compare.py` (new):
  - `side_series(ctx, proj_dir, side, component)` resolves each side onto the model's snapshot clock:
    - `current:<generator>`: `ctx.user_ts[("generators","p_max_pu",g)]`, else the network's `generators_t.p_max_pu[g]`, else `CurrentSeriesError` (existing kind `no_current_series`). Read these directly; do **not** reuse `current.series_from_current`, which reads `user_ts` only and refuses a MultiIndex series;
    - **multi-period models:** both sides stay on the network's `(period, timestep)` snapshots. Monthly means group by the timestep level's month across periods; `flh` is the weighted mean over all snapshots × the hours of the first period's timestep year; the `week` block stays inside one period and carries its `period`. Test B-C9 pins this;
    - `pending:<job>[:<point>]`: the artifact via the router's `_pending_artifact`/`_read_point_series`, aligned by `apply.prepare_profile(ctx, component, series, meta)`. The point defaults to the component; the component defaults to the current side's generator, else the point id;
    - `shaped:<job>[:<point>]`: the same, but the record must carry `calibrated: true`.
  - `compare(a, b, weights)`:
    - for each side: `cf` and `flh` from the snapshot-weighted mean (`n.snapshot_weightings.generators`) × the hours of the timestep year (8760/8784), plus `p10/p50/p90/peak/peak_hour` from `shape.profile_stats` on a copy indexed by the timestep stamps;
    - `monthly` means by calendar month, and `duration`: 100 points of the descending sorted series;
    - `delta` = b − a for every scalar and month;
    - `week`: the calendar week (Monday 00:00 on, within one period on a multi-period model) holding b's highest value, with both sides' values over its snapshots.

    Round to 4 decimals. A self-compare must give exact zero deltas.
- `routers/profiles.py`:
  - `GET /api/profiles/schema?technology=wind|solar&preset=<name>` on `router` (global, next to `/presets` l.501). Unknown technology or preset: 422 with the existing `_error_kind`/`public_message` shape;
  - `GET /api/projects/{name}/profiles/compare?a=…&b=…[&component=…]` on `project_router`, with `proj: AuthorizedProject = ProjectAccessDep` and `_foreground_ctx(proj, "compare profiles")`. It writes nothing: no `_check_lock`, no undo push. A malformed side is 422 `invalid_compare_side`; an unknown job is the existing 404; a `shaped:` side that is not calibrated is 422 `not_a_shaped_profile`; apply refusals map through `_apply_refusal_kind`;
  - in `/estimate` and `/generate`, catch `InvalidTuningError` **before** the generic `ProfileGenError` and raise 422 `{"error_kind": "invalid_params", "message": public_message(...)}` with the literal kind. The manifest test scans for literals;
  - add `InvalidTuningError` to the import list. Leave every other route alone.
  - `_weather_answer` (l.1444): add `window`. It is `None` for one full calendar year, else `apply._hourly_window(stamps, what="the model's snapshots")`, using the timestep level and the first period on a multi-period model. When `_hourly_window` refuses, `window` is `{"unsupported": public_message(...)}`; the bar then shows nothing new, and the apply refusal stays the place that explains it.
- `services/chat_tools.py`: `compare_profiles(project_id, a, b, component=None)` next to `get_profile_readiness` (l.3652), calling the route handler through `_profile_route` with `proj=_authorized_project(project_id)`. Register it in `DISPATCHERS` next to l.7857.
- `harness/catalogue.py`, **append-only**:
  - a `_t("compare_profiles", …)` entry directly after `get_profile_readiness` (l.1484-1494). Its description names the side forms, says every number is computed server-side, says to cite only the returned numbers, and ends with `Safety: read.`. `project_id`, `a` and `b` are required;
  - `"compare_profiles": [("GET", "/api/projects/{name}/profiles/compare")]` in `TOOL_ROUTES` after l.4472.

  Do not edit `harness/workflows/renewable-profile.md` (RP-08 owns it).
- `pypsa-gui/tool-error-kinds.json`: add `invalid_params`, `invalid_compare_side` and `not_a_shaped_profile`, each with `surface: "inline"` and a `why` in the existing entries' style. `tests/test_tool_error_kind_manifest.py::test_the_manifest_still_describes_the_backend` (l.147) tells you if one is missing or extra.
- `.scratch/live-voice/capability-inventory.json`: **regenerate, never hand-edit**: `python pypsa-gui/backend/smoke/assistant_inventory.py --write`, then `--check`. `tests/test_assistant_inventory.py` and `.github/workflows/assistant-inventory.yaml` run the check.
- `pypsa-gui/backend/tests/fixtures/route_inventory_phase0.txt`: add only the line the new tool needs, `GET     /api/projects/{name}/profiles/compare     -> compare_profiles_api_projects__name__profiles_compare_get` (match the exact operation id FastAPI prints), in sorted position before `/profiles/current` (l.280), with the file's column padding. `test_chat_tools_endpoint_map.py` needs it. Add no other line, and do not add the schema route (no tool maps to it).
- `pypsa-gui/CONTEXT.md`: two glossary entries in the profile block (after **Profile preset**, l.198), in the same form:
  - **Profile tuning**: the per-technology settings over a preset, the schema route, losses/derate on every source, the rest ERA5 only;
  - **Profile comparison**: two sides on the snapshot clock, the stats, server-computed.

**Docs**

- `doc/release-notes.d/outsource-profile-tuning-backend.md`: the fragment below.

### Stretch (only after the core is green)

- **S1. A staged template case for T-R1.** Add `eh_h2_hub_staged` as a third case: per-period windows, each period's 168 values equal to the same weather-year hours.

## Access and lock checks that must hold

- `GET /api/profiles/schema` is global and reads the shipped presets only, like `GET /api/profiles/presets`: no project, no lock. The auth middleware still requires a user (`main.py`, as for every non-public `/api` path).
- `GET /profiles/compare`:
  - authorises through `ProjectAccessDep`, which runs the per-project ACL. `tests/test_project_route_acl_policy.py::test_every_project_naming_route_runs_the_project_acl` must pass without an `ACL_POLICY` entry;
  - reads only the foreground context: `_foreground_ctx` gives 409 `project_not_open` for a background project;
  - reads only this project's pending folder (`_pending_artifact`: 422 for an id escaping the folder, 404 for another project's job).

  It is a GET, so `test_write_surface_lock_policy.py::test_every_write_route_has_a_lock_decision` (l.461) is unaffected.
- **Generate and apply keep their checks unchanged:** `_check_lock` on `/generate`, and on `/apply` the lock, undo snapshot first, foreground check and moved-bus warning. The Tune dialog uses these routes as they are.
- **Compare writes nothing.** A test pins it: undo depth, `network_revision`, `user_ts` and the pending folder's file list are all unchanged after a compare.

## The assistant's path

- `compare_profiles` is a read: `safety_tier_for("compare_profiles") == "read"`, so it needs no confirmation card.
- It calls the same handler as the route, so the payloads are equal (test B-C6). The name contains "profile", so `harness/toolsets.py` l.44 files it with the other profile tools in "network". It contains "compare" too, so l.47 also files it in "results". Both are intended; no toolset edit.
- `generate_renewable_profile` and `preview_renewable_profile` accept tuning keys through their existing `params` argument, because the tuning lives in `resolve_params`. Out-of-range keys now come back as `invalid_params`. Do not change their schemas or descriptions. RP-08 rewrites the workflow and adds `shape_generated_profile`; that is not this task.

## Tests

Every new test must fail on `master`; say in the PR body which line fails there.

### Backend: `pypsa-gui/backend/tests/test_profile_tuning.py` (new)

1. **B-T1** `test_the_schema_lists_every_field_with_unit_bounds_and_preset_value`, parametrised wind/solar:
   - `GET /api/profiles/schema?technology=wind&preset=vestas-v90-3mw` lists exactly the wind keys, each with `unit`, `min`/`max` (or `options`), `sources`, `preset_value` and `provenance`;
   - `hub_height == 105` and `cut_out == 25.0`, read from `profile_presets.yaml`;
   - the solar `csi-fixed-35deg-south` preset gives `tilt == 35.0`, `azimuth == 180.0`, `inverter_efficiency == 0.9`;
   - `preset=default` gives `null` for the atlite-only fields.
2. **B-T2** `test_unknown_technology_or_preset_is_a_422`.
3. **B-T3** `test_out_of_range_tuning_is_refused_as_invalid_params`: `losses: 0.9`, `tilt: 120` and `tracking: "dual"` each give 422 `invalid_params` on `/api/profiles/estimate` and on `/projects/{name}/profiles/generate`, and the message names the key and the bounds. Nothing is enqueued (`sq.solve_queue` holds no new job).
4. **B-T4** `test_synthetic_losses_scale_the_mean_exactly`: `generate_profile([("g", 8.0, 50.0)], "wind", params=resolve_params("default", "wind", {"losses": 0.1}), year=2023)` gives a mean of 0.9 × the baseline mean (`pytest.approx(rel=1e-12)`), and every value is 0.9 × the baseline value.
5. **B-T5** `test_derate_caps_the_series`: with `derate: 0.2`, the max is ≤ 0.8 and equals `min(baseline, 0.8)` hour by hour.
6. **B-T6** `test_atlite_fields_map_into_the_preset_params` (pure, no atlite):
   - `resolve_params("vestas-v90-3mw", "wind", {"hub_height": 120, "cut_out": 22})` gives `turbine.hub_height == 120`; every `V >= 22` point is replaced; the curve ends `(22, 3.0), (22.01, 0.0)`;
   - `cut_out: 30` (above the preset's 25.0) keeps the preset's points up to `(25.0, 3.0)`, drops `(25.01, 0)`, and the curve ends `(30, 3.0), (30.01, 0.0)`;
   - the solar preset with `tilt: 20, tracking: "horizontal", inverter_efficiency: 0.95` sets `orientation.slope`, `tracking` and `panel.inverter_efficiency`;
   - the shipped preset is unchanged afterwards.
7. **B-T7** `test_meta_records_tuning_and_what_the_source_ignored`: synthetic with `hub_height` and `losses` gives `meta["tuning"] == {...both}` and `meta["tuning_not_applied"] == ["hub_height"]`.
8. **B-T8** `test_untuned_generation_is_byte_identical`: `generate_profile` with no tuning keys returns the same series **and the same meta keys** as before this change. Compare against a frozen digest you record on `master` first.

### Backend: `pypsa-gui/backend/tests/test_profile_compare.py` (new)

Use a project fixture like `test_profile_batch_apply.py` l.27-46 (hourly 2025 year, bus at 8/50, wind `g_a` with a network-native `p_max_pu`, wind `g_c` with none) and its `_job` helper.

1. **B-C1** `test_a_profile_compared_with_itself_has_zero_deltas`: `a = b = pending:<job>:g_c` gives every `delta` scalar and month equal to 0.0, and the two `duration` lists equal.
2. **B-C2** `test_the_flh_delta_matches_a_hand_calculation`:
   - set `g_a`'s current `p_max_pu` to a constant 0.30;
   - write a pending artifact by hand with `job.write_pending` holding a constant 0.40 for `g_a` on 2025;
   - compare gives `a.flh == 0.30 × 8760`, `b.flh == 0.40 × 8760`, and `delta.flh == 876.0`.
3. **B-C3** `test_the_current_side_reads_what_the_generator_carries`: `current:g_a` reads the network-native series; after an apply it reads the applied series; `current:g_c` before any apply is 422 `no_current_series`.
4. **B-C4** `test_a_shaped_side_must_be_calibrated`: a `/profiles/shape` result is accepted as `shaped:<id>`, and a plain generated job as `shaped:` is 422 `not_a_shaped_profile`.
5. **B-C5** `test_malformed_and_foreign_sides_are_refused`: `nonsense`, `pending:` and `current:` give 422 `invalid_compare_side`; an unknown job id is 404; `pending:../x` is 422 `malformed_job_id`.
6. **B-C6** `test_the_chat_tool_returns_the_route_payload`, modelled on `test_profile_readiness.py` l.117-127:
   - `compare_profiles` is in `TOOLS`;
   - `safety_tier_for(...) == "read"`;
   - `TOOL_ROUTES` holds the GET route;
   - the dispatcher's output under `bound_ctx` equals the route's JSON.
7. **B-C7** `test_compare_writes_nothing`: the undo depth (`/api/network/undo/info`), `session_ctx(client).network_revision`, `user_ts` keys and the sorted pending folder listing are unchanged after two compares.
8. **B-C8** `test_a_sampled_week_compares_on_its_own_snapshots`: on a 168 h week network with weightings 8760/168, the `week` block has 168 snapshots, `duration` has 100 points, and `flh` uses the weighted mean.
9. **B-C9** `test_a_multi_period_model_compares_on_its_own_snapshots`: on a two-period model (2030, 2040; one 168 h week each, weightings 8760/168), `g_a` carrying a MultiIndex `p_max_pu` of constant 0.30 in 2030 and 0.50 in 2040. `current:g_a` against itself gives zero deltas; `flh == 0.40 × 8760`; the `week` block's snapshots all lie in one period, and the block names it.

### Backend: `pypsa-gui/backend/tests/test_profile_template_week.py` (new, RP-12)

1. **T-R1** `test_a_template_week_takes_the_weather_years_own_dates`, parametrised over `eh_h2_hub` (`wind_farm`, `solar_park` on bus `hub`) and `eh_microgrid` (`pv_plant`, `wind_turbines` on bus `island`):
   - create the project with `POST /api/projects/from_template/<tid>` as `test_project_flow.py::_create` l.100 does;
   - keep a copy of `n.snapshot_weightings`;
   - give the bus coordinates (`n.buses.loc[bus, ["x", "y"]] = [8.0, 54.0]` on the session context; the 0/0 default reads as no location);
   - generate with `from_generators` for one generator per technology (synthetic, no year: the snapshots are 2030, so the year is the last completed one, `year_source: "default"`);
   - apply each with `POST /profiles/apply` (explicit apply replaces the network-native series; the receipt has `before`). Each answer has `window.hours == 168`;
   - the stored series has 168 values, equal to the pending artifact's values at the same month-day-hour of the weather year (1–7 July);
   - `snapshot_weightings` is unchanged (`pd.testing.assert_frame_equal`).
2. **T-R2** `test_the_template_still_solves_after_apply`, marked `@pytest.mark.live_solve`: after T-R1's apply on `eh_h2_hub`, solve as `test_eh_staged_hubs.py::_solve` l.166-188 does; the status is `ok` or `optimal`.
3. **T-R3** `test_the_weather_answer_names_the_window`: `GET /profiles/weather` on the template gives `window == {"first": "2030-07-01 00:00:00", "last": "2030-07-07 23:00:00", "hours": 168, "runs": 1}`. On a full 2023 hourly year, `window is None`. Add this test to `tests/test_profile_weather_bar.py` instead if it fits there better.

### Backend: existing files that must pass unchanged

`test_profile_gen_presets.py`, `test_profile_gen_engine.py`, `test_profile_gen_atlite_source.py`, `test_profiles_router.py`, `test_profile_weather_bar.py`, `test_profile_gen_snapshot_window.py`, `test_profile_gen_shape.py`, `test_profile_readiness.py`, `test_profile_batch_apply.py`, `test_chat_tools_profiles.py`, `test_chat_tools_endpoint_map.py`, `test_tool_error_kind_manifest.py`, `test_assistant_inventory.py`, `test_write_surface_lock_policy.py`, `test_project_route_acl_policy.py`.

### Commands

From the repo root; install once with `pixi install --frozen -e default -e test`:

```bash
pixi run gui-tests tests/test_profile_tuning.py tests/test_profile_compare.py tests/test_profile_template_week.py
pixi run gui-tests tests/test_profile_gen_presets.py tests/test_profile_gen_engine.py tests/test_profile_gen_atlite_source.py tests/test_profiles_router.py tests/test_profile_weather_bar.py tests/test_profile_gen_snapshot_window.py tests/test_profile_gen_shape.py tests/test_profile_readiness.py tests/test_profile_batch_apply.py tests/test_chat_tools_profiles.py
pixi run gui-tests tests/test_chat_tools_endpoint_map.py tests/test_tool_error_kind_manifest.py tests/test_assistant_inventory.py tests/test_write_surface_lock_policy.py tests/test_project_route_acl_policy.py
pixi run python pypsa-gui/backend/smoke/assistant_inventory.py --check
pixi run gui-tests   # full backend suite before opening the PR
cd pypsa-gui/frontend && npx tsc -b   # the frontend must still build; you change none of it
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so test paths are relative to that folder. Use only the synthetic source; never call ERA5 or CDS.

## Out of scope / don't touch

- **Everything under `pypsa-gui/frontend/`.** That is task G-b, which starts after this PR merges.
- **Owned by the parallel Codex tasks D, E and F:**
  - `services/profile_shapes.py` and its `_gen_category` (F);
  - `find_capabilities` in the harness (E), and any new `services/not_via_chat.py` (E);
  - `routers/network_library_reference.py` and every library_reference route (D);
  - `tests/fixtures/route_inventory_phase0.txt`, except the one compare line above.
- `harness/workflows/renewable-profile.md`, `shape_generated_profile`, batch-apply chat arguments, Guided offers (RP-08, RP-10).
- **Generation flows that stay as they are:** "Generate defaults for all", per-row Generate and `DefaultsReview` behave as today. Tuning is per run and not saved per generator.
- `capability-inventory.json` by hand, `doc/release_notes.md`, `docs/agents/*`, `scripts/coord/*`.
- Campus files: `routers/campus_electrical.py`, `services/campus_*.py`, `frontend/src/**/campusElectrical*`, `CampusElectricalPanel*`.
- **Task E's route-disposition test:** task E adds `tests/test_route_chat_disposition.py`, which gives every backend route one chat disposition. This PR adds two routes. `GET /profiles/compare` is in `TOOL_ROUTES`, so it is covered. `GET /api/profiles/schema` is a GUI read (`UI_INTERNAL`). **Whichever of E and G-a merges second classifies the other's new routes** in that test and its lists. Say so in the PR body.
- `apply.py`, `shape.py`, `readiness.py`, `current.py`, `job.py`, `atlite_source.py`, `synthetic.py`: read and call them; do not change them. `routers/profiles.py` changes only where listed.
- In `routers/profiles.py`, leave `_preview_week`/`_dry_run` alone. They pick the lowest week for `p_max_pu` on purpose; the compare's own peak week is separate.
- No GUI for shaping. The compare takes a `shaped:` side only through the route and the tool.

## Repo rules

- Branch `outsource/profile-tuning-backend` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body:
  - starts with the line `Outsourced task G-a`, then follows `.github/pull_request_template.md`. Mark it as AI-generated;
  - says whether stretch S1 is done;
  - says which line of each new test fails on `master`;
  - gives one example JSON payload each of `GET /api/profiles/schema?technology=solar&preset=csi-fixed-35deg-south` and of a compare, for task G-b to build against;
  - lists the commands you ran.
- Add one release-note fragment, `doc/release-notes.d/outsource-profile-tuning-backend.md`:

  ```
  * Feat(pypsa-gui): profile tuning and compare, backend. New `GET /api/profiles/schema` lists a wind or solar preset's settings with their units, bounds and sources; tuning keys in `params` are validated (`invalid_params`) and mapped onto the preset. Losses and derate now apply to the synthetic source too. New `GET /api/projects/{name}/profiles/compare` and the assistant's read-only `compare_profiles` compare two profiles on the model's snapshots (full-load hours, capacity factor, quantiles, monthly means, duration curves, peak week). The weather answer names the dates a sampled model (an Energy Hub week) takes from the weather year.
  ```

  Never edit `doc/release_notes.md`.
- Tests and docs go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files. Make no paid model calls, and no ERA5/CDS downloads.

## Done when

Core:

- [ ] `GET /api/profiles/schema` serves both technologies from the presets, and out-of-range tuning is 422 `invalid_params` on estimate and generate.
- [ ] `losses` and `derate` change the synthetic series (B-T4, B-T5). Untuned generation is byte-identical (B-T8).
- [ ] `GET /api/projects/{name}/profiles/compare` returns both sides' stats, deltas, monthly means, the 100-point duration curves and the peak week, all on the snapshot clock, multi-period models included (B-C9). It writes nothing (B-C7).
- [ ] `compare_profiles` returns the route's payload. The inventory is regenerated and `--check` passes. The three new error kinds are in `tool-error-kinds.json`.
- [ ] `GET /profiles/weather` carries `window`, and T-R1, T-R2 and T-R3 pass.
- [ ] All new tests pass, and each fails on `master`. The existing files listed above pass unchanged.
- [ ] The full `pixi run gui-tests` and `npx tsc -b` are green, or the PR lists each failure that also fails on `master`.
- [ ] The two `CONTEXT.md` entries and the release-note fragment are present.
- [ ] The diff touches only the files listed under "Files to touch". `route_inventory_phase0.txt` gains exactly one line.
- [ ] One PR on `outsource/profile-tuning-backend`, with a body starting `Outsourced task G-a`.

Stretch, only if done: S1 staged case.

## Decisions already made

Settled in the plan review; do not reopen them in the PR.

- **How tuning travels:** flat keys in the existing `params`, validated and mapped in `presets.resolve_params`, so the router check, the job runner and the chat tools share one funnel. No new `tuning` request field.
- **Losses and derate:** `cf = min(cf, 1 − derate) × (1 − losses)`, applied after the source for both sources. Derate is a power cap; losses scale everything. No second code path that caps the power curve.
- **Bounds:** hub height 20–200 m, cut-out 15–35 m/s, derate 0–0.5, losses 0–0.5, tilt 0–90°, azimuth 0–360° (180 = south, atlite's convention), tracking `none` | `horizontal`, inverter efficiency 0.8–1.0.
- **ERA5-only keys on the synthetic source** are accepted and recorded in the meta as `tuning_not_applied`, not refused, because the chat may pass them.
- **The compare's axis:** both sides go onto the model's snapshot clock through `prepare_profile`, so the compare shows exactly what Apply would write. FLH is the snapshot-weighted mean × the timestep year's hours, so an Energy Hub week weighted ×52 reads as a year. Multi-period models follow the rule under `compare.py` above.
- **Compare is a GET** (`?a=&b=`): a read, so no lock-policy entry and no CSRF.
- **Tuned settings are not remembered per generator.** The applied profile's meta records `params` and `tuning`; Regenerate and "Generate defaults for all" use the preset again.
- **The schema route's chat disposition is `UI_INTERNAL`**, because the chat reads the same values through `get_profile_parameters`.
- **Energy Hub templates:** no GUI path to put a weather profile on a template is in scope. T-R1 proves the backend path through the routes. A per-row "Replace with a weather profile" for `network_native` rows is a separate follow-up.
