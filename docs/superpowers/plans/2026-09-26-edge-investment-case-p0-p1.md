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
> **Plan status:** draft v1, awaiting plan review loop (verdict recorded under § Plan review).

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
 ├─ WP1.1 Library series store (+ migration, org access rule)
 ├─ WP1.2 tariff_engine core (energy / TOU / fixed)  ── oracle for everything below
 ├─ WP1.3 PoC price + export binding
 ├─ WP1.4 capacity fee + connection agreement kinds
 ├─ WP1.5a peak vars ─► WP1.5b ratchet ─► WP1.5c tiers
 ├─ WP1.6 group contract
 ├─ WP1.7 cost_breakdown rows + objective gap 0
 └─ WP1.8 preflight validation
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
  {"pf","realistic"}`; (h) `IC_EXPORT_KEYS` is a stable tuple and the skeleton exports all of them as `None`.
- [ ] Green: models with docstring header pointing at the spec (mirror `models/energy_hub.py`); constants
  `IC_REPORT_SECTIONS`, `IC_PIPELINE_STAGES = ("design_solve","valuation_pf","valuation_realistic",
  "billing","participants","finance","uncertainty","assemble")`, `DEFAULT_IC_BUDGET_SOLVES = 30`,
  `MAX_IC_BUDGET_SOLVES = 120`, `empty_ic_section_map()`.
- [ ] Fixtures: `single_owner_participants.json`, `de_tariff_capacity_tou.json`, `us_tariff_demand_charge.json`,
  `fca_connection.json`, `ic_report_skeleton.json`, `ic_export_keys.json`.
- Acceptance: all (a)–(h) green; no imports from `services/`.

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
  (`tests/golden/fixture.py`) — including FOM once `claude/fix-fom-reconciliation` lands (until then the
  test compares `fixed_cost + fom_cost` explicitly and carries a TODO referencing that branch).
- [ ] Green: implementation reads only through `result_df` and `periodized_capital_costs`; no router imports.
- [ ] Add a case in `tests/test_results_facade_surface.py` `_LIFTED` if exposed as a handler (not in P0 —
  service-only).
- Acceptance: agreement test green on golden fixture; used later by finance (P4) and billing (P2).

### WP0.4 `sensitivity` scenario type
Files: `backend/routers/projects.py` (`_SCENARIO_TYPES`), `frontend/src/utils/scenarioType.ts`
(`SCEN_TYPES`, `SCEN_TYPE_LABEL`, `TAG_RE`), `frontend/src/utils/scenarioType.test.ts`,
`backend/tests/test_projects_scenario_type.py` (extend existing).

- [ ] Red: backend accepts `scenario_type="sensitivity"` on create/update and rejects `"foo"` with 400;
  frontend enum/label/regex tests include `sensitivity`.
- [ ] Green: add the value in both places. **No Alembic migration** (plain string column, `db/models.py` L76–80).
- Acceptance: both suites green; `ScenariosPanel.tsx` renders the badge (snapshot/assert in existing test).

### WP0.5 Persistence of the report and billing cache
Files: `backend/services/project_context.py` (`RESULT_STATE_KEYS`, `ProjectSolverState` field),
`backend/services/finance/report.py` (`store_ic_report`, `load_ic_report`, `ic_report_http_payload`),
`backend/tests/test_investment_case_persistence.py`, extend `backend/tests/qa_save_load_roundtrip.py`.

- [ ] Red: storing a skeleton `InvestmentCaseReport` in `solver_state["investment_case_report"]` survives
  save → load of a project bundle (`results_state.pkl`); `ic_report_http_payload` returns `(None, 204)` when
  absent and `(dict, 200)` when present; `_RESULTS_STATE_SCHEMA` bump only if pickled shape changes.
- [ ] Green: add keys; mirror `eh_report.store_eh_report`/`load_eh_report`.
- Acceptance: round-trip QA driver passes; existing `qa_save_load_roundtrip` unchanged otherwise.

### WP0.6 Tripwire tests for the new packages
Files: `backend/tests/test_investment_case_tripwires.py`.

- [ ] Red→Green: regex/AST tests that `services/commercial/**`, `services/finance/**`, `services/library/**`
  never import `routers.*` or `services.solver_service`; each `__init__.py` is docstring-only; every
  `compute_*` in `services/results/` added by this programme is defined in its module (mirror rules (c),(e),(f)
  of `test_results_facade_surface.py`).

### Phase 0 e2e QA gate
- [ ] Full backend `not slow` suite green; frontend vitest green; `qa_save_load_roundtrip.py` green.
- [ ] Findings note `docs/superpowers/findings/<date>-ic-p0-contracts.md`; assessor verdict recorded here.

---

## Phase 1 — Commercial layer, dispatch-grade

### WP1.0 Weightings from frequency + 15-minute fixture
Files: `backend/routers/network_time_axis.py` (`set_snapshots`, `set_multi_period_snapshots`),
`backend/routers/network_profiles.py` (template `freq` defaults), `backend/tests/test_snapshots_freq_weightings.py`,
fixture builder `backend/tests/fixtures/investment_case/edge_15min.py` (3 buses: `grid`, `poc`, `site`;
import Link `grid→poc` with `eh_role="grid_import"`; PV generator; BESS StorageUnit; site Load; 7 days × 96
snapshots).

- [ ] Red: `POST /network/snapshots` with `freq="15min"` and no `weightings` yields
  `snapshot_weightings.objective == generators == stores == 0.25`; `freq="h"` yields 1.0; `freq="30min"` 0.5;
  a profile template generated on a 15-min axis has 96 points/day; `services/adequacy/metrics.py`
  `horizon_years` on the 15-min fixture equals 7/365 within 1e-9 (weights are in hours).
- [ ] Red: audit test that greps `services/` for `8760`/`freq="h"`/`Timedelta(hours=1)` and asserts each
  site is in an allow-list with a reason (documents the hourly assumptions instead of silently keeping them).
- [ ] Green: derive weighting from `pd.tseries.frequencies.to_offset(freq)` hours; keep explicit `weightings`
  override; `_annual_hourly_reference` unchanged but its caller reports `not_supported_for_freq` instead of
  crashing on 15-min axes.
- Acceptance: fixture solves under HiGHS in < 20 s; energy KPIs on the fixture equal Σ p × 0.25.

### WP1.1 Library series store
Files: `backend/services/library/__init__.py`, `series_store.py`, `backend/routers/library.py` (series only in P1;
tariffs/contracts CRUD in P2 WP2.4), Alembic `alembic/versions/0008_library.py`, `backend/db/models.py`
(`LibraryItem(org_id, kind, name, version, hash, path, created_by, created_at)`),
`backend/services/library_acl.py`, `backend/tests/test_library_series_store.py`, `test_library_acl.py`.

- [ ] Red: `put_series(org, name, series, meta)` returns `TimeSeriesRef(id, version, hash)`; re-put with same
  content is idempotent (same version), changed content bumps version; `resolve(ref)` returns the exact
  series; refs are org-scoped — a member of another org gets 403 through the router; super-admin sees all;
  items persist across app restart (DB row + parquet/CSV file under `<projects_root>/<org>/library/`).
- [ ] Green: implementation; migration; access rule `library_acl.can_read/can_write(user, org)` (org
  membership or super-admin) — distinct from `project_acl`.
- [ ] `test_projects_bundle_pins_library_versions`: a project bundle records the `(id, version)` of every
  series it references (sidecar `library_refs.json`) so a reload of the bundle is reproducible.
- Acceptance: tests green; `alembic upgrade head` on SQLite and Postgres compose file both succeed.

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

### WP1.3 PoC price and export binding
Files: `backend/services/commercial/lp_bindings.py` (`_wrap_with_commercial_bindings`, `materialise_poc_prices`),
`backend/services/solver_service.py` (compose after reserve-margin wrapper, before `_wrap_with_objective_scale`,
re-export in the facade block), `backend/models/schemas.py` + `SolverConfig` (new fields
`commercial: dict` — a JSON-serialisable dict, not a Pydantic object, so `asdict`/bundle persistence work),
`backend/tests/test_solver_config_parity.py` (**new**: dataclass fields == `SolverConfigSchema.model_fields`),
`backend/tests/test_lp_bindings_poc_price.py`.

- [ ] Red: with `commercial.import_tariff_item` bound to the PoC Link, the solved import profile on the
  15-min fixture shifts BESS charging into the cheapest TOU window (assert energy in the cheap window ≥ X);
  LP import cost equals `tariff_engine.rate(...)` energy-item total within 1e-6 (same resolution, convex,
  energy-only tariff ⇒ exact); export price makes PV export positive when price > 0 and zero when price = 0;
  parity test between `SolverConfig` and `SolverConfigSchema` fields.
- [ ] Green: materialise `marginal_cost` series on the import Link from the Library series/tariff periods;
  export via grid-sink Load or negative-cost Link (pick one, document); wrapper chaining identical to
  `_wrap_with_capex_budget`; `_safe_log(log_queue, "[COMMERCIAL] ...")`.
- Acceptance: LP-vs-engine equality on the energy-only case; facade tests green.

### WP1.4 Capacity fee + connection agreement kinds
Files: `lp_bindings.py`, `backend/services/commercial/connection.py`, `backend/tests/test_connection_agreement.py`.

- [ ] Red: `firm` → `p_nom_max = import_cap`, `capital_cost = capacity_fee` on an extendable PoC Link, the
  optimiser sizes the connection below the cap when the fee is high; `non_firm_static` → `p_max_pu` scalar;
  `non_firm_dynamic` → time-varying `p_max_pu` from the envelope series (resolved via WP1.1);
  `fca` → envelope plus curtailment-hours stress entry registered with `services/adequacy/stress.py`
  (kind `parametric`, disclosed); `available_from` → PoC vintage with `p_nom_max=0` before that period on a
  two-period fixture; undo restores every mutated attribute (mirror `apply_archetype_pack_detailed().undo()`).
- [ ] Green: implementation with `apply_connection_agreement(n, agreement) -> Applied(undo)`.
- Acceptance: each kind has a solve test; undo round-trip test.

### WP1.5a Peak-demand variables
Files: `lp_bindings.py`, `backend/tests/test_lp_bindings_peak_demand.py`.

- [ ] Red: on the 15-min fixture with a `demand` item (€/kW·month), the LP adds `P_peak[m]` per billing
  month present in the snapshots; `p_import[t] ≤ P_peak[m(t)]`; objective includes `Σ rate_m · P_peak[m]`
  (weights not applied — a demand charge is per period, not per interval); BESS shaves the peak vs the
  no-demand-charge solve; LP demand cost equals `tariff_engine` demand item on the same dispatch (exact for a
  single-tier demand charge); a month with no snapshots produces no variable and is reported
  `not_established` in the bindings summary.
- [ ] Green: variables via `n.model.add_variables(lower=0, name="poc_peak_import", coords=[months])`,
  constraints via `n.model.add_constraints`, objective via `n.model.objective +=`.
- Acceptance: exactness test; representative-weeks fixture test.

### WP1.5b Ratchet
- [ ] Red: `ratchet(lookback_months=11, share=0.9)` adds `P_peak[m] ≥ 0.9·P_peak[k]` for modelled k in
  the window and `P_peak[m] ≥ 0.9·meter_history_max` for months outside the horizon; missing history ⇒
  `ratchet_seed_missing` flag and no history constraint; billing on the solved dispatch equals LP demand cost.
- [ ] Green: implementation; the running-max carry for windowed dispatch is **P6**, only the hook
  (`initial_peak_lower_bound: dict[month, MW]`) is added here.

### WP1.5c Tiers
- [ ] Red: increasing marginal rates ⇒ stacked variables `q_tier_k ≤ width_k`, cost `Σ rate_k q_k`, LP
  energy cost equals engine on cumulative monthly volume; decreasing rates ⇒ item flagged `nonconvex_tier`,
  LP prices at the tier predicted from meter history (or tier 0 when absent, disclosed), engine bills exactly,
  and the bindings summary lists the flagged item.
- [ ] Green: implementation.

### WP1.6 Energy-hub group contract
Files: `lp_bindings.py`, `backend/tests/test_group_contract.py` (fixture: two members, two PoC Links, one group cap).

- [ ] Red: `Σ_members p_import[t] ≤ group_cap` binds when individual caps sum above it; per-member
  allocation reported as energy shares (the cost allocation itself is P3); undo restores.
- [ ] Green: constraint over the member Links' `Link-p` variables.

### WP1.7 Cost-breakdown rows and objective reconciliation (the P1 gate)
Files: `backend/services/results/cost_breakdown.py`, `objective_decomposition.py`,
`backend/tests/test_commercial_objective_reconciliation.py`.

- [ ] Red: after any P1 solve on the fixtures, `cost_breakdown` contains rows `energy_import`,
  `energy_export` (negative), `network_capacity`, `demand_charge` with per-period values, and
  `objective_decomposition.gap_pct == 0` within 1e-6 relative on: energy-only tariff; + capacity fee;
  + demand charge; + ratchet; + convex tiers; + group cap; representative-weeks variant.
- [ ] Green: read the bindings summary the wrapper leaves on the network (`n._commercial_terms`) — the same
  hand-off pattern `cost_breakdown.py` L503–513 already uses for the curtailment term.
- Acceptance: gap 0 on all seven cases.

### WP1.8 Preflight validation
Files: `backend/services/validation_service.py`, `backend/tests/test_validation_commercial.py`.

- [ ] Red: errors for a tariff bound with no PoC Link; a `demand` item on an axis coarser than its
  settlement (warn with the `resolution` cause); a PPA and an export price on the same asset without
  `ppa_changes_dispatch` (warn: double count); a group contract naming a non-member Link; DR contract on a
  load also in `dsr_buses` (existing double-count rule extended).
- [ ] Green: checks return structured `Issue`s with codes `commercial.*`.

### Phase 1 e2e QA gate
- [ ] `backend/tests/qa_commercial_lp.py` (auto-discovered by `run_qa_drivers.py`): build the 15-min fixture
  → set DE capacity+TOU tariff and US demand-charge tariff in turn → solve → assert LP cost == engine rating
  per item (exact where convex), objective gap 0, BESS peak shaving observed, undo restores, bundle save/load
  keeps the bindings and library refs.
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

- [ ] Review round 1: verdict, conditions, closure notes.

## Open items for the plan review to settle

1. Export modelling choice in WP1.3 (grid-sink Load vs negative-cost Link) — recommend the Link (keeps
   import/export on one PoC pair and lets `p_nom` caps apply symmetrically).
2. Whether `commercial` config should live in `SolverConfig` (persisted with the solve) or as a project-level
   sidecar like `dtc_config.json`; recommend `SolverConfig` for provenance (`assumptions_hash` covers it).
3. Library storage format (parquet needs pyarrow; CSV is dependency-free) — recommend CSV + gzip in P1,
   parquet later if size demands.
