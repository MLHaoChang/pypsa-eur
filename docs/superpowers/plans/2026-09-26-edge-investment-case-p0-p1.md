# Edge Investment Case — Implementation Plan (P0 contracts & seams, P1 commercial layer; P2–P8 outline)

> **For agentic workers:** implement work package by work package, TDD (red → green → verify), then an
> independent implementation review per work package until it passes, then the phase e2e QA gate before
> the next phase. Prefer extending existing seams (`services/solver/objective.py` wrappers,
> `services/results/compute_*`, `services/adequacy/*_runner.py` pattern, `RESULT_STATE_KEYS`) over new
> parallel stacks. House rules: `.cursor/skills/gui-backend-change/SKILL.md`, `.cursor/rules/pypsa-gui-backend.mdc`.
>
> **Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` (revised after adversarial review;
> decisions 1–20). **Companion notes:** `docs/superpowers/notes/2026-09-26-*.md`.
>
> **Plan status:** v2.1 — passed the plan review loop (round 1 `PASS WITH CONDITIONS`, round 2 `PASS WITH CONDITIONS`, all conditions closed in text; see § Plan review). Implementation starts at P0.

**Goal of P0–P1.** Freeze the contracts (commercial, finance, archetypes, report), make the tool bill and
optimise against the client's point-of-connection (PoC) tariff at 15-minute settlement with the objective
still reconciling to the cost breakdown, and persist the new report through project bundles. No finance
arithmetic yet (P4), no UI beyond what P1's QA needs.

**Environment.** Backend tests: `cd pypsa-gui/backend && python -m pytest -m "not slow"`; QA drivers:
`python tests/run_qa_drivers.py`; frontend: `cd pypsa-gui/frontend && npx vitest run`. In containers
without pixi, a venv with the pins from `pixi.toml` (`pypsa==1.1.2`, `linopy==0.8.0`, `highspy==1.14.0`,
`xarray<2025.7`) plus `backend/requirements.txt` runs the backend suite (verified 2026-09-26).

**Dependency order.**

```
P0 contracts & seams
 ├─ WP0.1 models  ─► WP0.2 packs ─► WP0.5 persistence
 ├─ WP0.3 physical-quantity seam
 ├─ WP0.4 sensitivity scenario type
 └─ WP0.6 tripwire tests
P1 commercial layer (dispatch-grade)
 ├─ WP1.0 weightings-from-freq + 15-min fixture
 ├─ WP1.1a series store + migration ─► WP1.1b router + org ACL ─► WP1.1c bundle pins
 ├─ WP1.2 tariff_engine core (energy / TOU / fixed)  ── oracle for everything below
 ├─ WP1.3 PoC price materialisation + export binding (+ typed SolverConfig.commercial)   ── gap 0 from here on
 ├─ WP1.4a firm / non-firm static / available_from ─► WP1.4b envelope + FCA
 ├─ WP1.5a-0 linopy new-variable spike ─► WP1.5a peak vars ─► WP1.5b ratchet ─► WP1.5c tiers
 ├─ WP1.6 group contract
 ├─ WP1.7 reload-safe cost_breakdown rows (persisted terms) + objective gap 0 before/after save→load
 └─ WP1.8 preflight validation (+ arbitrage-loop warning)
