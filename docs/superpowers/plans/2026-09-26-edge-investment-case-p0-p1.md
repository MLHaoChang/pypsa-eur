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
  directory) via `storage_paths.library_dir`; the path **always** carries the org id (`.library/<org_id>/` — a HIDDEN directory: implementation found that `legacy_migrate._scan_root` offers any non-hidden, non-UUID directory under the projects root as a claimable leftover it may move, which `_library/` would have been)
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

### WP1.1c Bundle pins library versions
- [ ] Red: a project bundle records `(id, version, hash)` of every series it references in a sidecar
  `library_refs.json`; reload with a missing/changed item yields a `library_ref_stale` issue rather than a
  silent substitution; `qa_save_load_roundtrip.py` extended.
- [ ] Green: sidecar write/read in `routers/projects.py` bundle paths (`_BUNDLE_FILES`).

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

### WP1.4a Connection agreement — firm, non-firm static, `available_from`
Files: `backend/services/commercial/connection.py`, `backend/tests/test_connection_agreement.py`.

- [ ] Red: `firm` → `p_nom_max = import_cap`, `capital_cost = capacity_fee` on an extendable PoC Link; the
  optimiser sizes the connection strictly below the cap when the fee is high (strict inequality with
  tolerance); `non_firm_static` → `p_max_pu` scalar; `available_from` → PoC vintage with `p_nom_max=0` before
  that period on a two-period fixture; `apply_connection_agreement(n, agreement) -> Applied(undo)` restores every
  mutated attribute (mirror `apply_archetype_pack_detailed().undo()`); **gap 0** (the fee appears as
  `network_capacity` via WP1.7's persisted-data path — implement that row here).
- [ ] Green: implementation.

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

### WP1.5b Ratchet
- [ ] (Gate P0 condition 3) with `realistic_dispatch` or any other new `services/solver/` module, add
  the assertion that `test_solver_facade_surface.py`'s glob covers it.
- [ ] Red: `ratchet(lookback_months=11, share=0.9)` adds `ic_peak_import[m] ≥ 0.9·ic_peak_import[k]` for
  modelled k in the window and `≥ 0.9·meter_history_max` for months outside the horizon; missing history ⇒
  `ratchet_seed_missing` flag and no history constraint; billing on the solved dispatch equals LP demand cost;
  **gap 0**.
- [ ] Green: implementation; the running-max carry for windowed dispatch is **P6** (spec §13 amended to say so);
  only the hook `initial_peak_lower_bound: dict[month, MW]` is added here.

### WP1.5c Tiers
- [ ] Red: increasing marginal rates ⇒ stacked variables `ic_tier_q[k] ≤ width_k`, cost `Σ rate_k q_k`, LP
  energy cost equals engine on cumulative monthly volume; decreasing rates ⇒ item flagged `nonconvex_tier`,
  LP prices at the tier predicted from meter history (or tier 0 when absent, disclosed), engine bills exactly,
  and the bindings summary lists the flagged item; **gap 0** (tier terms persisted in `last_commercial_terms`).
- [ ] Green: implementation.

### WP1.6 Energy-hub group contract
Files: `lp_bindings.py`, `backend/tests/test_group_contract.py` (fixture: two members, two PoC Links, one group cap).

- [ ] Red: `Σ_members p_import[t] ≤ group_cap` binds when individual caps sum above it; per-member
  allocation reported as energy shares (cost allocation is P3); undo restores; **gap 0** (a pure constraint
  adds no objective term).
- [ ] Green: constraint over the member Links' `Link-p` variables.

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

### WP1.8 Preflight validation
Files: `backend/services/validation_service.py`, `backend/tests/test_validation_commercial.py`.

- [ ] Red: errors for a tariff bound with no PoC Link; a `demand` item on an axis coarser than its
  settlement (warn, cause `resolution`); a PPA and an export price on the same asset without
  `ppa_changes_dispatch` (warn: double count); a group contract naming a non-member Link; DR contract on a
  load also in `dsr_buses` (existing double-count rule extended); **`commercial.arbitrage_loop`** warning when
  the export price exceeds the import price in any interval (two PoC Links on one bus pair can otherwise cycle
  energy for profit).
- [ ] Green: checks return structured `Issue`s with codes `commercial.*`.

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
