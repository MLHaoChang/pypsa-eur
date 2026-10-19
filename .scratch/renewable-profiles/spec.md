<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Renewable profiles: the owner's vision, the gaps, and the extension spec

Status: needs-triage (owner questions RP-Q1–RP-Q4 open, see the end)
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`; profile stack head
`779feccd48aa9e819b49a0bf5b69dba8f9cbbdfe` (PR #398).

This file extends two existing documents and replaces neither:

- the engine spec and plan on PR #360
  (`docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md`,
  owner rulings D1:A–D6:A, D7:B);
- the hand-off on PR #398 (`pypsa-gui/docs/handoff-renewable-profiles.md`),
  whose planned E1b–E4 are folded into the tickets below.

Assessment page with the evidence: https://claude.ai/artifact/Lku1eH1xdKVG2Cgi1yUNZc

## The owner's vision (condensed, 2026-10-19)

- V1. The generator lives where users upload time series for generators. A
  renewable profile is one kind of time series. It belongs in the Time Series
  Manager, not in a separate island.
- V2. Default is automatic. A wind or solar generator with coordinates gets
  a profile generated with that technology's default settings.
  Coordinates come from the generator, or else from its bus.
- V3. Missing coordinates are highlighted with a reminder. The user must
  enter coordinates or set the profile manually.
- V4. The user picks the weather source and the weather year.
- V5. Hourly profiles (for example 8,760 h). The horizon and resolution come
  from the project's snapshots, not from a separate setting, where possible.
- V6. A fine-tuning panel with detailed, technology-specific parameters.
- V7. Chat-guided throughout. The copilot guides the flow and calibrates
  from observed deviations ("peak time and full-load hours match, but the
  tail doesn't, so dampen the tail"). It turns such feedback into parameter
  or profile adjustments for the chosen weather year and coordinates, and
  shows before/after.

## Where the stack stands (from the code on #398)

| Req | State | Evidence | Closed by |
| --- | --- | --- | --- |
| V1 | missing | `TimeSeriesManager.tsx` is untouched by every PR; the Renewables tab (`:56`) is upload-only; the generator is a sidebar island (`App.tsx:170`) | RP-04 |
| V2 | partial | `routers/profiles.py:106` resolves generators to points; nothing generates by default; the workflow interviews 8 steps first | RP-04, RP-08 |
| V2 coordinates | partial | the bus only (`profiles.py:122–130`) | RP-01 |
| V3 | missing, bug | `profiles.py:128` `float(x or 0.0)`: a bus at PyPSA's default 0/0 yields a profile at 0°E 0°N; spec §4 says refuse | RP-01, RP-02, RP-04 |
| V4 | covered | source and year on routes, chat and Studio; default year = last calendar year (`engine.py:245`) | RP-03 (default from snapshots) |
| V5 | partial | always a full calendar weather year at a user-picked resolution; partial-year and non-hourly horizons refused at apply (`apply.py:105–127`) | RP-03 |
| V6 | partial | preset dropdown plus a raw JSON textarea (`ProfileStudioSection.tsx:114–148`) | RP-05 |
| V7 guided | partial | 6 chat tools and the `renewable-profile` workflow; no `ui_open_panel` hand-off; no "offer when a profile is missing" | RP-08, RP-10 |
| V7 calibration | missing | no shaping code in `services/profile_gen/` | RP-06, RP-08 |
| V7 before/after | missing | apply returns a summary; preview is synthetic daily means only | RP-07 |
| batch | partial | one job carries many points; apply takes one generator per call | RP-09 |

## Extension requirements

Each requirement is testable. The ticket files hold the acceptance tests.

### R1 Coordinates (V2, V3)

- Resolution order: the generator's own `x`/`y` when the network carries them
  as finite numbers, else the bus `x`/`y`.
- A coordinate pair is missing when either value is NaN, or both are exactly
  0 (PyPSA's default; no European site sits at 0°E 0°N). An override is the
  explicit way out.
- Generating for a generator with missing coordinates is refused with
  `error_kind: missing_coordinates`. The refusal names every affected
  generator and both remedies (set coordinates, or upload a profile). No
  silent default anywhere: route, chat tool or job.
- A read-only readiness feed lists every wind or solar generator with its
  coordinate source (`generator`, `bus`, `missing`), its current profile
  state (`none`, `uploaded`, `generated` with provenance, `network-native`),
  and whether its provenance is stale (bus moved, horizon changed).

### R2 Horizon from snapshots (V4, V5)

- Default weather year: the snapshots' calendar year when it is a completed
  past year, else the last completed year. The user can always override it,
  and the default is shown as "from your snapshots".
- Default resolution follows the snapshots. Hourly snapshots get hourly
  profiles. Regular multi-hour steps (2 h, 3 h, …) get means of the hourly
  series over each step.
- Partial-year horizons: see RP-Q3. The recommended answer slices the
  weather year to the snapshots' month-day-hour window, with the leap-day
  rules unchanged.
- Each relaxation of an apply refusal names its new rule in `apply.py`'s
  rule table and is reviewed by the gatekeeper (data write).

### R3 Time Series Manager is the home (V1, V2, V3, V4)

- The Renewables tab shows, per generator row, a coordinate badge and a
  profile-source badge (uploaded / generated · source · year / none).
- A tab-level weather bar: source (Synthetic / ERA5) and weather year,
  prefilled from R2, asked once per project and remembered in project meta.
- "Generate defaults for all" queues one job for every ready generator, then
  applies each, as one undo step. Generators with missing coordinates are
  listed in a banner with two actions: "Set coordinates" (edits the bus or
  generator x/y through the normal edit path) and "Upload instead".
- A per-row "Generate" action and "Tune…" opens the tuning panel (R4).
- Profile Studio stays only as decided in RP-Q4.

### R4 Tuning panel (V6)

- Schema-driven per technology, from a backend schema endpoint. No raw
  JSON for the common knobs.
  - Wind: turbine preset, hub height, power-curve scale/derate, cut-out
    speed, availability/losses factor.
  - Solar: panel preset, tilt, azimuth, tracking (none, single-axis),
    inverter efficiency, losses factor.
- Each field shows its preset value, its provenance and its unit. Changed
  fields are marked, and "Reset to preset" restores them.
- Expert JSON stays as an advanced escape hatch.

### R5 Calibration and shaping (V7)

A deterministic post-processing layer over a generated per-unit series. It
never re-downloads weather. Its operations, applied as an ordered chain
stored in the profile's provenance:

- `target_flh` / `target_cf`: hit a full-load-hours or capacity-factor target
  within 0.5 % while keeping [0, 1]. Monotone scaling with clipping,
  iterated, because plain scaling breaks at the cap.
- `quantile_map`: map the duration curve onto a target duration curve,
  either from an uploaded observed series or from named quantiles
  (P10/P50/P90). The hour-by-hour ranking (timing) is kept.
- `tail_damp`: compress the low or high tail beyond a quantile by a factor,
  then re-hit the original FLH unless the user asks otherwise.
- `peak_shift` (solar diurnal or wind seasonal): shift the timing by N hours
  or days, for documented clock or timezone offsets only.
- Each operation is pure, with property tests: values stay in [0, 1], the
  targets are met, and the rank order is kept where promised.
- The shaped series is a new pending artifact that names its parent job.
  Apply stays the one write path (`assign_profile`).

### R6 Before/after (V7)

- A comparison read between any two of: the current `p_max_pu`, a pending
  artifact, or a shaped artifact. It returns FLH, CF, peak value and
  hour-of-day of the mean peak, P10/P50/P90, monthly means, and a
  downsampled duration curve and week overlay.
- The Time Series Manager shows it as an overlay plus a duration-curve chart
  before apply. The chat cites only those returned numbers.

### R7 Chat (V7)

- Default-first workflow: with a ready generator and a known weather choice,
  generate defaults without interviewing. Ask only for the weather source and
  year when unknown, and for coordinates when missing.
- New tools: `get_profile_readiness` (read), `shape_generated_profile`
  (write → pending artifact), `compare_profiles` (read). Batch apply on
  `apply_generated_profile` (`components: [...]` or `all_from_job: true`),
  behind one confirmation card.
- The parameters step hands off to the GUI with `ui_open_panel('timeseries')`.
- Deviation feedback maps to R5 operations: "the tail doesn't match"
  → `tail_damp` or `quantile_map`; "too many full-load hours" → `target_flh`;
  "peak is an hour late" → `peak_shift`. The copilot shows before/after
  (R6) and asks before apply.

## Owner questions (sent through the coordinator)

- RP-Q1 Landing shape for the stack (3 units / one by one / one PR).
- RP-Q2 What "automatic" means: one-click defaults (recommended) or silent
  background generation.
- RP-Q3 Partial-year horizons: slice the weather year (recommended) or keep
  refusing.
- RP-Q4 Profile Studio: keep it as an Expert advanced view linked from the
  Time Series Manager (recommended), or remove it.

Answers land here under `## Decisions`.
