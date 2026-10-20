# Hand-off: renewable profile generation (profile_gen → GUI + chat)

Audience: the next agent session (or human) picking up this work. Read this
first; it is self-contained even without the previous session's workspace.

## The goal (what the tool should achieve)

Let a user put a weather-driven capacity-factor time series on every wind and
solar generator with **zero configuration by default**, and **full control when
asked**:

1. **Default path** — pick a renewable generator (or all of them); its bus
   coordinates seed the site automatically; a default preset generates the
   profile; applying it writes `p_max_pu`. No coordinates typed, no parameters
   touched.
2. **Parametrized path** — when the user wants control, they tune the turbine/
   panel preset, weather source (synthetic offline vs ERA5 reanalysis via
   atlite), weather year, and resolution (hourly/daily/weekly) in the Time
   Series Manager's Renewables tab (the everyday surface) or the standalone
   Profile Studio (the power surface).
3. **AI-guided path** — the chat assistant leads the same flow via the
   `renewable-profile` workflow (scope → technology → parameters → weather →
   resolution → preview → generate → apply), can open the GUI panel for the
   user (`ui_open_panel('timeseries')`), and coaches CDS credential setup when
   ERA5 is wanted without a key.

Hard rules the implementation keeps:

- Generation NEVER writes to the network. A queued `profile_gen` job produces
  a pending artifact (`<storage>/profiles_pending/<job_id>.nc` + `.json`);
  applying is a separate, undoable, lock-gated write (`assign_profile`).
- Every refusal is a typed exception mapped to a 422 `{"error_kind",
  "message"}` — never a bare string, never a silent fallback.
- Leap-year and alignment rules live in `apply.py` and are enforced exactly:
  leap→non-leap drops Feb 29; non-leap→leap REFUSES; partial/sub-hourly/
  segmented horizons REFUSE; multi-investment-period snapshots tile the
  weather year into every period.
- Resolutions aggregate AT generation (hourly native; daily/weekly are means
  over the hour) and upsample AT apply (daily: month-day map; weekly:
  step-hold, same calendar year only, never tiled). There is no annual
  resolution — an annual mean is a scalar, not a profile.

## Done so far (stacked PR chain, merge in this order)

Phase 1 — the engine and its surfaces:
`#362 → #363 → #364 → #365 → #371 → #373 → #374 → #377 → #378 → #380`
(engine + synthetic source, cutout cache, atlite/ERA5 source, presets,
apply path, queue job kind, HTTP router, chat tools, Profile Studio panel,
docs). Recommend squash-merge of #371 (3 junk placeholder commits).

Phase C — chat-guided generation:
- #390 — resolution engine end to end (hourly/daily/weekly).
- #393 — `from_generators` on the chat tools (superseded shape; see #398).
- #395 — the `renewable-profile` guided workflow (8 steps, CDS coaching).
- #396 — docs + full E2E evidence.
- #394 — retro guardrails: `bound_ctx` test fixture, `TESTING.md`,
  `AGENTS.md` navigation, ruff D209/D213 config fix (parallel to #395).

Phase E — GUI default path (in flight):
- #398 — E1a: `from_generators` on the HTTP routes; one shared resolver
  (`points_from_generators` in `routers/profiles.py`); generate snapshots
  coordinates at enqueue time.

All pushed content was byte-verified against the local working tree.

## Planned next steps

- **E1b (frontend, next)** — Renewables tab of `TimeSeriesManager.tsx`:
  a wand action per generator row (generate with defaults → poll
  `profilesApi.pending` → auto-apply onto that generator → invalidate the
  `generator_profiles` query so the chart refreshes), plus "Generate defaults
  for all" in the tab header (one job with every renewable generator as
  `from_generators`, then apply each). Defaults: preset `default`, source
  `synthetic`, year = last completed calendar year, resolution `hourly`.
- **E2 (frontend)** — collapsible "Tune parameters" section in the Renewables
  tab: preset picker (`profilesApi.presets`), source Seg (Synthetic /
  ERA5-atlite), weather year, resolution Seg. Collapsed by default; the
  default path stays one click.
- **E3 (markdown)** — `harness/workflows/renewable-profile.md`: default-first
  rule (generate with defaults unless the user asks to tune) + GUI hand-off
  via `ui_open_panel('timeseries')` in the parameters step.
- **E4** — QA gate (full backend profile_gen+harness suites, full frontend
  suite), extend `chat_probe_phase_c.py`-style live probe to the GUI default
  path, CONTEXT.md + release-note touch-up.

## Operational handbook (this environment)

- Clone: `repo/` in the session workspace; backend `repo/pypsa-gui/backend`,
  frontend `repo/pypsa-gui/frontend`.
- Python venv with all deps (atlite, fastapi, pytest-xdist, ruff):
  `.venv-pd2/bin/python` at the workspace root. Backend tests run from
  `backend/` with `PYTHONPATH=<workspace>/repo`:
  `…/.venv-pd2/bin/python -m pytest tests/<files> -q -p no:warnings -n 8 --dist loadfile`
- Frontend: `npx tsc -b` (typecheck), `npx vitest run <file>` (one file ~2 s;
  full suite ~2 min, QA gates only). jest-dom matchers are NOT registered —
  use `textContent` + `toMatch`. If vitest hangs: `pkill -f vitest`, redirect
  output to a file, poll with `tail`.
- **No local git credentials.** Branch/PR via the GitHub MCP tools; push file
  contents via the MCP endpoint (the previous session's workspace has
  `mcp_push.py`, `push_verified.sh` — push + byte-verify against
  raw.githubusercontent — and `check_changed.sh` — ruff F/E9 blocking + tsc +
  focused pytest). If those scripts are gone, recreate them; NEVER
  `git checkout/restore` modified files (uncommitted work would be lost).
- Chat-tool tests that touch the network need the `bound_ctx` fixture
  (conftest) — without it the foreground network is empty.
- E2E fixture: `/tmp/pgs-e2e/projects/E2E Prof` — one wind generator `g_wind`
  at bus (8.0, 50.0), full-year 2023 hourly snapshots, local mode.

## Key APIs (verified)

- `services/profile_gen/engine.py`:
  `generate_profile(points, technology, params=None, source="synthetic",
  year=None, resolution="hourly", now=None) -> ProfileResult(series, meta)`;
  typed errors in the same module; `RESOLUTIONS=("hourly","daily","weekly")`.
- `services/profile_gen/apply.py`: `assign_profile(ctx, component,
  series: pd.Series, meta) -> summary` (alignment table + provenance meta).
- `routers/profiles.py`: `points_from_generators(names)` (shared resolver),
  `EstimateRequest`/`GenerateRequest` (points optional; `from_generators`,
  `resolution`), apply endpoint pushes undo first.
- Chat tools (`services/chat_tools.py`, catalogue in `harness/catalogue.py`):
  `list_profile_presets`, `get_profile_parameters`,
  `preview_renewable_profile`, `list_pending_profiles`,
  `generate_renewable_profile`, `apply_generated_profile`.
- Frontend: `src/api/profiles.ts` (`profilesApi.presets/estimate/generate/
  pending/apply`), `src/pages/ProfileStudioSection.tsx`,
  `src/pages/TimeSeriesManager.tsx` (Renewables tab, `GenItem`).
