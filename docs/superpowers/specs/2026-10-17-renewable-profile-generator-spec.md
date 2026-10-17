# Spec: Renewable Profile Generator (coordinate-driven time series)

**Status:** revised after independent review (2026-10-17) · **Author:** Kimi
session · **Owner:** Hao
**Depends on:** atlite==0.6.1 (pinned in pixi), the user time-series store
(`services/user_timeseries.py`), the solve queue, the chat harness.
**Vocabulary:** per `pypsa-gui/CONTEXT.md` — *profile* here means a per-unit
capacity-factor time series written to a component's `p_max_pu`; *Unavailable*
keeps its ADR-0001 meaning. New terms — **coordinate profile**, **cutout
cache**, **profile preset** — are added to CONTEXT.md by the plan's docs task.

---

## 1. The feature

A user adds a wind or solar asset to a Project — on the map or the canvas, at
real coordinates — and instead of hand-uploading a CSV, asks the system to
**generate the asset's hourly capacity-factor profile from its location**.

Two faces over one engine (ADR-0004 pattern):

- **Guided / chat:** the assistant asks the minimum questions (technology,
  preset, weather year), shows a clearly-labelled synthetic *estimate*,
  queues generation, and — once the job's pending artifact exists — applies
  it on a confirmation card. Both generation AND application have chat tools.
- **Expert:** a *Profile Studio* section exposes every parameter (turbine
  curve, hub height, panel, tilt/azimuth or tracking, correction factor) with
  a preview. Pre-download previews are always synthetic estimates and are
  labelled as such; after a generation job completes, the preview is read
  from the real pending artifact.

Either path ends in the same write: the profile becomes the component's
`p_max_pu` series in the live network **and** the per-project `user_ts.json`
store — persisting with the project, following renames/deletes, driving the
capacity-expansion solve, and riding into gridspine whenever a study uses the
project's own network as its dispatch source.

**Testing default:** the `default` preset needs no weather download — a
deterministic synthetic profile (closed-form, documented, seeded). Real
profiles download ERA5 weather through atlite.

## 2. Why this shape

- **Coordinates already exist.** Buses carry `x`/`y` (verify CRS is
  EPSG:4326 during the pre-flight probe); Sites group buses. The generator
  consumes the asset's bus coordinates, with an explicit coordinate override
  exposed in the API and chat schemas for off-bus siting.
- **One write path.** The user time-series store is keyed by
  `(component, attribute, column_name)`, is a per-ProjectContext view, and
  persists to `user_ts.json`. Writing into *that* store (attribute
  `p_max_pu`) gets persistence, undo/audit, rename tracking, and solve
  integration for free. **Note (review fix):** the store has no metadata
  channel today — it must be extended to carry per-series meta; that work is
  in the plan's Task 4.
- **gridspine needs no change.** Its case39 fixture keeps synthetic profiles.
  The backend must not import `gridspine.ingest` (engine cage, tested both
  directions).
- **Downloads are slow; writes are fast.** ERA5 cutouts are hundreds of MB
  and minutes of CDS latency. Generation is a queued background job producing
  a *pending artifact*; **applying** is a separate synchronous, confirmed
  write. Consequence (review fix): between job completion and apply, the
  network may have changed — apply re-resolves the component at apply time.

## 3. Scope

**In.**

1. `services/profile_gen/` — the engine: coordinate set + parameters → hourly
   per-unit series. Two sources behind one interface:
   - `synthetic` — deterministic, seeded, closed-form. Always available, no
     I/O. The `default` preset and all tests use it.
   - `atlite` — ERA5 cutout over the points' padded bounding box; **per-cell
     hourly CF time series** (review fix: in atlite 0.6.x the
     `capacity_factor=True` kwarg is deprecated sugar for
     `aggregate_time="mean"` and returns a *time-averaged scalar* — the
     time-series spelling is `aggregate_time=None` with no layout/matrix;
     the exact 0.6.1 call signature is confirmed in the pre-flight probe,
     ruling 27), nearest-cell `.sel()` per coordinate. atlite is imported
     lazily inside this one module only.
2. **Cutout cache** — shared `<app data>/cutouts/`, keyed by
   (module, bbox, year); `.part`-then-rename atomicity; server-side job
   timeout (CDS requests can wedge; user abort is not enough); atlite
   downloads ERA5 per month, giving natural 12-steps/year progress
   granularity for SSE.
3. **Profile presets** — `profile_presets.yaml`, per-field provenance
   (gridspine templates v2 discipline). Review fix: turbine/panel references
   must resolve **offline** — atlite preships only a small library and some
   named turbines trigger a runtime OEDB download, which would break
   "tests/demos never touch the network". Presets therefore embed explicit
   curve dicts (V/P/hub_height supported by atlite), which also strengthens
   provenance. (Also noted: atlite ≥0.5 changed `add_cutout_windspeed`
   defaults, another reason configs are pinned verbatim.)
