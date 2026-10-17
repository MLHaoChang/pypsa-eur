# Plan: Renewable Profile Generator (coordinate-driven time series)

**Spec:** `docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md`
(revised after review — the alignment rules and the chat apply path are
binding here).
**Style:** repo conventions — TDD with RED evidence and a mutation per task
(including the router task); path-limited commits; gates
`pixi run gui-tests` and `npx vitest run` + `npx tsc -b`.

**Pre-flight probes (before any task):**
1. Read the *installed* atlite 0.6.1: the exact per-cell time-series call
   (`aggregate_time=None` semantics — NOT `capacity_factor=True`, which is
   deprecated sugar for a time-averaged scalar), the Cutout constructor, and
   whether every preset's turbine/panel reference resolves **offline**
   (several named turbines trigger a runtime OEDB download). Ruling 27.
2. Verify the queue's worker concurrency (`services/solve_queue.py`) —
   spec §3.5's non-blocking claim depends on it.
3. Verify the user-TS store's write API and whether it has a metadata
   channel (review fix 12 — Task 4 assumes it gets one).
4. Verify bus `x`/`y` CRS is EPSG:4326.
5. Verify the pixi env's `cdsapi` version against spec D2.

---

## Task 1 — Engine interface + synthetic source

**Files:** `services/profile_gen/__init__.py`, `services/profile_gen/engine.py`,
`services/profile_gen/synthetic.py`, `tests/test_profile_gen_engine.py`.

- [ ] `generate_profile(points, technology, params, source, year) ->
  ProfileResult{series, meta}`; meta = source, preset, params, weather year,
  bbox, leap-day policy, generated-at, atlite version (spec D6).
- [ ] `synthetic.py`: seeded, deterministic; solar = diurnal clear-sky shape
  (day length from latitude/date) × seasonal envelope × cloud factor, zero at
  night; wind = smooth seeded AR-like shape around a seasonal mean; [0,1]
  clamped, no NaN.
- [ ] Contract tests: hourly tz-naive UTC index; length == hours of the
  requested year (8760/8784) for full-year generation; bounds; meta
  completeness; determinism (same inputs → byte-identical).
- [ ] Carrier guard at the boundary: technology ∉ {wind, solar} → typed
  refusal (spec §4).
- [ ] Mutations: drop night-zero → solar-night test red; unseeded RNG →
  determinism test red.

## Task 2 — Cutout cache + downloader

**Files:** `services/profile_gen/cutout_cache.py`,
`tests/test_profile_gen_cutout_cache.py`.

- [ ] `ensure_cutout(bbox, year, module="era5", progress=None, stop_event=None)
  -> Path` under `<app data>/cutouts/`; key `era5_{bbox_hash}_{year}.nc`.
- [ ] `.part`-then-rename atomicity; stale `.part` resumed or deleted, never
  served; **server-side timeout** (spec §3.2).
- [ ] CDS credential preflight (spec D2): missing/legacy-format key → typed
  refusal with setup instructions, before any download. Never log the key.
- [ ] `progress`/`stop_event` hooks shaped for the queue runner; report
  per-month granularity (atlite downloads ERA5 monthly).
- [ ] Tests (stubbed downloader, no network): cache hit; `.part` never
  returned; refusal path; abort path; timeout path.
- [ ] Mutation: return the `.part` path → visibility test red.

## Task 3 — Atlite source (after the cache exists)

**Files:** `services/profile_gen/atlite_source.py`,
`tests/test_profile_gen_atlite_source.py`,
`tests/fixtures/profile_gen/` (tiny committed cutout, built once by
`tools/make_profile_gen_fixture.py`).

- [ ] Per-cell hourly CF via the probe-verified 0.6.1 call
  (`aggregate_time=None` spelling), nearest-cell `.sel()` per coordinate;
  atlite imported lazily inside functions, only in this module (cage-style
  import test across `profile_gen/`).
- [ ] Tests run against the fixture cutout: asserted length is the
  **fixture's** length, values match a recorded golden, coordinates map to
  the intended cells.
- [ ] Mutation: `+1`-shifted cell selection → the cell-mapping test red.

## Task 4 — Presets

**Files:** `services/profile_gen/presets.py`,
`services/profile_gen/data/profile_presets.yaml`,
`tests/test_profile_gen_presets.py`.

- [ ] Schema `{name, technology, source_type, params, provenance}` with
  per-field provenance; unknown preset / missing field refused at load.
- [ ] Turbine/PV configs are **explicit embedded curve dicts** (offline by
  construction — review fix 6), not names resolved through atlite's library.
- [ ] Ship: `default` (synthetic) per technology; ≥2 wind, ≥2 PV presets.
- [ ] Mutation: silently filling missing provenance → refusal test red.

## Task 5 — Apply path + user-TS metadata channel

**Files:** `services/profile_gen/apply.py`, `services/user_timeseries.py`
(meta channel — review fix 12), `tests/test_profile_gen_apply.py`,
`tests/test_user_timeseries_meta.py`.

- [ ] `assign_profile(ctx, component, series, meta)` implementing spec §4's
  alignment table exactly: timestamp align on exact-year match; drop-Feb-29
  when source is leap and target is not (meta `leap_day: dropped`); refuse
  leap target with non-leap source, partial/sub-hourly/segmented snapshots,
  and multi-period networks — each a distinct typed error with its own test.