P2 billing pass & contracts … P8 (outline, §P2–P8)
```

---

## Phase QA gate + TDD protocol (mandatory)

1. **Red:** write the work package's failing tests first (pytest `test_*.py`; `live_solve` mark where a
   solve is needed; a unit-level red must exist even when a solve test is skipped in an environment).
2. **Green:** minimum code; no drive-by refactors of the god modules (`routers/network.py`,
   `routers/results.py`, `services/solver_service.py`, `services/pypsa_service.py`).
3. **Verify:** work package tests + the facade/seam tripwires (`tests/test_results_seam.py`,
   `tests/test_results_facade_surface.py`, `tests/test_solver_facade_surface.py`, chat guard tests).
4. **Implementation review (independent):** an adversarial reviewer reads the diff against the work
   package's acceptance list and the spec; verdict `PASS` | `PASS WITH CONDITIONS` | `FAIL`. Conditions are
   closed in the same work package before moving on; `FAIL` → fix and re-review.
5. **Phase e2e QA gate:** run the phase's QA driver(s) + the full backend `not slow` suite + frontend
   vitest; independent assessor verdict `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO`; record it under the
   phase with a link to the findings note in `docs/superpowers/findings/`.
6. Do not start Phase N+1 before Phase N's gate is `GO` (or conditions closed).

**Gate rubric:** acceptance met; no rebuild of shipped work; no scope leak into later phases; red→green
evidence; every unresolvable number `null`+flag (ADR-0001); objective reconciles (gap 0) on every solve
fixture; nothing under `services/{commercial,finance,library}` imports routers or `solver_service`.

---

## Review deltas carried from the spec review (bind this plan)

| Finding | Where it lands |
|---|---|
| F1 rolling helper has no per-window hook, forbidden multi-period | P6 builds `realistic_dispatch.py` as its own driver; P1 touches nothing rolling |
| F2 new objective terms must reconcile | **WP1.7** cost_breakdown rows + gap 0 acceptance on every P1 fixture |
| F3 15-min weightings | **WP1.0** first in P1 |
| F4 series need a home | **WP1.1** Library series store |
| F5 report persistence | **WP0.5** `RESULT_STATE_KEYS` + bundle round-trip |
| F6/F7 naming & placement | runner under `services/finance/`, thin `compute_*` results, `start_*` pattern |
| F8 peak vars under representative periods / windows | **WP1.5a** `not_established` months; **P6** running-max carry |
| F9 non-convex tiers | **WP1.5c** flag + predicted-tier rate |
| F10 gap attribution per item kind | **P2 WP2.3** |
| F15 objective = site cost | spec decision 9; **WP1.7** names the rows accordingly |
| F16 billing core before LP bindings | **WP1.2** precedes WP1.3+ |
| F17 missing WPs | WP0.5, WP0.6, WP1.0, WP1.1, WP1.7, P2 WP2.5, P5 tab/route |
| Plan review R1-F1 reload-safe reconciliation | WP0.5 persists `last_commercial_terms`; WP1.7 recomputes rows from persisted data, gap 0 before and after save→load |
| R1-F2 materialisation must precede model build | WP1.3 hook in `run_simulation` after `_reapply_ts`, before `_apply_modelling_assumptions` |
| R1-F3 no `add_variables` precedent | WP1.5a-0 spike |
| R1-F4 library path / ACL | WP1.1a reserved `_library/` prefix, `taken_names` test, SQLite migration tripwire |
| R1-F6 typed commercial config | WP1.3 `CommercialConfig` on the schema, dict on the dataclass, `types.ts` mirror |
| R1 (c) gap 0 per WP | acceptance line on WP1.3–WP1.6 |
| F18 oversized WPs | WP1.5 split a/b/c; P4 WP4.2a/b, WP4.3a/b |

---

## Phase 0 — Contracts & seams (no solver behaviour)

### WP0.1 Pydantic contracts + report skeleton
Files: `backend/models/commercial.py`, `backend/models/finance.py`, `backend/models/flex_archetypes.py`,
`backend/tests/test_investment_case_contracts.py`, fixtures `backend/tests/fixtures/investment_case/*.json`.

- [ ] Red: tests that (a) every model in spec §4 round-trips `model_validate_json(m.model_dump_json()) == m`;
  (b) `IcSectionStatus = Literal["ok","not_established","skipped"]` rejects `"missing"`; (c)
  `InvestmentCaseReport` model validator requires `completeness` keys == `IC_REPORT_SECTIONS`; (d)
  `TariffItem` rejects `tiers` with non-monotone thresholds and `ratchet.share ∉ (0,1]`; (e)
  `ConnectionAgreement(kind="fca")` requires `envelope` or `curtailment_hours_per_year`; (f)
  `Participant.role` and `ValueStream.kind` enums exactly as spec §4.1; (g) `CashflowLine.provenance.mode ∈
  {"pf","realistic"}`; (h) `IC_EXPORT_KEYS` is a stable tuple and the skeleton exports all of them as `None`;
  (i) `CommercialConfig` (the typed solver-config sub-object: `poc_link`, `import_tariff_id`,
  `export_price_ref`, `connection`, `group_contract`, `demand_items`) validates and dumps to a plain dict.
- [ ] Green: models with docstring header pointing at the spec (mirror `models/energy_hub.py`); constants
  `IC_REPORT_SECTIONS`, `IC_PIPELINE_STAGES = ("design_solve","valuation_pf","valuation_realistic",
  "billing","participants","finance","uncertainty","assemble")`, `DEFAULT_IC_BUDGET_SOLVES = 30`,
  `MAX_IC_BUDGET_SOLVES = 120`, `empty_ic_section_map()`.
- [ ] Fixtures: `single_owner_participants.json`, `de_tariff_capacity_tou.json`, `us_tariff_demand_charge.json`,
  `fca_connection.json`, `ic_report_skeleton.json`, `ic_export_keys.json`, `commercial_config_minimal.json`.
- Acceptance: all (a)–(i) green; no imports from `services/`.

### WP0.2 Pack loader, hashing, `not_established` semantics
Files: `backend/services/finance/packs/__init__.py` (docstring only), `base.py`, `eu_de.py`, `us_federal.py`
(**stubs with `valid_from`, `source`, empty rule tables**), `backend/tests/test_finance_packs.py`.

- [ ] Red: `pack_hash(pack)` is sha256 of canonical JSON `[:16]` (same recipe as
  `services/adequacy/archetypes.py::pack_hash`); loading an unknown jurisdiction raises `PackNotFound`;
  a pack missing a rule returns `RuleLookup(status="not_established", reason=...)`, never a default number;
  hash changes when any field changes; hash stable across process restarts (fixture pins it).
- [ ] Green: `load_pack(jurisdiction, as_of: date)`, `RuleLookup`, `pack_hash`.
- Acceptance: tests green; `eu_nl`/`ca_federal` deliberately absent (P4 WP4.3b).

### WP0.3 Physical-quantity seam
Files: `backend/services/results/physical_quantities.py`, `backend/tests/test_physical_quantities_seam.py`.

- [ ] Red: `physical_quantities(n, cfg, *, result_df)` returns per-component-class frames of `p_nom_opt`,
  `build_year`, `lifetime`, `capital_cost` (annuitised, from `periodized_capital_costs`), `overnight_cost`,
  `fom_cost`, dispatched energy per period, PoC import/export energy per snapshot; **and** a test asserts the
  energy and fixed-cost totals equal what `compute_asset_economics` reports on the golden network
  (`tests/golden/fixture.py`). Until FOM is reconciled in `compute_asset_economics` (delegated fix, tracked in
  `notes/2026-09-26-edge-client-feature-benchmark.md` §1.6) the test compares `fixed_cost + fom_cost`
  explicitly and carries a TODO with that reference.
- [ ] Green: implementation reads only through `result_df` and `periodized_capital_costs`; no router imports.
- Acceptance: agreement test green on golden fixture; used later by finance (P4) and billing (P2).

### WP0.4 `sensitivity` scenario type
Files: `backend/routers/projects.py` (`_SCENARIO_TYPES`, and `_LEGACY_TAG_RE` which is derived from it),
`frontend/src/utils/scenarioType.ts` (`SCEN_TYPES`, `SCEN_TYPE_LABEL`, `TAG_RE`),
`frontend/src/utils/scenarioType.test.ts`, `backend/tests/test_scenario_type.py` (extend the existing file).

- [ ] Red: backend accepts `scenario_type="sensitivity"` on create/update and rejects `"foo"` with 400;
  legacy description `"[sensitivity] x"` is lifted like the other tags; frontend enum/label/regex tests include
  `sensitivity`.
- [ ] Green: add the value in both places. **No Alembic migration** (plain string column, `db/models.py` L76–80).
- Acceptance: both suites green; `ScenariosPanel.tsx` renders the badge (assert in existing test).

### WP0.5 Persistence of the report, billing cache and commercial terms
Files: `backend/services/project_context.py` (`RESULT_STATE_KEYS`, `ProjectSolverState` fields),
`backend/services/finance/report.py` (`store_ic_report`, `load_ic_report`, `ic_report_http_payload`),
`backend/tests/test_investment_case_persistence.py`, extend `backend/tests/qa_save_load_roundtrip.py`.

- [ ] Red: three keys — `investment_case_report`, `billing_frames`, `last_commercial_terms` — each stored in
  `solver_state` survives save → load of a project bundle (`results_state.pkl`); reset paths clear them (they
  iterate the tuple); `ic_report_http_payload` returns `(None, 204)` when absent and `(dict, 200)` when present;
  `_RESULTS_STATE_SCHEMA` (`routers/projects.py` L288) bumped only if the pickled shape changes.
- [ ] Green: add keys + `ProjectSolverState` fields; mirror `eh_report.store_eh_report`/`load_eh_report`.
- Acceptance: round-trip QA driver passes for all three keys; existing `qa_save_load_roundtrip` unchanged otherwise.

### WP0.6 Tripwire tests for the new packages
Files: `backend/tests/test_investment_case_tripwires.py`.

- [ ] Red→Green: regex/AST tests that `services/commercial/**`, `services/finance/**`, `services/library/**`
  never import `routers.*` or `services.solver_service`; each `__init__.py` is docstring-only; every
  `compute_*` in `services/results/` added by this programme is defined in its module (mirror rules (c),(e),(f)
  of `test_results_facade_surface.py`); new solver-side modules under `services/solver/` are covered by
  `test_solver_facade_surface.py` automatically — assert the file list includes them.

### Phase 0 e2e QA gate
- [ ] Full backend `not slow` suite green; frontend vitest green; `qa_save_load_roundtrip.py` green.
- [x] Findings note `docs/superpowers/findings/2026-09-27-ic-p0-contracts.md`.
- [x] Assessor verdict (2026-09-27): GO WITH BINDING CONDITIONS — (1) tripwire must resolve relative
  imports (closed); (2) seam test must separate objective vs generators weightings before WP1.0
  (closed); (3) solver-facade file-list assertion lands with the first services/solver module (due in
  P1); findings note counts corrected — see the findings note. **Phase 1 may start.**

---

## Phase 1 — Commercial layer, dispatch-grade

**Per-WP invariant from WP1.3 onward:** every solve fixture must reconcile — `objective_decomposition.gap_pct
== 0` within 1e-6 relative — at the end of each work package, not only at WP1.7. A WP that widens the gap is
not green.

### WP1.0 Weightings from frequency + 15-minute fixture
Files: `backend/routers/network_time_axis.py` (`set_snapshots`, `set_multi_period_snapshots`, `get_snapshots`,
`sample_representative_weeks`), `backend/routers/network_profiles.py` (template `freq` defaults),
`backend/tests/test_snapshots_freq_weightings.py`, fixture builder
`backend/tests/fixtures/investment_case/edge_15min.py` (3 buses: `grid`, `poc`, `site`; import Link `grid→poc`
with `eh_role="grid_import"`; PV generator (daytime); BESS StorageUnit; site Load with an **evening peak above
the PV window** so a demand charge always binds; 7 days × 96 snapshots; solved once module-scope like
`tests/golden/fixture.py::_SOLVED`, marked `live_solve`).

- [ ] Red: `POST /network/snapshots` with `freq="15min"` and no `weightings` yields
  `snapshot_weightings.objective == generators == stores == 0.25`; `freq="h"` yields 1.0; `freq="30min"` 0.5;
  a profile template generated on a 15-min axis has 96 points/day; `services/adequacy/metrics.py`
  `horizon_years` on the fixture equals 7/365 within 1e-9; `GET /network/snapshots` on a 15-min axis returns
  `can_sample_weeks=false` with reason code `not_supported_for_freq`, and `POST .../sample_weeks` returns 400
  with that code (today it silently samples 168 of 672 points: `network_time_axis.py` L689, L722);
  `compute_carrier_kpis` energy on the fixture equals Σ p × 0.25.
- [ ] Red: audit test that greps `services/` for `8760`/`freq="h"`/`Timedelta(hours=1)` and asserts each
  site is in an allow-list with a reason; the initial allow-list is pinned at the measured 38 hits / 19 files.
- [ ] Green: derive weighting from `pd.tseries.frequencies.to_offset(freq)` hours; keep explicit `weightings`
  override; sample-weeks refuses non-hourly axes with the reason code.
- Acceptance: fixture solves under HiGHS in < 20 s; all red items green.

### WP1.1a Library series store + migration
Files: `backend/services/library/__init__.py`, `series_store.py`, `backend/services/storage_paths.py`
(`library_dir(org_id, org_segment)`), Alembic `alembic/versions/0008_library.py`, `backend/db/models.py`
(`LibraryItem(org_id, kind, name, version, hash, path, created_by, created_at)`),
`backend/tests/test_library_series_store.py`.

- [ ] Red: `put_series(org, name, series, meta)` returns `TimeSeriesRef(id, version, hash)`; re-put with same
  content is idempotent (same version), changed content bumps version; `resolve(ref)` returns the exact
  series; files live under the reserved prefix `<projects_root>/_library/<org>/…` (never under a project
  directory) via `storage_paths.library_dir`; the path **always** carries the org id (`.library/<org_id>/` — a HIDDEN directory: implementation found that `legacy_migrate._scan_root` offers any non-hidden, non-UUID directory under the projects root as a claimable leftover it may move, which `_library/` would have been); `library_dir(root, org_id)` lives in `services/storage_paths.py` (it takes the root rather than an `org_segment` flag, because the org id is always in the path)
  whether or not `use_org_segment()` is on for project dirs; `taken_names` ignores `_library`;
  items persist across app restart; CSV+gzip payload hashed on canonical bytes (`float_format="%.10g"`,
  ISO tz-aware `date_format`) so idempotency is deterministic; `tests/test_alembic_sqlite.py::
  test_migrated_schema_matches_the_model_schema` is the migration tripwire (goes red until 0008 lands).
- [ ] Green: model, migration, store, path helper.
- Acceptance: tests green; `alembic upgrade head` on SQLite in the suite; Postgres upgrade is a manual
  checklist item in the findings note (no CI runner for compose).

### WP1.1b Library router + org access rule
Files: `backend/routers/library.py` (series endpoints only in P1), `backend/main.py` (router registration),
`backend/services/library_acl.py`, `backend/tests/test_library_acl.py`, `test_library_router.py`.

- [ ] Red: org member reads/writes own org's items; member of another org → 403; super-admin sees all;
  unauthenticated → 401; router registered (`/api/library/series` appears in the OpenAPI schema).
- [ ] Green: `library_acl.can_read/can_write(user, org)` (org membership or super-admin), distinct from
  `project_acl`. Chat tools for the Library are **deferred to P2 WP2.4** (stated deviation from "tools ship per
  phase": nothing in P1 is user-facing yet).
- As implemented (review round 1 PASS WITH CONDITIONS → closed): ACL unit tests live in
  `test_library_router.py` (no separate `test_library_acl.py`). Routes: `GET/POST /api/library/series`,
  `GET /api/library/series/{name}?version=`; `org_id` other than the caller's → 403 unless super-admin, an
  unknown org → 404 (was a 500 via the FK retry loop). Timestamps: all-with-offset (kept in `timezone`, else
  UTC) or all-naive (stored naive; naive + `timezone` → 422, the tariff engine's rule); mixed → 422. Names are
  stripped and refuse `/`, blanks and control characters (one path segment). `MAX_POINTS` = 1,000,000. A
  stale/missing payload → 409 `{code: "library_ref_stale"}`. `route_inventory_phase0.txt` regenerated.
  Re-review → PASS WITH CONDITIONS, closed: the point cap is checked in the handler (a pydantic `max_length`
  422 echoed the whole input, 29 MB); zone markers are case-insensitive and include `UTC`/`GMT`, and an index
  pandas parsed as aware counts as aware; names also refuse `.`, `..`, `?`, `#` and unprintable characters.

### WP1.1c Bundle pins library versions
- [ ] Red: a project bundle records `(id, version, hash)` of every series it references in a sidecar
  `library_refs.json`; reload with a missing/changed item yields a `library_ref_stale` issue rather than a
  silent substitution; `qa_save_load_roundtrip.py` extended.
- [ ] Green: sidecar write/read in `routers/projects.py` bundle paths (`_BUNDLE_FILES`).
- As implemented: `services/library/bundle_pins.py` (`collect_refs` walks the solver-config dict for any
  valid `PriceSeriesRef`-shaped dict; `write_pins` atomic, removes the sidecar when nothing is pinned;
  `check_pins(db, org, dir, config=)` → issues `{code: library_ref_stale, reason, id, version, hash, message}`,
  reasons `missing | changed | payload_unreadable | unpinned | sidecar_unreadable`). `SolverConfig.commercial:
  dict | None = None` lands here (WP1.3 types it on the API schema) so the pins have a carrier. Save writes
  the sidecar beside `solver_config.json`; open (`GET /api/projects/{name}`), `import_bundle` and a cold
  `activate` return `library_issues` and log each to the changelog; a resident activate returns `[]` (checked
  when opened). Pins are checked against the PROJECT's org, so a bundle imported into another org reports
  `missing` rather than resolving against that org's Library. Nothing rewrites a ref. Tests:
  `test_library_bundle_pins.py`; `qa_save_load_roundtrip.py` has four IC library-pin steps. Tampered
  sidecars are tested through bundles: an on-disk edit under a resident project is overwritten by its
  write-back on the next open.
- Review round 1 → PASS WITH CONDITIONS, all closed: a corrupt payload (`BadGzipFile`, `EOFError`,
  `zlib.error`, other `OSError`) is `LibraryRefStale` in `series_store.resolve`, and `check_pins` never raises
  (`payload_unreadable`); opening or importing a project without `solver_config.json` resets to defaults
  instead of inheriting the previous project's config; `activate` re-checks a RESIDENT context's in-memory
  refs (`check_pins(project_dir=None)`), since the session resolver, the dispatcher and path reads hydrate
  without a check; the sidecar uses the house `atomic_write_text`; pins are validated strictly (no `2.7 → 2`);
  a snapshot restore without pins drops the live sidecar.

### WP1.2 `tariff_engine` core (energy / TOU / fixed) — the oracle
Files: `backend/services/commercial/__init__.py`, `tariff_engine.py`, `backend/tests/test_tariff_engine_core.py`,
hand-rated fixtures `backend/tests/fixtures/investment_case/bills/*.json` (inputs + expected per-item amounts,
computed by hand and cross-checked in a spreadsheet committed as CSV).

- [ ] Red: `rate(dispatch: DataFrame[interval, import_mw, export_mw], tariff: Tariff, *, meter_history=None)`
  → frame `(interval, tariff_item, quantity, rate, amount)` + `monthly_bill`, `annual_bill`; tests: TOU with
  season × weekday × window resolution at 15-min; fixed item pro-rated by days; `measured_on` import/export/net;
  currency and unit conversions; leap-year February; DST transition (Europe/Berlin) does not double-count
  an interval; all-zero dispatch gives fixed-only bill; amounts sum to the hand-rated fixture to the cent.
- [ ] Green: pure pandas implementation; no LP awareness.
- Acceptance: fixtures match exactly; runtime < 1 s for a 15-min year.
- As implemented (review round 1 FAIL → fixed): `rate(dispatch, tariff, *, step_hours, timezone,
  billing_period=None)` returns `RatingResult(lines[interval, tariff_item, quantity_kwh, rate, amount],
  fixed_lines, monthly, annual, per_item, total, total_supported, flags, notes, unsupported_items)`.
  `step_hours` is required (float or per-row Series); tz-aware input requires `timezone`; naive input
  with a timezone is refused; NaN quantities and unrated intervals propagate to month and year; fixed
  items pro-rate by local HOURS (743/745 in DST months) or bill `billing_period`; unsupported items
  carry a reason. The engine is currency-agnostic (project currency is decision 13); "unit
  conversions" = MW × h → kWh, and non-`per_kwh` energy units are unsupported with a reason.

### WP1.3 PoC price materialisation, export binding, typed commercial config
Files: `backend/services/commercial/lp_bindings.py` (`materialise_poc_prices`, `_wrap_with_commercial_bindings`),
`backend/services/solver_service.py` (call `materialise_poc_prices` **immediately after the `_reapply_ts` block
(~L609) and before `_apply_modelling_assumptions`**, because `extra_functionality` runs after the linopy model is
built and `_reapply_user_ts_to_network` would otherwise clobber an earlier write; compose the wrapper after the
reserve-margin wrapper and before `_wrap_with_objective_scale`; re-export both in the facade block),
`backend/models/schemas.py` (`SolverConfigSchema.commercial: CommercialConfig | None`), `SolverConfig.commercial:
dict | None` (dumped at the router in `update_solver_config`), `frontend/src/api/types.ts` mirror,
`backend/tests/test_solver_config_parity.py` (**new**: dataclass field names == `SolverConfigSchema.model_fields`),
`backend/services/results/cost_breakdown.py` (`energy_import` / `energy_export` rows from the persisted
materialised prices — needed for this WP's gap-0 invariant), `backend/tests/test_lp_bindings_poc_price.py`.

- [ ] Red: after a solve with an energy-only TOU item bound, `n.links_t.marginal_cost[poc]` equals the
  tariff-period rates at every snapshot (persisted, not transient); BESS charging energy inside the cheapest
  TOU window is ≥ 90 % of total charging energy on the fixture; LP import cost equals
  `tariff_engine.rate(...)` energy-item total within 1e-6 (same resolution, convex ⇒ exact); export via a
  second PoC Link `poc→grid` with `marginal_cost = −price` makes PV export positive when price > 0 and zero when
  price = 0; `SolverConfigSchema.commercial` rejects a `poc_link` that is not a Link name (422) and round-trips
  through `update_solver_config` → `asdict` → `_solver_config_from_dict`; parity test green; **gap 0**.
- [ ] Green: materialisation from Library series / tariff periods; export Link; wrapper chaining identical to
  `_wrap_with_capex_budget`; `_safe_log(log_queue, "[COMMERCIAL] ...")`.
- Acceptance: all red items green; facade tests green.
- As implemented (deviations recorded):
  - **Inline tariff in P1.** Library tariff CRUD is P2 WP2.4, so `CommercialConfig.import_tariff: Tariff` carries
    it; a bare `import_tariff_id` is valid data but refused by the P1 binding (422 at the route). New fields
    `export_link` (the `poc→grid` Link) and `timezone` (set → naive snapshots are UTC, tariff windows read on
    the site clock; None → snapshots are the site clock, as `tariff_engine.rate(timezone=None)`).
  - **Export price resolution at config time.** `run_simulation` has a dozen callers (every adequacy study) and
    no org or DB, so `PUT /api/simulation/solver_config` resolves `export_price_ref` in the active project's org
    (the caller's org for an unsaved network), aligns it (coarser series held; uncovered → 422
    `export_price_coverage`) and writes `links_t["ic_export_price"]`, a custom dynamic attribute that survives
    `copy()` and netCDF and is not an Input, so the user-TS backup leaves it alone. A snapshot change leaves
    NaN rows, which the solve refuses (`commercial_binding_failed`) rather than pricing at 0. An unresolvable
    ref → 409 `library_ref_stale`.
  - **Solve time.** `materialise_poc_prices` runs in LOPF after the reapply/normalise/non-finite block and
    before `_apply_modelling_assumptions`; it rewrites `links_t.marginal_cost[poc|export]` = static + items
    (− price) every solve (idempotent) and emits `last_commercial_terms`. `net` items are split by direction
    (cost → import, revenue → export; exact without simultaneous import/export). Fixed, tiered, demand and
    capacity items are listed in `not_in_lp` with the WP that binds them. `_wrap_with_commercial_bindings`
    is composed after the reserve margin and before the objective scale; it chains only in WP1.3.
  - **Rows.** `cost_breakdown["commercial"] = {energy_import, energy_export, included_in_total: true}` from the
    persisted columns — a labelled split of the Links' statistics OPEX, so the gap stays 0 by construction.
  - Tests: `test_lp_bindings_poc_price.py` (21), `test_solver_config_parity.py`; `types.ts` mirrors the types.
  - Review round 1 → **FAIL**, fixed: (1) a persisted base/written record per priced Link
    (`links_t["ic_base_marginal_cost"]`, `links_t["ic_written_marginal_cost"]`) restores any Link a previous
    config priced — the hook runs on EVERY LOPF, commercial or not; (2) the base is the user's time-varying
    cost when present (a column that differs from what was written is a re-upload and becomes the new base);
    (3) PoC/export Links with `p_min_pu < 0` are refused; (4) snapshots where export pays more than import
    costs are counted (`simultaneous_flow_risk_snapshots`) and a solve that circulates is flagged in the rows;
    (5) a zoned Library series needs `commercial.timezone` (`timezone_required`), and alignment uses it;
    (6) the route refuses during a solve (`solver_in_flight`) and writes the price only when fully covered;
    (7) rows read the Links the last solve priced (`n.meta["ic_poc_links"]`, persisted) and flag
    `config_changed_since_solve` / `*_not_established` (ADR-0001); (8) the route dry-runs the adders
    (export item without export Link, unrated snapshots → 422); (9) refusals are `{code, message}`
    (`commercial_binding_invalid`, `library_org_unknown`); (10) a finer series is averaged per snapshot step and
    a resampled axis is refused via `n.meta["ic_export_price_axis"]`.
  - Re-review → **FAIL** (a Properties-panel edit or a snapshot-axis change re-adopted the tariffed column as the
    base: double counting). **Redesign: the PoC prices are TRANSIENT** like every other LP transform — applied
    for the solve on top of the user's cost and undone after it, so the user's `marginal_cost` is never modified
    on disk and no base/written bookkeeping exists. `Applied.commit()` (called only after a successful solve)
    persists the €/MWh added per Link in `links_t["ic_energy_price"]` and `n.meta["ic_poc_links"]`; the rows are
    recomputed from those and ADDED to the totals (they are not in `n.statistics()`). Averaging no longer spans
    gaps (window = min(next snapshot, one step)); `ic_*` frames are hidden from and refused by the time-series
    routes. **Plan acceptance amended:** "`links_t.marginal_cost[poc]` equals the rates (persisted)" is met by
    the persisted `ic_energy_price` frame; the LP sees the rates during the solve.
  - Round 3 → **PASS WITH CONDITIONS**, closed: `n.meta["ic_poc_links"]["priced"]` records the Links the
    solve priced — a priced Link whose record is gone is None + `*_not_established`, 0.0 only for a Link left
    unpriced (ADR-0001); `cost_rows` weights the block with the caller's `years(period)`, so the block equals
    what the totals carry; the upload route refuses `ic_*`; per-period vintage bounds on a PoC Link are refused
    (their clones would carry dispatch the rows do not read). Disclosure: `n.statistics()`/asset economics
    exclude the (transient) commercial terms; `cost_breakdown` and `horizon_system_cost` are the reconciled totals.

### WP1.4a Connection agreement — firm, non-firm static, `available_from`
Files: `backend/services/commercial/connection.py`, `backend/tests/test_connection_agreement.py`.

- [ ] Red: `firm` → `p_nom_max = import_cap`, `capital_cost = capacity_fee` on an extendable PoC Link; the
  optimiser sizes the connection strictly below the cap when the fee is high (strict inequality with
  tolerance); `non_firm_static` → `p_max_pu` scalar; `available_from` → PoC vintage with `p_nom_max=0` before
  that period on a two-period fixture; `apply_connection_agreement(n, agreement) -> Applied(undo)` restores every
  mutated attribute (mirror `apply_archetype_pack_detailed().undo()`); **gap 0** (the fee appears as
  `network_capacity` via WP1.7's persisted-data path — implement that row here).
- [ ] Green: implementation.
- As implemented: `services/commercial/connection.py` — `apply_connection_agreement(n, agreement, poc_link=,
  export_link=) -> Applied(facts, undo)`, validated before any mutation. The fee (`per_kw_year`, `per_kw_month`
  ×12) is €/MW/yr scaled by the horizon in years on a single-period axis (PyPSA charges `capital_cost` once per
  horizon) and used as is on a multi-period axis (period weightings count years). firm+fee → extendable in
  [0, cap] with `capital_cost += fee`; firm without fee → `p_nom = cap`; `non_firm_static` → `p_max_pu =
  cap / p_nom` (fixed physical p_nom required) and the fee on the contracted cap; `available_from` → `p_max_pu =
  0` before the date, plus `build_year` on a multi-period axis; `non_firm_dynamic`/`fca` → refused until WP1.4b.
  `run_simulation` applies it just before `_apply_modelling_assumptions` and chains the undo AFTER their
  restore. `cost_breakdown["commercial"]["network_capacity"]` is recomputed from config + `p_nom_opt` and ADDED
  to capex (the fee is not on the Link after the undo); gap 0 single- and multi-period. Tests:
  `test_connection_agreement.py` (18, 6 live).
- Review round 1 → **FAIL**, fixed: the fee is an **explicit LP term** on the PoC Link's `p_nom`
  (`connection.add_fee_term`, via `_wrap_with_commercial_bindings`), never `capital_cost` — so `overnight_cost`
  and fixed capacity cannot drop it (#1, #4); per period `w_obj(p)·fee·nyears(p)`, the same rule on flat and
  multi-period axes (#8), with the per-period €/MW committed to `n.meta["ic_connection_fee"]` on success; a fee
  on FIXED capacity (non-firm, fca) is a fixed charge reported as `network_capacity_fixed` outside the total,
  flagged `fixed_charge_not_in_lp` (#1); `available_from` closes by PERIOD on a multi-period axis (flat
  snapshots promoted repeat their timestamps) and by site-clock date on a flat one (#2, #10); non-firm
  availability respects an existing `p_max_pu` profile (#3); firm without fee caps a user-extendable Link
  instead of fixing it (#7); a fee with rolling/myopic is refused in P1 (#5, #6). `services/commercial/
  cost_rows.commercial_cost_terms` is the one source for the commercial rows: `cost_breakdown` folds them in as
  the component "Commercial" (Σ by_component == totals, per period) and `cost_totals.horizon_system_cost` adds
  the same items (#5, #9); a fee named by the config but not committed is None + `network_capacity_not_established`.
- Round 2 (with WP1.4b) → **FAIL**, fixed: adequacy sweeps no longer re-size the connection —
  `freeze_capacities` marks the network operational (`_ic_operational`), the agreement then pins the designed
  `p_nom_opt` (min = size, max = size + ε) with no fee term, and operational solves commit nothing; the block
  is years-weighted like the totals; FCA curtailment is chosen among OPEN snapshots only, accumulating real
  weights (never above target) with `target_hours`/`achieved_hours` disclosed; the fixed fee accrues only
  while open, per period; a DST gap at midnight shifts forward instead of raising; the route resolves, aligns
  and plans the registry BEFORE any write — FCA on an unsaved project → 409 `fca_needs_saved_project`, a full or
  invalid registry → 422 `stress_registry_invalid`, an unreadable registry is never overwritten → 409
  `stress_registry_unreadable`, and re-binding or clearing (`commercial: null`) removes this layer's FCA entry;
  a `links_p_max_pu` entry naming a missing Link fails closed (`link_missing`).
- Round 3 → **FAIL**, fixed: the writers no longer re-align a series the route already aligned (MultiIndex crash);
  class C gates a profiles entry naming a missing Link as a `profiles_incomplete` row (`note: link_missing`)
  instead of aborting the sweep; a study's planning limit on the PoC Link (`_ic_link_limits`, set by the lever
  study's `import_cap`) caps the agreement; the operational pin is `min(p_nom_opt, cap)` and discloses
  `operational_design_mismatch`; `freeze_capacities` sets the operational flag last; a PUT carrying
  `commercial: null` when none is stored does not re-bind.

### WP1.4b Connection agreement — dynamic envelope and FCA
- [ ] Design line first: `services/adequacy/stress.py` knows `VALID_KINDS=("parametric","profiles")`, and its
  `profiles` entries accept only `loads_p_set` and `generators_p_max_pu` (`_profile_series` L123–129, validator
  L191–197) — there is no Link slot. WP1.4b therefore **extends the `profiles` schema with a `links_p_max_pu`
  key** (validate / mutate / undo mirroring `generators_p_max_pu`; a small, disclosed extension, not a new
  kind) and expresses the FCA curtailment-hours entry as a `profiles` entry whose `links_p_max_pu` series is
  the PoC Link's envelope with the curtailed hours zeroed (deterministic, disclosed as `fca_synthetic_hours`).
  Red test for the schema extension comes first.
- [ ] Red: `non_firm_dynamic` → time-varying `p_max_pu` from the envelope series (resolved via WP1.1a);
  `fca` → envelope plus the stress entry registered; undo restores; **gap 0**.
- [ ] Green: implementation.
- As implemented: `stress.py` profiles accept `links_p_max_pu` (validate / mutate / undo like
  `generators_p_max_pu`; `test_stress_links_profile.py`). The config route resolves `connection.envelope` from the
  Library into `links_t["ic_envelope_mw"]` (aligned, written only when covered, axis-hashed; `envelope_coverage`,
  `timezone_required`). `non_firm_dynamic`/`fca`+envelope → `p_max_pu = min(existing, min(envelope, cap)/p_nom)`
  for the solve; `fca` without envelope → capped at the contracted capacity. `fca_stress_entry` zeroes the
  highest-load snapshots worth `curtailment_hours_per_year × horizon years` (stable order, deterministic),
  `frequency_per_year = 1`, `disclosure: fca_synthetic_hours`; the route registers it (replace by id) in the
  project's stress registry. Tests: `test_connection_envelope.py`.

### WP1.5a-0 Spike — new linopy variables inside `extra_functionality`
No precedent exists (`grep add_variables services/ tests/` → 0 hits). Half a day, recorded in
`docs/superpowers/findings/<date>-ic-p1-linopy-spike.md`.

- [ ] On the 15-min fixture: `n.model.add_variables(lower=0, name="ic_peak_import", coords=[pd.Index(months,
  name="month")])`, a per-snapshot `≤` constraint, `n.model.objective += …`; confirm `n.objective` includes the
  term; pypsa 1.1.2 `assign_solution`/`assign_duals` skip the dash-less name with an info log and no error; the
  solution is readable post-solve via `n.model.variables["ic_peak_import"].solution`; `_wrap_with_objective_scale`
  ≠ 1 composes correctly and `_rescale_results_for_objective` does not double-scale the term.
- [ ] Pin the naming convention in the spec §5.1: new variables are **dash-less with an `ic_` prefix**.
- Exit: go/no-go for WP1.5a; if no-go, fall back to modelling `P_peak[m]` as an auxiliary extendable Link per
  month (documented alternative).
- Result (2026-09-27): **GO** — see `docs/superpowers/findings/2026-09-27-ic-p1-linopy-spike.md`. Peak values
  needed after a reload go into `last_commercial_terms`; `ic_*` duals are read from `n.model` post-solve.

### WP1.5a Peak-demand variables
Files: `lp_bindings.py`, `backend/tests/test_lp_bindings_peak_demand.py`.

- [ ] Red: with a `demand` item (€/kW·month), the LP adds `ic_peak_import[m]` per billing month present in the
  snapshots; `p_import[t] ≤ ic_peak_import[m(t)]`; objective includes `Σ rate_m · ic_peak_import[m]` (no
  snapshot weights — per period, not per interval); the fixture's evening peak import is **strictly lower** than
  the no-demand-charge solve by > 1 % ; LP demand cost equals `tariff_engine` demand item on the same dispatch
  (exact for a single-tier demand charge); a month with no snapshots produces no variable and is reported
  `not_established` in the bindings summary; after optimize, **`run_simulation`** (not `lp_bindings`, which
  never touches state) reads `n.model.variables["ic_peak_import"].solution` and publishes it via
  `_emit_state(last_commercial_terms=…)` in the same block that publishes `last_reserve_margin`
  (`solver_service.py` ~L1101–1113), clearing it to `None` on solves without bindings; **gap 0**.
- [ ] Green: implementation.
- As implemented (deviations): the solved peaks are read inside the transient apply's undo (while `n.model` and the
  spec still exist) and committed to `n.meta["ic_demand_peaks"]`/`["ic_demand_info"]` on success — the rows come
  from `n.meta` (spec §5.1 "As implemented"), and `run_simulation` publishes `last_commercial_terms` (with
  `demand_peaks`) only after a successful solve, `None` after a plain one.
- Review round 1 → **FAIL**, fixed: the engine flags `demand_month_not_established:<YYYY-MM>` for months of the
  billing period (else the dispatch span) with no rows — per-item/total withheld; demand is measured on the item's
  `settlement` interval mean (engine and LP) with a `resolution:` note; a NaN outside every window no longer
  unrates the month; the LP span is the represented calendar year when a period's weights sum to a year;
  partial months are charged in full and disclosed (spec §5.2 decision); rows flag `config_changed_since_solve`,
  `demand_charge_not_established`, `demand_months_not_established`, `demand_partial_months`; rolling is refused
  only when it would actually run (not with SCLOPF or multi-period, which fall back to full).
- Round 2 → **FAIL**, fixed: demand-interval keys are computed from UTC instants (`tariff_engine.interval_key`),
  so the autumn fall-back hour no longer raises in the engine or the LP; a year-representing period bills the
  calendar year holding most of the weight and partial-month detection covers edge months (a UTC year read in
  Bogotá/Tokyo); meter history seeds only the first investment period; the committed demand info carries the
  notes (`ratchet_seed_missing` flagged in the rows) and a content hash of the demand items (a rate change after
  the solve is `config_changed_since_solve`). Tracked (LOW): per-key constraint loop cost at year scale (~4 s for
  72 keys + ratchets); the engine notes `demand_on_partial_month` on sampled months of a representative year.

- [ ] (Gate P0 condition 3) with `realistic_dispatch` or any other new `services/solver/` module, add
  the assertion that `test_solver_facade_surface.py`'s glob covers it.
- [ ] Red: `ratchet(lookback_months=11, share=0.9)` adds `ic_peak_import[m] ≥ 0.9·ic_peak_import[k]` for
  modelled k in the window and `≥ 0.9·meter_history_max` for months outside the horizon; missing history ⇒
  `ratchet_seed_missing` flag and no history constraint; billing on the solved dispatch equals LP demand cost;
  **gap 0**.
- [ ] Green: implementation; the running-max carry for windowed dispatch is **P6** (spec §13 amended to say so);
  only the hook `initial_peak_lower_bound: dict[month, MW]` is added here.
- As implemented (**deviation**: the plan's `ic_peak_import[m] ≥ ρ·ic_peak_import[k]` would ratchet on
  ratcheted values and decay as ρ² — real ratchets use prior ACTUAL peaks): a second variable
  `ic_billed_demand[key] ≥ ic_peak_import[key]`, `≥ ρ·ic_peak_import[key']` for each lookback month modelled in
  the same investment period, `≥ ρ·history/1000` for a month before the horizon with
  `CommercialConfig.meter_history_peaks_kw` ("YYYY-MM" → kW); an unknown lookback month adds no constraint and
  the terms carry `ratchet_seed_missing`. The objective and the rows use the billed demand (`billed_mw`). The
  engine bills the same rule (`demand_lines.billed_kw`) with `meter_history` as {"YYYY-MM": kW}; a missing
  seed is a note, the bill is a lower bound, so `total` is withheld (`total_supported` given) and `complete`
  is False. `initial_peak_lower_bound` ({"YYYY-MM": MW}) floors a month's modelled peak (P6 hook).
  Gate P0 condition 3 stays open: no new `services/solver/` module has landed (the commercial layer lives in
  `services/commercial/`). Tests: `test_ratchet.py`.

### WP1.5c Tiers
- [ ] Red: increasing marginal rates ⇒ stacked variables `ic_tier_q[k] ≤ width_k`, cost `Σ rate_k q_k`, LP
  energy cost equals engine on cumulative monthly volume; decreasing rates ⇒ item flagged `nonconvex_tier`,
  LP prices at the tier predicted from meter history (or tier 0 when absent, disclosed), engine bills exactly,
  and the bindings summary lists the flagged item; **gap 0** (tier terms persisted in `last_commercial_terms`).
- [ ] Green: implementation.
- As implemented: tiers apply to cumulative MONTHLY import volume on an energy item with ONE catch-all period
  (tier rates replace the period rate); windowed tiers → `tiers_with_windows`, revenue/net/export tiers and demand
  tiers are refused with a reason. Convex (rising): `ic_tier_q[item|period|month|k]` with `0 ≤ q ≤ width_k` (MWh),
  `Σ_k q = Σ_t w_t·p_import[t]` for the month, objective `Σ w_obj·rate_k·q`; the volumes are committed to
  `n.meta["ic_tier_volumes"]` on success and reported as `energy_tiers` rows (gap 0). Non-convex (falling):
  priced at the first tier as a flat import adder, `notes: nonconvex_tier`, `nonconvex_tier_items` listed (no
  volume history in P1). The engine bills tiers exactly per interval (chronological cumulative volume), so a
  bill line is traceable; tiered items with windowed periods are `unsupported:tiers_with_windows`. A tiered
  item under rolling/myopic is refused (P6). Tests: `test_tiers.py`.

### WP1.6 Energy-hub group contract
Files: `lp_bindings.py`, `backend/tests/test_group_contract.py` (fixture: two members, two PoC Links, one group cap).

- [ ] Red: `Σ_members p_import[t] ≤ group_cap` binds when individual caps sum above it; per-member
  allocation reported as energy shares (cost allocation is P3); undo restores; **gap 0** (a pure constraint
  adds no objective term).
- [ ] Green: constraint over the member Links' `Link-p` variables.
- As implemented: `CommercialConfig.group_members` + `group_cap_mw` (both or neither; unique; members must be
  one-way Links); the transient apply sets the spec, `_wrap_with_commercial_bindings` adds
  `Σ Link-p[members] ≤ cap` per snapshot (`ic_group_cap`), and the commit stores `n.meta["ic_group"]` with each
  member's share of the group's import energy (also in `last_commercial_terms["group"]`). No objective term.
  Tests: `test_group_contract.py`.

### WP1.7 Reload-safe cost-breakdown rows and objective reconciliation (the P1 gate)
Files: `backend/services/results/cost_breakdown.py`, `objective_decomposition.py`,
`backend/tests/test_commercial_objective_reconciliation.py`.

Mechanism (per plan review F1 — the solver's `n._*` stashes are deleted around every solve and never
persisted, so nothing may be read from `n._`):
- `energy_import` / `energy_export` rows are recomputed from persisted `links_t.marginal_cost × links_t.p0 ×
  snapshot_weights("objective")` on the PoC Links;
- `network_capacity` from PoC Link `capital_cost × (p_nom_opt − p_nom)`;
- `demand_charge` and tier terms from the persisted `last_commercial_terms` entry (WP0.5), injected into
  `compute_cost_breakdown(n, cfg, *, commercial_terms=None)` as a keyword by the thin handler — never from `n._`.

- [ ] Red: on all seven cases (energy-only; + capacity fee; + demand charge; + ratchet; + convex tiers; + group
  cap; representative-weeks variant) `cost_breakdown` contains the rows with per-period values and
  `objective_decomposition.gap_pct == 0` within 1e-6 relative **both before and after a bundle save → load**.
- [ ] Green: implementation; `test_results_seam.py` case for `compute_cost_breakdown` with the new keyword;
  `test_results_facade_surface.py` `_HANDLER_PARAMS` **unchanged** (`get_cost_breakdown: []` — a keyword-only
  service argument does not change the route's positional list); the thin handler passes
  `_state["last_commercial_terms"]`; rule (d) (keyword-only, not exposed on the router) holds.
- Acceptance: gap 0 on all seven cases, pre- and post-reload.
- As implemented (**deviation**, consistent with spec §5.1 "As implemented"): no `commercial_terms=` keyword —
  every row is recomputed from records committed on success into the network itself (`links_t["ic_energy_price"]`,
  `n.meta["ic_poc_links" | "ic_connection_fee" | "ic_demand_peaks" | "ic_demand_info" | "ic_tier_volumes" |
  "ic_group"]`), which ride `network.nc`; `_HANDLER_PARAMS` unchanged. `test_commercial_objective_reconciliation.py`
  runs the seven cases through the real project save/load routes: gap < 1e-6 and identical rows and totals before
  and after the reload (the representative-weeks case discloses its unsampled months as not established).

### WP1.8 Preflight validation
Files: `backend/services/validation_service.py`, `backend/tests/test_validation_commercial.py`.

- [ ] Red: errors for a tariff bound with no PoC Link; a `demand` item on an axis coarser than its
  settlement (warn, cause `resolution`); a PPA and an export price on the same asset without
  `ppa_changes_dispatch` (warn: double count); a group contract naming a non-member Link; DR contract on a
  load also in `dsr_buses` (existing double-count rule extended); **`commercial.arbitrage_loop`** warning when
  the export price exceeds the import price in any interval (two PoC Links on one bus pair can otherwise cycle
  energy for profit).
- [ ] Green: checks return structured `Issue`s with codes `commercial.*`.
- As implemented: `services/commercial/preflight.commercial_findings` → `validation_service._check_commercial` (LOPF):
  `commercial.binding_invalid` (error — every refusal the solve would make: missing/two-way PoC, export or group
  Links, a bare Library tariff id, unrated snapshots, missing/stale export price or envelope, an invalid connection
  agreement); `commercial.arbitrage_loop` (warning — snapshots where exporting pays more than importing costs);
  `commercial.demand_resolution` (demand interval finer than the axis); `commercial.demand_partial_months`
  (spec §5.2); `commercial.tariff_out_of_validity`. **Deviation:** the PPA/export-price and DR-contract/`dsr_buses`
  double-count checks need P2's contracts (not in the P1 config) and move to P2 WP2.2. Tests:
  `test_validation_commercial.py`.

### Phase 1 e2e QA gate
- [ ] `backend/tests/qa_commercial_lp.py` (auto-discovered by `run_qa_drivers.py`): build the 15-min fixture
  → set DE capacity+TOU tariff and US demand-charge tariff in turn → solve → assert LP cost == engine rating
  per item (exact where convex), objective gap 0 before and after save/load, evening peak shaved, undo restores,
  bundle keeps the bindings and library refs.
- [ ] Full backend `not slow` suite, QA drivers, frontend vitest all green; findings note
  `docs/superpowers/findings/<date>-ic-p1-commercial-layer.md`; assessor verdict recorded here.

---

## P2–P8 (outline; each gets its own plan file before it starts)

- **P2 Billing pass & contracts** — WP2.1 demand/ratchet/tier rating at 15-min; WP2.2 contract settlement
  (PPA pay-as-produced / baseload / as-consumed BTM / sleeved, CfD, DR availability+activation, lease, EaaS,
  retail); WP2.3 per-item-kind `billing_vs_lp_gap` with cause attribution (`resolution`, `nonconvex_tier`,
  `fixed`, `ratchet_seed`) and warn gate only for unattributed gaps > 5 %; WP2.4 Library CRUD for tariffs,
  contracts, connection agreements + import schemas (URDB-compatible JSON); WP2.5 `compute_billing` /
  `compute_cfe_score` thin results with `test_results_seam.py` cases. Gate: hand-rated bills to the cent;
  REopt URDB fixture parity.
- **P3 Participants & value flows** — participants, assignment, conservation invariant (internal streams sum
  to zero), templates (`single_owner`, `btm_ppa`, `landlord_tenant`, `dso_developer`, `energy_hub` with
  allocation keys), per-participant tables + Sankey in a new Results tab `investment` (add to `Results.tsx`
  tab union + route). Gate: conservation on all templates; FE tests.
- **P4 Finance engine (single owner)** — WP4.1 time axis/capex phasing/escalation/degradation/replacement/
  terminal value; WP4.2a debt sizing + schedules + fees + IDC; WP4.2b DSCR sculpting fixed-point (1e-6, ≤50
  iters, `not_established` on failure; CFADS post-tax pre-financing) + DSRA; WP4.3a tax/depreciation with
  `eu_de`, `us_federal`; WP4.3b `eu_nl`, `ca_federal`; WP4.4 dated incentives (OBBBA tables, FEOC flag);
  WP4.5 NPV/IRR/payback/DSCR/LLCR/PLCR, solve-for-PPA, real/nominal WACC gate; WP4.6 xlsx export; runner
  `services/finance/investment_case_runner.py` + `POST/GET /results/investment_case` (+abort) registered in
  `STUDY_KEYS`/`ABORTABLE_STUDIES`; chat tools with the four guard tests. Gate: **SAM Single Owner** parity
  on 3 committed reference cases.
- **P5 Flex archetypes** — DC load (+ 24/7 CFE), BESS degradation/augmentation/warranty, EV fleet/hub,
  thermal/process flex; forms + chat tools. Gate: build→solve→bill→finance per archetype; EH pipeline still
  passes on archetype networks.
- **P6 Dispatch realism** — `solve_strategy="realistic"` driver (own window loop; commits `horizon−overlap`;
  AR(1) forecast error; settlement rule: PoC absorbs deviation within envelope and is billed, SoC from realised
  balance; `P_peak` running-max carry; reservations), per-period flattening of multi-period designs, seeds,
  haircut chip. Gate: PF ≥ realistic mean on every fixture; reproducible seeds; annual balances match design
  within tolerance.
- **P7 Tax equity & uncertainty** — partnership flip (SAM "Partnership Flip with Debt" oracle; fields per spec
  §6.7), sale-leaseback/inverted lease schema-first; scenario matrix, finance-only path, tornado, P50/P90;
  `InvestmentCaseReport` assembler + chat narration + link to `ReferenceDesignReport`.
- **P8 Scale & hardening** — clustered PyPSA-Eur acceptance, 15-min-year performance budget, security review.

---

## Plan review

- [x] **Round 1 (2026-09-26): `PASS WITH CONDITIONS`.** Conditions and closure:
  F1 reload-safe reconciliation → WP0.5 `last_commercial_terms` + WP1.7 rewritten to persisted-data recompute,
  gap 0 before/after reload; F2 materialisation location → WP1.3 hook after `_reapply_ts`, before
  `_apply_modelling_assumptions`; F3 no `add_variables` precedent → WP1.5a-0 spike + `ic_` dash-less naming;
  F4 library path/ACL → `_library/` reserved prefix via `storage_paths.library_dir`, `taken_names` test, SQLite
  migration tripwire, Postgres manual; F5 billing cache → WP0.5 three keys; F6 typed config →
  `CommercialConfig` on the schema, dict on the dataclass, `types.ts`; F7 file names + legacy tag case; F8
  sample-weeks refusal + pinned audit size + KPI endpoint named; F9 module-scope solved fixture; F10 phantom
  branch wording; splits WP1.1a/b/c and WP1.4a/b; per-WP gap-0 invariant; spike before 1.5a; strict-inequality
  acceptance; FCA stress design line; library chat tools deferred to P2 (stated); `main.py` registration.
- [x] **Round 2 (2026-09-26): `PASS WITH CONDITIONS`** — all round-1 conditions confirmed closed except the FCA
  stress design line, which needed a `links_p_max_pu` extension of the `profiles` schema (now in WP1.4b); three
  minors closed in this text (cost_breakdown in WP1.3 file list; `run_simulation` publishes `ic_peak_import`
  via `_emit_state`; `_HANDLER_PARAMS` unchanged); F4 wording nit closed. Reviewer: no further code re-check
  needed. **Plan passes; implementation may start at P0.**

## Resolved open items

1. Export modelling: a second PoC Link `poc→grid` with `marginal_cost = −price` (keeps import/export on one
   bus pair; caps apply symmetrically); WP1.8 warns on `commercial.arbitrage_loop` when export price > import
   price in any interval.
2. Commercial config lives in `SolverConfig` (persisted with the solve, covered by `assumptions_hash`) with the
   typed `CommercialConfig` boundary on `SolverConfigSchema`; library refs pinned in the bundle sidecar (WP1.1c).
3. Library storage: CSV + gzip, hashed on canonical bytes; parquet/pyarrow is not in the pins and not needed in P1.