4. **Apply path** — `assign_profile(ctx, component, series, meta)`:
   alignment validation (§4 rules), write to the user-TS store (extended
   with meta), reapply to the live `_t` table, changelog stamp, dispatch
   marked stale. Overwrite semantics: regenerating overwrites the same store
   key; undo restores the prior state, including the network-native
   `p_max_pu` when no earlier user entry existed.
5. **Job integration** — third queue job kind `profile_gen` (inc-4 D2
   precedent). Review fix: D4's claim "a download never blocks a solve" is
   only true if the queue runs kinds concurrently — verify worker
   concurrency in the pre-flight probe; if the worker is serial, the plan
   either adds a second worker or the spec says honestly that generation
   queues behind solves.
6. **Router** — `/api/profiles/...`: presets (GET), estimate (POST,
   synthetic, labelled), generate (POST → job), pending artifacts (GET),
   apply (POST; body carries the coordinate-override option).
7. **Chat tools** — `list_profile_presets` (read), `get_profile_parameters`
   (read), `preview_renewable_profile` (read; synthetic estimate + summary
   stats, labelled), `generate_renewable_profile` (write → job),
   `list_pending_profiles` (read), `apply_generated_profile` (write;
   confirmation card; re-resolves the component and validates the artifact
   belongs to this project). **ADR-0002 live probe is definition of done.**
8. **Frontend** — Profile Studio (Expert): schema-driven form, preview,
   apply; Guided: assistant offers generation when a wind/solar asset lacks
   a profile.

**Out (ledgered).** Cell interpolation; offshore exclusions (region-scale
land eligibility is upstream's job); SARAH-3; hydro; wake modelling;
multi-investment-period support (v1 refuses — §4); ERA5 redistribution terms
beyond the metadata note.

## 4. Alignment & validity rules (review fixes 9–11, 13–14)

A generated series is hourly, tz-naive UTC, one weather year. Applying it to
a network follows **explicit rules — never silent reindexing**:

| Case | Rule |
|---|---|
| Snapshots span exactly that year, hourly | Align by timestamp. |
| Weather year is leap (8784 h), target year is not | Drop Feb 29, note in meta (`leap_day: dropped`). |
| Target year is leap, weather year is not | Refuse (fabricating 24 h is worse than refusing). |
| Partial-year or sub-hourly/segmented snapshots | Refuse with a typed error naming the supported shapes. |
| Multi-investment-period network (MultiIndex snapshots) | **Refuse in v1** with a typed error; tiling per period is a deliberate later increment, not a fallback. |
| `snapshot_weightings != 1` | Allowed; the series indexes hours, weightings stay the model's business. |
| Asset carrier not wind/solar, or artifact technology ≠ component carrier | Refuse at generate and at apply. |
| Bus has no coordinates and no override given | Refuse with guidance (set bus coordinates or pass the override). |

## 5. Decisions to confirm with the owner

| # | Question | Recommendation |
|---|---|---|
| D1 | Engine location | `pypsa-gui/backend/services/profile_gen/`; gridspine untouched. |
| D2 | CDS key handling | Env / new-format `.cdsapirc` (`url: https://cds.climate.copernicus.eu/api`, bare token; old UID:KEY 404s); `cdsapi>=0.7.2`; the CDS-account **licence acceptance** step is part of setup or downloads fail at runtime. Verify the pixi env's cdsapi pin in the probe. Never stored per project, never in chat, never logged. Missing key → typed refusal with these instructions. |
| D3 | Cutout cache | `<app data>/cutouts/`, shared, LRU-evictable by hand. |
| D4 | Queue kind | New kind `profile_gen`, after verifying worker concurrency (§3.5). |
| D5 | No-parameter default | `default` preset = synthetic source, labelled `source: synthetic` so it can never masquerade as weather-derived. |
| D6 | Profile metadata | Extends the user-TS store: source, preset, params, weather year, bbox, leap-day handling, generated-at, atlite version. |

## 6. Risks / honest constraints

- ERA5 access is per-machine setup (key + licence acceptance); without it
  only synthetic works, and UI/chat say so up front.
- First download per (bbox, year) takes minutes; the cache makes nearby
  sites instant afterwards.
- Nearest-cell ≈ 25–30 km: fine for energy profiles, not a siting study;
  UI copy must not oversell.
- atlite API drift is fenced behind one lazy-imported module.
- Tests never touch CDS: atlite path runs against a tiny committed fixture
  cutout (a few cells × a few days; its length contract is the fixture's
  length, not 8760).

---

## Review record

Independently reviewed 2026-10-17 (fresh-eyes agent, verdict "not executable
as written"): 4 High / 10 Medium / 6 Low findings. All High and Medium are
incorporated above — chat apply path (H1), atlite kwarg semantics (H5),
multi-period policy (H9), leap-year policy (H10), CDS setup completeness,
preset offline resolution, queue-concurrency verification, metadata channel,
carrier validation, coordinate override plumbing, overwrite/undo semantics,
artifact project-validation + path-traversal guard, job timeout, task-order
inversion, router mutation check, terminology (one term: *coordinate
profile*), preview honesty. Lows (pending-artifact eviction policy:
keep-last-N per project; lazy atlite import) are in the plan.