- [ ] Write store entry `(component, "p_max_pu", name)` + meta; reapply `_t`;
  changelog; dispatch stale via `services/dispatch_status.py`; undo restores
  prior state **including the network-native profile when no earlier user
  entry existed**.
- [ ] Locking/ACL per the write-surface ratchets
  (`test_write_surface_lock_policy.py`, `test_project_route_acl_policy.py`).
- [ ] Overwrite semantics: regeneration overwrites the same key,
  changelog-stamped.
- [ ] Mutation: skip the freshness flip → dispatch-stale test red.

## Task 6 — Queue job kind `profile_gen` + pending artifacts

**Files:** `services/solve_queue.py`, `services/solve_job_store.py`,
`db/models.py` + alembic migration, `services/profile_gen/job.py`,
`tests/test_solve_queue_profile_gen.py`.

- [ ] Runner: `ensure_cutout` → `generate_profile` → pending artifact
  `profiles_pending/<job_id>.nc` + meta JSON. Never touches the network.
- [ ] Artifact hygiene: id validated as belonging to the project (path-
  traversal guard); keep-last-N eviction per project.
- [ ] Abort mid-download → job `aborted`, no partial cutout, no artifact;
  boot reconciliation marks interrupted.
- [ ] If probe 2 found a serial worker: a second worker for `profile_gen`,
  or the honest sentence in the API copy — decided by measurement, recorded
  here.
- [ ] Mutation: runner ignores `stop_event` → abort test red.

## Task 7 — Router `/api/profiles`

**Files:** `routers/profiles.py`, app registration,
`tests/test_profiles_router.py`.

- [ ] `GET /api/profiles/presets`; `POST /api/profiles/estimate` (synthetic,
  labelled; downsampled preview, never a raw 8760 body);
  `POST /api/projects/{name}/profiles/generate` (body accepts the coordinate
  override) → job; `GET …/profiles/pending`; `POST …/profiles/apply`.
- [ ] Apply re-resolves the component at apply time; renamed/deleted since
  job completion → typed refusal; bus moved far from the generated
  coordinate → warning field in the response.
- [ ] CDS-missing refusal surfaces as 422 with the setup instructions.
- [ ] Thin-over-service tests (patch + assert call); 403/404 tenancy.
- [ ] **Mutation:** drop the ACL check → the 403 test red.

## Task 8 — Chat tools (both halves of the loop)

**Files:** `services/chat_tools_schema.py`, `services/chat_tools.py`,
`tests/test_chat_tools_profile_gen.py`.

- [ ] Reads: `list_profile_presets`, `get_profile_parameters`,
  `preview_renewable_profile` (synthetic estimate + summary stats, labelled),
  `list_pending_profiles`.
- [ ] Writes: `generate_renewable_profile` (queues the job) and
  `apply_generated_profile` (confirmation card; the chat apply path the
  first draft lacked). Both accept the coordinate override; both validate
  technology vs carrier.
- [ ] Invariants green: `len(TOOLS)==len(DISPATCHERS)`, TOOL_ROUTES, `_h`
  AST scan.
- [ ] **ADR-0002 live probe, definition of done:** transcript of
  presets → preview → generate → pending → apply through the chat panel.
- [ ] Mutation: drop one tool from DISPATCHERS → registry-invariant test red.

## Task 9 — Frontend: Profile Studio (Expert) + Guided hook

**Files:** `frontend/src/pages/ProfileStudioSection.tsx`,
`frontend/src/api/profiles.ts`, tests beside them.

- [ ] Schema-driven form from `GET /api/profiles/presets`; estimate preview
  labelled "synthetic estimate"; post-job preview from the pending artifact;
  Apply via the sanctioned write discipline.
- [ ] Empty state on wind/solar assets without a profile: "Generate from
  location". CDS-missing state renders the setup instructions.
- [ ] Component tests only; nothing downloads.
- [ ] Mutation: apply sending a partial row → the spread test red.

## Task 10 — Docs and vocabulary

- [ ] `CONTEXT.md`: **coordinate profile**, **cutout cache**, **profile
  preset**, the synthetic-vs-atlite `source` distinction.
- [ ] `docs/REPO-GUIDE.md`: new row (`services/profile_gen/`).
- [ ] Release note under `doc/release-notes.d/`.

## Out of scope (named so nobody drifts)

Cell interpolation; offshore exclusions; SARAH-3; hydro; wake modelling;
multi-investment-period support (v1 refuses by design); gridspine code
changes (profiles ride in the network's `p_max_pu`).

## Review record

Reviewed 2026-10-17 by a fresh-eyes agent ("not executable as written";
4 High / 10 Medium / 6 Low). All findings incorporated: task order fixed
(atlite source now follows the cache; presets are offline-embedded curves);
chat loop closed with `apply_generated_profile` + `list_pending_profiles`;
atlite kwarg semantics corrected and deferred to the probe; multi-period and
leap-year policies specified in spec §4; metadata channel added as Task 5
work; carrier validation, coordinate override, overwrite/undo, artifact
guards, server-side timeout, and a router-task mutation added.
