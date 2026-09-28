# Guided investment study — MVP-1 plan v2 (the BESS question, end to end)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development or superpowers:executing-plans. Implement phase by phase. Extend `pypsa-gui/backend/services/adequacy/` runners, `services/results/` surfaces and `services/solver/objective.py` over any new parallel stack. Every number this plan adds is a new economic surface and must reconcile with the ones that exist.
>
> **Revision (v2, 2026-09-28):** rewritten after adversarial review v1 (**NO-GO**: 8 blockers, 11 should-fixes, 13 nits; `docs/superpowers/notes/2026-09-28-mvp1-plan-review-v1.md`). Every finding is resolved or explicitly deferred under § Review deltas. v1 is kept as `...-mvp1-v1.md`. **Re-gate (2026-09-28): `GO WITH BINDING CONDITIONS`** (`docs/superpowers/notes/2026-09-28-mvp1-plan-review-v2.md`). The seven binding conditions BC-1 to BC-7 are folded into the phases below and listed under § Review deltas (v2 re-gate); each is checked at the named gate. S1 may start.

**Goal.** A user with a load file and a tariff answers "Do I need a BESS at my site, and what is it worth?" inside the product: a verdict relative to the grid-only baseline, recommended MW and duration, NPV, IRR and payback on a stated basis, a value-stream waterfall, a tornado over the key drivers with the sizes held fixed, and a DOCX report plus an XLSX pro forma whose every figure is bound to the model. No canvas required, and the canvas is one click away.

**Spec.** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md`. This plan amends the spec in four places (recorded in the spec's §13 revision history): decision 14 (MVP-1 quick screen is 8760 h of one year), decision 19 (native PDF moves to MVP-2; MVP-1 ships DOCX and printable HTML), §8.1 (the MVP-1 battery is a StorageUnit with enumerated duration, not a Store + Link pair), and §9 (maturity badge and ledger CSV export pulled into MVP-1; ledger CSV import stays MVP-2).

**Assessment.** `docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md`.

**Already shipped (verified by review v1, do not rebuild).**
- Solve path: `services/solver_service.py::run_simulation`; queue: `services/solve_queue.py::SolveQueue` (`_run_solve_job` hydrates its own `ProjectContext` from `job.storage_dir`, solves off the foreground, and **saves every successful solve** through `_save_context`).
- Wrapper chain in `run_simulation`, inner to outer: `_wrap_with_curtailment_cost` → `_wrap_with_capex_budget` → `_wrap_with_ens_cap` → `_wrap_with_reserve_margin` → `_wrap_with_objective_scale`. The curtailment wrapper (`n.model.objective +=`) is the precedent for an objective term; the capex-budget wrapper is the precedent for a constraint.
- Costs: `services/solver/periodized_costs.py` (`_annuity`, `upfront_cost_series`, `with_periodized_cost_defaults`, `periodized_capital_costs`); `services/results/cost_breakdown.py::compute_cost_breakdown` (its `capex` **already includes FOM**; `fom` is the share inside it); `services/results/asset_economics.py::compute_asset_economics` (StorageUnit rows carry `discharge_revenue_eur`, `charge_cost_eur`, `spread_eur_per_mwh`, `lcos_eur_per_mwh`, `fom_cost_eur`, `vom_cost_eur`; there is **no** `revenue_eur` on storage rows; on a flat network `by_period` is `[]`); `services/results/objective_decomposition.py::_bridge`.
- Import-link selection: `services/adequacy/archetypes.py::select_import_links(n, overlay)` (role → poc bus → carrier).
- Campaign: `services/adequacy/campaign.py` (`start`, `check`, `record`, `end`, `CHARGEABLE`, `estimate_solves`; `_context()` falls back to the process foreground unless run under `contextvars.copy_context()`); mesh: `services/study_state.py`, `services/project_context.py::STUDY_KEYS`, `routers/results.py::_abort_study`.
- Worker-thread study precedent: `services/adequacy/eh_study_runner.py::start_eh_study` (mutates and reverts the active network under `PyPSAService.get_lock()` inside `copy_context()`; does not fork).
- Fork: `routers/projects.py::_create_scenario_db(db, user, base, req)` copies `_BUNDLE_FILES` from disk and needs a DB session and a `User`.
- Access: `routers/deps.py::require_project_access(name, ...)` binds the `{name}` **path** parameter and raises **404**, never 403, and works on a nested router prefix (checked with TestClient). `main.py::_FOREIGN_LOCK_GATE_PREFIXES` and `_SOLVER_BLOCKING_PREFIXES` are matched with `startswith`, so a templated prefix cannot be added; `/api/projects/` is already solver-blocking; exemptions exist as patterns (`_FOREIGN_LOCK_GATE_EXEMPT_PATTERNS`). Routers that touch the projects root mount `fs_permission.require_file_access` as a dependency.
- Preflight: `services/validation_service.py::_check_extendable_bounds` **errors** on a non-extendable asset with `p_nom = 0` and on an extendable asset with `p_nom_max = inf`. `gen_zero_costs` warns on a zero-cost generator.
- Bundles: `routers/projects.py::_BUNDLE_FILES` is a tuple of files (every consumer reads bytes); directories live in `_BUNDLE_DIRS` (today `("uploads",)`) and are copied by `_copy_bundle_dirs`.
- Study mesh: `routers/results.py::_publish_study` and `_refuse_if_mesh_busy` act on the **active** context; `tests/test_adequacy_study_swap_guard.py` derives `ABORTABLE_STUDIES` from the abort routes in `routers/results.py` by regex.
- Project creation off the foreground: `project_registry.create_root` → `ensure_project_dir` → `PyPSAService.build_context()` → `_save_context(ctx, name, project_row=row, storage_dir=dir, persist_user_ts=False, db, user)`; the first save claims through `PyPSAService.hydrate_or_adopt(key)`.
- Foreground saves (`save_project` → `_save_context(..., persist_user_ts=True)`) call `_backup_network_ts_to_user_ts` and `_reapply_user_ts_to_network` against the process-global `_user_ts` (OPEN-ITEMS 1).
- Report contract: `services/adequacy/study_report.py` (`_disclosures`, `_not_established` which filters `status == "no_data"`, `_evidence_gaps`), `services/adequacy/eh_report.py::assemble_reference_design_report`; live frames: `eh_report._live_result_df`.
- `explain_investment` and the zero-profit reading note: `services/chat_tools.py`.
- Frontend: `pages/modelHorizon/StepShell.tsx` (hard-codes `aria-label="Model horizon steps"`), `pages/modelHorizonModel.ts`, `pages/GridspinePanel.tsx` stage list, `pages/results/adequacy.tsx` chips and shared caveats, `layout/NewProjectWizard.tsx` (already has a `'study'` tab for the gridspine study; `ProjectsHomePage.START_ACTIONS` maps cards to tabs).
- Libraries in the pixi env: python-docx 1.2.0 (in `backend/requirements.txt`, **not** in `gui-requirements.txt`), openpyxl 3.1.5, Jinja2 3.1.6 (the only precedent, `routers/io.py`, builds a bare `Environment` with autoescape off), matplotlib 3.11.2 (pixi) / 3.10.9 (`gui-requirements.txt`). No PDF renderer, no SVG rasteriser, no Excel formula evaluator.
- Golden matrix: `tests/golden/{fixture,oracle,coverage}.py`, `tests/test_golden_coverage.py::ROUTE_FILES`, `tests/test_bundle_sidecars.py` (walks `services/adequacy/` only). The golden fixture is multi-period with a `StorageUnit` and no import Link.

**Honest scope (MVP-1).** One question template (BESS at a grid-connected site). Perspective `site_owner`. Basis real, pre-tax, no subsidy, one stated currency year. Tariff with energy bands, one monthly demand charge, fixed charge, export price. Baseline: grid supply with existing assets. Options: best with BESS at each of three enumerated durations (1, 2, 4 h), best with BESS + PV (PV optional at intake), without. Quick screen and full study are both 8760 h of one representative year (spec amendment). Tornado at fixed sizes by re-dispatch. Report to DOCX and printable HTML. Deferred: native PDF, PPTX, share link, AI prose, post-tax and nominal bases, degradation and replacements, scenario sets, ancillary and capacity revenue, resilience value, multi-party, other templates, break-even bisection, option map, ledger CSV import, OpenEI import. The copilot is not required for any deliverable.

**Tech stack.** Unchanged: FastAPI / PyPSA 1.1.2 / linopy 0.8.0; React + TS; `pixi run gui-tests`, `pixi run gui-qa-drivers`.

**Dependency order.**

```
S0 pre-fixes (independent, small; may merge first)
S1 contracts + persistence + routes (under /api/projects/{name}/studies)
 ├─ S2 assumptions library + ledger
 ├─ S3 tariff prices as network data + demand-charge constraint + bill calculator
 └─ S4 question pack + mutation boundary + option runner  (needs S2, S3)
      ├─ S5 pro forma (asset-scoped; second golden fixture) ─┐
      └─ S6 findings (verdict, streams, fixed-size tornado, explain) ─┼─→ S7 report (DOCX/HTML/XLSX)
S8 frontend guided flow (starts after S1 on fixture payloads; wires to S4–S7 as they land)
S9 QA driver + packaging + findings note (last)
```

---

## Phase QA gate + TDD protocol (mandatory)

Every phase follows this loop. **Do not start phase N+1 until phase N's gate is cleared.**

1. **TDD, red:** write failing tests that encode the phase acceptance first and show the red error (for a live-solve test the environment cannot run, show a unit-level red and document the skip).
2. **TDD, green:** the minimum code to pass. No drive-by refactors.
3. **Mutation check** on the property the phase exists for; show that the intended tests, and only they, go red.
4. **Independent assessor gate:** `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO` against acceptance and modelling honesty (basis labelled, null-not-zero, completeness set, no second engine, no user project mutated).
5. **Proceed rule:** `GO` commit; `GO WITH BINDING CONDITIONS` satisfy in the same phase, re-verify, then proceed; `NO-GO` stop and re-gate.

**Global constraints.**
- No existing number changes. Golden tests, `test_fom_reconciliation.py`, `qa_asset_economics.py`, `qa_cost_decomp_overnight.py` and `qa_eh_reference_design.py` pass unchanged at every phase.
- A project with no attached demand charge solves byte-for-byte as before, proven **through `run_simulation`** (S3), not through the golden fixture's direct `n.optimize`.
- **No user project is ever mutated by a study.** A question-first study creates its own base project (S4 M0). Option forks and tornado forks are study-owned and cleaned up.
- Every payload: `status: ok | not_established | skipped` per section, `basis`, `currency_year`, `engine` and `fidelity` on every figure, `honesty_notes` tuple, `null` plus a flag for an unresolvable figure (ADR-0001).
- **Study-initiated saves** never touch `services/user_timeseries.py::_user_ts` (OPEN-ITEMS 1): load and PV series are written to `loads_t.p_set` and `generators_t.p_max_pu` directly, M0 saves with `persist_user_ts=False`, forks are copied on disk, and queue saves of non-foreground contexts pass `persist_user_ts=False`. The Expert view's ordinary foreground save of a fork still goes through `_reapply_user_ts_to_network`, so **in auth (multi-user) mode the study routes refuse unconditionally until OPEN-ITEMS 1 is closed** (BC-6). `PYPSAGUI_DECISION_STUDIES=1` enables them only in local mode and in tests; it is not an operator override. The refusal test covers the flag set in auth mode.
- Vocabulary: the object is a **decision study** (`decision_study` ids, `/studies` under projects, SlidePanel `decision`). `CONTEXT.md` gains the term with the gridspine study and the adequacy studies under "_Avoid_".
- Cite function names in commits and findings, not line numbers. Path-limited commits; one phase per pull request; `pixi run gui-tests` and `pixi run gui-qa-drivers` before each push.

---

## S0. Pre-fixes that mislead an investment reading (small, independent)

**Files.**
- `frontend/src/layout/PropertiesPanel.tsx`: every annuity badge gains `/yr`: Generator, StorageUnit and Link `Capital cost` → `€/MW/yr`; Store `Capital cost` → `€/MWh/yr`; Line and Transformer `Capital cost` → `€/MVA/yr`; the quick-add input label `'Capital cost (€/MW)'` likewise. `Overnight cost` badges stay without `/yr`.
- `frontend/src/layout/CreationForm.tsx`: `$/MWh`, `$/MW` → `€/MWh`, `€/MW`.
- New `backend/services/results/economics_caveats.py`: `ZERO_PROFIT_BY_CONSTRUCTION` as the one string; `services/chat_tools.py` imports it for its reading note (no second copy).
- `backend/services/results/asset_economics.py`: output gains `reading_notes: [ZERO_PROFIT_BY_CONSTRUCTION]` when the network's dispatch is fresh (`dispatch_status_detail`, the predicate `explain_investment` uses; a `p_nom_opt` column is not evidence) and any extendable asset is at an interior optimum, using the classifier lifted out of `chat_tools` into `services/results/sizing.py`.
- `frontend/src/pages/results/Economics.tsx`, `pages/results/asset/AssetDetail.tsx`: render `reading_notes` beside Net profit as one caveat chip.
- `frontend/src/pages/results/MarginLoopPanel.tsx`: the literal `max_solves` placeholder renders the number.

**Acceptance.** Badge and currency assertions in the existing panel tests; a backend test that an interior-optimum golden asset carries the note and a bound-hitting one does not; a MarginLoopPanel test asserts a digit where the placeholder was.

**TDD evidence to record.** Red for the badge (`expected "€/MW/yr"`), red for `reading_notes` (`KeyError`), red for the placeholder (`found "max_solves"`).

- [x] Gate S0 — **GO** (2026-09-28; first pass NO-GO on two blockers, fixed in `b9288f8`; `docs/superpowers/notes/2026-09-28-mvp1-s0-gate.md`, findings `2026-09-28-s0-pre-fixes-investment-reading.md`). Follow-ups outside S0's files, not blocking: `GenerationStack`, `CapacityExpansion` and `propertyDocs` still label capital cost without `/yr`; the quick-add label and edit-mode badges are correct but untested.

## S1. Contracts, persistence, routes

**Files.**
- New `backend/models/study.py`: `DecisionStudy`, `DecisionQuestion`, `LedgerRow` (with `currency_year`), `AssumptionsLedger`, `DemandCharge`, `Tariff`, `OptionSpec`, `OptionResult`, `InvestmentCase`, `Findings`, `DecisionReport`, `StudyMaturity`; enums `Perspective`, `Basis`, `VerdictClass`, `Fidelity`. Import `SectionState`/`SectionStatus` from `models/energy_hub.py`. Field lists per spec §4; pydantic v2.
- New `backend/services/study/__init__.py`, `services/study/store.py`: a study is a sidecar `studies/<study_id>.json` **inside its own base project's storage directory** (S4 M0), written with `services/atomic_io.py`. `studies` goes in **`_BUNDLE_DIRS`**, not `_BUNDLE_FILES` (BC-4; every `_BUNDLE_FILES` consumer reads bytes), so bundles and snapshots carry it through `_copy_bundle_dirs`; `services/study/forks.py` (S4) copies with its own walk that **skips** `studies/` and `results_state.pkl` (it does not reuse `_copy_bundle_dirs` unchanged). The sidecar test for `services/study/` asserts membership in `_BUNDLE_DIRS`.
- New `backend/routers/studies.py`, mounted at `/api/projects/{name}/studies` with `ProjectAccessDep`: `POST /` (create; returns the new base project and study id), `GET /{study_id}`, `PATCH /{study_id}` (per-step apply), `DELETE /{study_id}` (cascades to study-owned forks). `{study_id}` resolves only inside the authorised project's `studies/` directory; an unknown id is 404. Writes call `_check_project_lock(db, _lock_target(project), user)`; run and abort (S4) use `_enforce_project_lock`. Register in `main.py` with `dependencies=_projects_router_guard` (`fs_permission.require_file_access`), like the worksheet and projects routers. **Neither prefix list is touched** (BC-3): both match with `startswith`, and `/api/projects/` is already solver-blocking, which would 409 every study write while the active project solves; instead add a pattern exemption `^/api/projects/[^/]+/studies(/|$)` to the solver-blocking gate, extended the way `_FOREIGN_LOCK_GATE_EXEMPT_PATTERNS` already works, and keep the in-handler lock checks against the **path** project. Add a route-inventory test that every handler in `routers/studies.py` declares `ProjectAccessDep`, and a test that a study `PATCH` succeeds while the active project is solving.
- `pypsa-gui/CONTEXT.md`: the "Decision study" entry.

**Acceptance.** Round-trip test for every model with `null` figures preserved; router tests for create, read, patch, delete; **404** for a non-member and for a foreign study id; 409 for a foreign lock; a study `PATCH` succeeds while the active project is solving; bundle export includes `studies/`; a snapshot restore brings a study directory back; the route-inventory test; the auth-mode refusal test with and without the flag.

**TDD evidence.** `ModuleNotFoundError: models.study` → green; `test_bundle_includes_studies_dir` red on the missing name; the route-inventory test red on a handler without the dependency (add one deliberately, then remove).

- [ ] Gate S1

## S2. Assumptions library and ledger

**Files.**
- New `backend/study_library/technology_costs.csv`: columns `technology, parameter, value, unit, basis, currency_year, source, source_year, source_url, range_low, range_high`. Seed rows: battery inverter EUR/kW, battery storage EUR/kWh, battery FOM share, battery lifetime, round-trip efficiency, utility and rooftop PV EUR/kW, PV FOM, PV lifetime; transcribed from the technology-data release that `rules/retrieve.smk::retrieve_cost_data` pins, each row citing release, year and currency year. Vendored and versioned (`library_version`); the runtime never downloads.
- New `backend/study_library/tariffs.csv`: two seed tariffs (a German-style industrial tariff with an energy price, a monthly capacity charge and a network charge; a flat time-of-use reference), both `source=illustrative`.
- New `backend/study_library/finance_defaults.yaml`: real discount rate, horizon rule (`horizon_years = battery lifetime`), basis, perspective, currency year, with sources.
- New `backend/services/study/library.py`: `load_library(version)`, `seed_ledger(question, intake, library) -> AssumptionsLedger` (every row `provenance=library`, `status=default`, `sensitivity_flag` from `key_drivers`).
- New `backend/services/study/ledger.py`: `apply_user_row`, `diff_against_defaults`, `key_driver_rows`, `maturity_from_ledger` (`screening` while any key driver is `default` or the load is `synthetic`; `feasibility` when all key drivers are `customised` or `measured`; `design` reserved).
- `routers/studies.py`: `GET/PUT .../ledger`, `GET .../ledger.csv` (export only; import is MVP-2).

**Acceptance.** A seeded BESS ledger has a source, a year and a currency year on every key-driver row; a user edit flips `provenance` and `status` and survives a re-seed; maturity moves to `feasibility` when the key rows are customised and the load is uploaded; CSV export round-trips through the reader used by the test (no import route).

**TDD evidence.** Red: `seed_ledger` missing; red: maturity stays `screening` after edits.

- [ ] Gate S2

## S3. Tariff prices as network data, demand-charge constraint, bill calculator

**Design (resolves B1, B2, S3, S4, N11, N13).**
- Energy-band and export prices are **permanent network data**: the S4 pack writes `links_t.marginal_cost` on `grid_import` (positive) and on `grid_export` (negative, the export credit). They survive save and load, every reporting surface sees them, and the Expert view shows real prices. Nothing is reverted around the solve.
- `SolverConfig.demand_charge: dict | None` carries only the demand-charge spec (price per MW per billing period, billing-period rule, import link names). It is a new dataclass field, surviving the queue snapshot (`_solver_config_from_dict`) and the partial PUT (`exclude_unset`).
- `demand_charge_eur` after the solve is **recomputed from `links_t.p0`** by the bill calculator, never read from `n.model`.

**Files.**
- New `backend/services/study/tariff.py`: `write_tariff_prices(n, tariff, import_link, export_link)`; `BillCalculator.bill(import_mw, export_mw, tariff, snapshot_weightings) -> Bill` with `annual_bill`, `by_component` (energy, demand, fixed, network, export_credit), `peak_mw_by_billing_period`, `basis`, `currency_year`, `engine="bill_calculator"`; each component `null` with a flag when its series is absent.
- `backend/services/solver/objective.py`: `_wrap_with_demand_charge(network, user_fn, cfg, log_queue)`: variable `peak_import` (dims: billing period), constraints `peak_import_le` (`Link-p[import, t] ≤ peak_import[p]`), objective term `Σ price × peak_import[p]` added the way `_wrap_with_curtailment_cost` adds its term. Names carry no hyphenated component prefix. **Composed immediately after `_wrap_with_capex_budget` and before `_wrap_with_ens_cap`, `_wrap_with_reserve_margin` and `_wrap_with_objective_scale`.** Refuses with a typed error and a `[TARIFF]` log line when `cfg.solve_strategy` is myopic or rolling, when SCLOPF is on, when `multi_investment_periods` is true, or when the snapshots are not a flat `DatetimeIndex`.
- `backend/services/solver_service.py`: the field and the composition point.
- `backend/services/results/objective_decomposition.py::_bridge`: a named `demand_charge_eur` term (from the bill calculator) so the bridge closes.
- `backend/services/validation_service.py`: preflight warns when the export price exceeds the import price in any hour (import-to-export cycling would pay).

**Acceptance.**
- `test_tariff_demand_charge.py`: on a two-month toy with a StorageUnit, solved through `run_simulation`, the peak import falls with the charge; `peak_import[p]` equals the max import in each billing period within solver tolerance; the objective delta equals `Σ price × peak` plus the energy-cost delta; `user_objective_scale=10` leaves `p_nom_opt` and `demand_charge_eur` unchanged.
- Refusal tests: one per refused mode.
- `test_demand_charge_absent_is_noop.py`: a solve **through `run_simulation`** with `demand_charge=None` produces the same `objective`, `generators_t.p` and `links_t.p0` (frame hashes) as the commit before the phase; the mutation "wrapper always active" turns this test red.
- Bill-calculator unit tests: flat band, two-band time-of-use, monthly demand charge, export credit, absent series → `null` with flag.
- Decomposition test: `residual_gap_pct` within its existing tolerance with the demand-charge term present.

**Mutation.** Drop `peak_import_le`: the "peak equals max import" test goes red and nothing else.

**TDD evidence.** Red: `SolverConfig has no attribute demand_charge`; red on the binding test before the wrapper exists.

- [ ] Gate S3

## S4. Question pack, mutation boundary, option runner

**Battery representation (resolves B3, B4; spec §8.1 amended).** The MVP-1 battery is a **`StorageUnit`** with `p_nom_extendable=True` and an enumerated duration `max_hours ∈ {1, 2, 4}` (one option per duration, spec decision 5). Costs are written as **`overnight_cost`** per MW = `inverter_eur_per_kw × 1000 + max_hours × storage_eur_per_kwh × 1000`, plus `fom_cost`, `lifetime` and `discount_rate` from the ledger; `capital_cost` is left for the periodized fill and asserted derived. The efficiency split is `sqrt(rte)` on both sides. Every existing surface (capacity, economics, LCOS, cycling, compare) already handles a StorageUnit. The Store + Link pair is deferred to MVP-2 with the reviewer's list (ratio constraint, cycling coverage, one carrier convention, golden case).

**Mutation boundary (resolves B8).**
- **M0 (create).** `POST .../studies` on a question template **creates a new base project** named by the user through the chain `project_registry.create_root` → `ensure_project_dir` → `PyPSAService.build_context()` → `_save_context(ctx, name, project_row=row, storage_dir=dir, persist_user_ts=False, db, user)`, with the first-save claim inside `PyPSAService.hydrate_or_adopt(key)` (BC-5). The request's slot is untouched. An existing user project is never overwritten. "Custom question on an existing project" runs no pack and only attaches the study record.
- **M1 (run).** Option forks are named deterministically (`<base>-opt-<option_id>`), created through `services/study/forks.py` (the `_create_scenario_db` logic lifted to a service that takes `db`, `user_id` and re-fetches the user; `scenario_type="scenario"`), replaced on re-run, excluded from copying `studies/` and `results_state.pkl`. Each fork gets an explicit `SolverConfig` (lopf, flat year, no rolling, no SCLOPF, `demand_charge` set, `discount_rate` and `default_lifetime` from the ledger); nothing is inherited from the base's config. Enqueue passes `project_key`, `storage_dir` and `enqueued_by_user_id` from the fork's row.
- **M2 (tornado, S6).** Variants solve on throw-away forks deleted after their NPV is read. Option forks are never re-solved.
- **Context.** The runner takes the base context explicitly and runs under `contextvars.copy_context()`. The mesh check and the claim are **context-parameterised** (BC-5): `running_study_key(base_ctx.solver_state)` and `_solver_in_flight_ctx(base_ctx)`, checked and published under `base_ctx.mutation_lock` → `base_ctx.solver_state_lock`, the same shape as `_publish_study`; the active-context helpers are not used. `campaign.CHARGEABLE` and `estimate_solves` gain `decision_study`; `STUDY_KEYS`, `STUDY_LABELS` and `ABORTABLE_STUDIES` gain `decision_study`, and `test_adequacy_study_swap_guard.py::test_abortable_studies_matches_the_routes_that_actually_exist` is extended to scan `routers/studies.py` for the decision-study abort route. If an agent campaign is already open on that context, record against it rather than starting a second.
- **Results.** Read with a live-frame `result_df` (`eh_report._live_result_df` precedent) and the fork's own config, never `_state["lopf_results"]` or `_state["solver_config"]`.
- **Abort and delete.** Both mark and remove study-owned forks; S9's invariant is "no user project changed; study forks cleaned up".

**Files.**
- New `backend/services/study/questions.py`: `BESS_AT_SITE: DecisionQuestion` (mandatory inputs: site and zone, tariff, load, connection limit; options `none` (the StorageUnit and PV are **omitted** from the fork, never fixed at 0, because `_check_extendable_bounds` errors on a non-extendable asset with `p_nom = 0`; BC-1), `bess_1h`, `bess_2h`, `bess_4h`, `bess_pv_2h` when PV is enabled; headline metrics `npv, payback, sizes`; value streams `demand_charge_reduction, energy_shift, export_credit`; key drivers `battery_storage_eur_per_kwh, battery_inverter_eur_per_kw, demand_charge_price, energy_price_level, discount_rate`).
- New `backend/services/study/packs.py`: `build_site_network(intake, ledger) -> pypsa.Network`: buses `site` and `grid`; a zero-cost Generator at `grid` with `p_min_pu=-1` (supplies import, absorbs export); `grid_import` Link (`eh_role=grid_import`, `p_nom` = connection limit) and `grid_export` Link (`eh_role=grid_export`); the Load from the upload (parsed through `routers/uploads.py` attachments and `services/timeseries_qa.py`) or a **new** sector profile library (`study_library/load_profiles/*.csv`, marked synthetic); optional PV Generator with a synthetic profile marked `synthetic_pv_profile`; the StorageUnit as above with `cyclic_state_of_charge=True` (N3-v2). Every extendable asset gets a **finite `p_nom_max` from a ledger row** (connection limit × a stated multiple, with source and status; BC-2), because preflight errors on `p_nom_max = inf`; when the bound binds, the verdict carries a `size_at_upper_bound` caveat from the existing `_sizing` classification (an NPV at a bound is not an optimum). The zero-cost grid generator is tagged `eh_role=grid_supply` and the `gen_zero_costs` preflight warning is suppressed for that tag (N2-v2). The site bus is **not** tagged `eh_poc`; `select_import_links` must return exactly `['grid_import']`. Snapshots: 8760 hourly steps of one year, `snapshot_weightings` = 1.
- New `backend/services/study/forks.py`, `services/study/runner.py`: as the boundary above; status in the results-state store keyed `decision_study`.
- `routers/studies.py`: `POST .../run {fidelity}` (both fidelities are 8760 h in MVP-1; the field exists for MVP-2), `GET .../run`, `POST .../run/abort`; 409 through `_refuse_if_mesh_busy`.

**Acceptance.**
- Pack test: `upfront_cost_series(n, "StorageUnit")` equals the ledger formula ×1000; the static `capital_cost` column stays `0.0` and the accessor `n.c["StorageUnit"].capital_cost` equals `overnight × annuity(r, L) × nyears` (N1-v2); `validate_for_run(n, cfg)` returns **no errors for every option**, including `none` (BC-1, BC-2); `select_import_links` returns exactly `['grid_import']`; RTE reproduces the ledger; the export price is below the import price in every hour on the seed tariffs.
- M0 test: creating a study leaves every pre-existing project's directory hash unchanged; the new base project holds the pack and the sidecar.
- Runner test with the fake solver from `tests/test_adequacy_abort.py`: five forks, five solves charged to a `decision_study` campaign on the study's context (not the foreground's); `none` has zero storage; abort after the first solve leaves `completeness.options = not_established` naming the rest and removes the unsolved forks.
- Budget test: `budget_solves=2` with five options refuses before the first solve with `CampaignBudgetError` naming the shortfall.
- Re-run test: forks are replaced, not duplicated (no 409).

**TDD evidence.** Red: `BESS_AT_SITE` missing; red: the M0 hash test when the pack is built into the foreground project (write it that way first, deliberately).

- [ ] Gate S4

## S5. The pro forma (a new economic surface, registered in the golden matrix)

**Design (resolves B5 basis, B6, N12, S2, S9).** Asset-scoped, upfront-only, one basis.
- **CAPEX** lands in year 0 from `upfront_cost_series × p_nom_opt` for the option's own assets. No annuity term appears anywhere in the cash flow.
- **Fixed O&M** per year = Σ over the option's assets of `asset_economics.fom_cost_eur` on the annual basis; the reconciliation target is **`Σ asset_economics.fom_cost_eur` over the option's assets** (per-asset; `cost_breakdown.fom` has no per-asset view), cross-checked against `by_component["StorageUnit"].fom` plus the PV carrier row in `by_carrier` (BC-7).
- **Variable OPEX** = the option's own assets' `vom_cost_eur` only, never system `cost_breakdown.opex` (which contains the grid energy bill).
- **Savings** = `bill_baseline − bill_option` from the bill calculator, both on the same tariff.
- **Market revenue at duals** is reported, labelled `engine=lp_duals`, and **excluded** from `net_cash_flow` (the bill already prices the same energy). For the battery it is `discharge_revenue_eur − charge_cost_eur` (StorageUnit rows have no `revenue_eur`); for PV it is `revenue_eur` (BC-7).
- **Horizon** = the battery's ledger lifetime. PV outlives it, so its salvage is valued as the **present value of the remaining annuities** the LP implicitly charged (one basis with the LP), reported in `years[-1]` and `kpis.salvage_eur` with `salvage_basis=annuity_pv`; the `npv_nonnegative_at_optimum_by_construction` note is exact for battery-only options and stated as approximate for `bess_pv` (BC-7).
- **Rates.** The ledger's real discount rate and lifetimes are written into the LP (S4 M1), so LP and pro forma share one basis.
- **KPIs.** `npv`; `irr` by bisection (`null` with `irr_undefined` when no sign change); simple and discounted payback by first crossing; `lcos` on discounted energy; `capex_total`; `salvage_eur`.
- Flat-network path: `active_period_years` returns `None` on a flat network and `asset_economics` emits no `by_period`; the expander reads annual values and multiplies by `horizon_years`.
- **Honesty notes:** `basis_real_pre_tax_no_subsidy`, `currency_year_<y>`, `single_year_extrapolated`, `perfect_foresight_dispatch`, `demand_charge_perfect_foresight`, `no_degradation`, `no_replacement_within_horizon`, `synthetic_pv_profile` / `synthetic_load_profile` when applicable, `duals_include_demand_charge`, `npv_nonnegative_at_optimum_by_construction`, `market_revenue_at_duals_excluded_from_cash_flow`.

**Files.**
- New `backend/services/study/proforma.py::build_investment_case(n_option, cfg_option, n_baseline, ledger, bills, option) -> InvestmentCase`.
- New `backend/tests/golden/site_fixture.py`: a **second golden fixture in the production shape** (flat year, `grid`/`site` buses, `grid_import`/`grid_export` Links with a flat tariff, a StorageUnit, an optional PV) with `tests/golden/oracle.py` gaining `npv`, `irr`, `payback`; `tests/golden/coverage.py::SURFACES` gains `investment_case` and `investment_case_xlsx`; `tests/test_golden_coverage.py::ROUTE_FILES` gains `routers/studies.py`; `tests/test_bundle_sidecars.py` walks `services/study/`.
- New `backend/tests/test_proforma_golden.py`: on the site fixture, `Σ years.capex` equals the oracle upfront total; `Σ years.opex_fixed` equals `Σ asset_economics.fom_cost_eur × horizon` over the option's assets and matches `by_component["StorageUnit"].fom` plus the PV carrier row; NPV equals the oracle's NPV of the same vector; `market_revenue_at_duals` is absent from `net_cash_flow`.
- New `backend/services/study/proforma_xlsx.py::write_proforma_xlsx(case, ledger) -> bytes`: sheet `Cash flows` (year rows; formulas for discount factor, discounted and cumulative cash flow; NPV pinned as `=CF0 + NPV(rate, CF1:CFn)`), `KPIs`, `Assumptions` (the ledger), `Provenance` (engines, basis, currency year, fidelity, library version, model and ledger hashes). A small in-repo evaluator implements Excel's `NPV` semantics with a test against Excel's documented example.
- `routers/studies.py`: `GET .../options/{option_id}/case`, `.../case.xlsx` (filenames through `services/http_filenames.py`).

**Mutation.** Add the annuity to `opex_fixed`: the golden reconciliation goes red. Multiply CAPEX by the annuity: red.

**TDD evidence.** Red: `test_proforma_golden` on `ImportError`; red: NPV mismatch before discounting is wired; red: the XLSX NPV cell when the formula range starts at CF0.

- [ ] Gate S5

## S6. Findings: verdict, value streams, fixed-size tornado, explain

**Design (resolves B5).** Size once, then evaluate. The tornado **holds the recommended sizes fixed** (`p_nom_extendable=False`, `p_nom = p_nom_opt`) and re-dispatches at each bound on throw-away forks (S4 M2), `method=redispatch_fixed_sizes`. NPV can then go negative, so `marginal` is reachable. The verdict page shows `npv_nonnegative_at_optimum_by_construction` beside the NPV.

**Files.**
- New `backend/services/study/findings.py`:
  - `value_streams(bills)`: demand-charge reduction, energy time-shift and export credit from `Bill.by_component` deltas; the sum equals `savings` (test).
  - `verdict(cases, tornado)`: `recommended` when the best option's NPV > 0 at every tornado bound; `marginal` when the sign flips inside the bounds; `not_recommended` when NPV ≤ 0 at the centre. One sentence from a fixed template with fact IDs. Main caveat = `demand_charge_perfect_foresight` whenever demand-charge reduction is the top stream.
  - `tornado(study, rows)`: for each `sensitivity_flag` row, two re-dispatches at `range_low` and `range_high` (the energy-price row scales the time-of-use bands around their time-weighted mean; the demand-charge row scales the price; cost rows change CAPEX only, no re-dispatch needed), charged to the campaign after a budget check; sorted by swing. Price and demand-charge perturbations **recompute `bill_baseline`** with the bill calculator at the perturbed tariff (no solve: the baseline has no flexible asset), and an option whose `p_nom_opt ≤ ε` is **skipped** (its NPV is 0 at every bound) rather than re-dispatched (BC-1, BC-7).
  - `explain(option)`: built on `services/results/sizing.py::classify_sizing` (the one classifier S0 already lifted out of `chat_tools`; do not add a second in `services/study/`), plus the bus-price and CO2 signals `explain_investment` assembles, lifted from `chat_tools` into `services/study/explain.py` so the UI does not need the copilot; `chat_tools.explain_investment` calls it (one implementation, output pinned byte-identical by a recorded fixture).
  - `assemble_findings(study) -> Findings` with completeness and honesty notes.
- `routers/studies.py`: `POST .../findings/tornado`, `GET .../findings`.

**Acceptance.** Streams sum to savings on the toy; verdict classes on three constructed cases; a constructed case where the sign flips at the high storage-cost bound yields `marginal` (this is the test that could never pass under v1); tornado on two dispatch-sensitive rows charges four solves on throw-away forks that are gone afterwards, and the option forks' network hashes are unchanged; abort mid-tornado keeps computed bars and sets `tornado.status=not_established` naming the rest; `chat_tools.explain_investment` output identical before and after the lift.

**TDD evidence.** Red: streams do not sum before the export-credit sign is fixed; red: `marginal` never emitted with re-solves (write the re-solve version first, show red, then switch to re-dispatch).

- [ ] Gate S6

## S7. The report: DOCX, HTML, XLSX

**Design (resolves S6, N4, N5, N7).**
- `assemble_decision_report(study) -> DecisionReport`: sections per spec §7; `facts` map (fact_id → value, unit, basis, currency year, engine, fidelity); `prose` = template paragraphs with `{{fact_id}}` references only; no AI paragraphs in MVP-1 (field present, empty). The disclosure helpers in `study_report.py` are lifted to accept a generic section list with a mapping (`not_established` filters `status in {"no_data", "not_established"}`), and `build_study_report` keeps its output byte-identical (pinned). The BESS report gets its own disclosure set; the adequacy opening sentence is not reused.
- Renderer guard `validate_prose(report)`: rejects numeric tokens (`(?<![A-Za-z])\d`) outside a `{{fact_id}}` and any unresolved reference, with a unit allowlist (`CO2`, `H2`, `N-1`, `24/7`); names section and paragraph on failure.
- Stale flag: `report.stale=true` when the ledger hash or any **option** fork's network hash differs from the ones recorded at findings time (tornado forks are excluded, they no longer exist).
- HTML: Jinja2 `Environment(autoescape=True)`, template `backend/templates/decision_report.html.j2`, printable stylesheet, charts as PNG rendered server-side from matplotlib `Agg` figures built from the same data the frontend charts use (no SVG rasterisation). Served with `Content-Disposition` from `services/http_filenames.py::content_disposition` and `Content-Security-Policy: sandbox`.
- DOCX: python-docx with headings, tables, the same PNGs, the Assumptions table (edited rows marked) and a Provenance appendix.

**Files.** New `backend/services/study/report.py`, `render_html.py`, `render_docx.py`, `backend/templates/decision_report.html.j2`; `routers/studies.py`: `POST .../report`, `GET .../report`, `.../report.html`, `.../report.docx`, `.../report.xlsx`.

**Acceptance.** A report from the S4 toy renders to HTML and DOCX with every verdict-page KPI equal to the findings payload; `validate_prose` rejects a hand-typed "4.1 M" and accepts "CO2" and "24/7"; disclosures render before the first number; a ledger edit after assembly sets `stale`; a study named `<script>` renders escaped in HTML; the DOCX opens with python-docx and contains the Assumptions table; `build_study_report` output unchanged (fixture).

**Mutation.** Remove the digit check: the hand-typed-number test goes red. Set `autoescape=False`: the XSS test goes red.

**TDD evidence.** Red on `validate_prose` accepting a bare number; red on `stale` never set; red on the unescaped study name.

- [ ] Gate S7

## S8. Frontend: the guided flow

**Design (resolves S10, N1, N8).** A new **`decision`** SlidePanel and a **new** wizard tab, `decision` (question cards), reachable from a new `ProjectsHomePage` card. The existing `'study'` tab (gridspine) and the `'blank'` default are untouched, so `NewProjectWizard.study.test.tsx`, `ProjectsHomePage.kind.test.tsx` and the workbench default stay green.

**Files.**
- `frontend/src/api/decisionStudies.ts`: client and types mirroring `models/study.py`.
- `frontend/src/pages/modelHorizon/StepShell.tsx`: generic step id type parameter with `labels` and a `navLabel` prop defaulting to `"Model horizon steps"`; Model Horizon passes `STEP_LABELS`; `ModelHorizon.render.test.tsx` passes unchanged.
- `frontend/src/layout/NewProjectWizard.tsx`: the `decision` tab with question cards (BESS question; custom question; the other tabs unchanged). `ProjectsHomePage.tsx`: one added card mapping to the new tab.
- New `frontend/src/pages/decision/`: `DecisionHub.tsx` (task list with section status chips, maturity badge; entry state from the study record: intake incomplete opens the intake, else the hub), `Intake.tsx` (StepShell: site and zone → what exists → consumption upload or sector profile → goal → horizon and perspective (read-only in MVP-1, shown with its source) → check your answers; per-step PATCH), `Options.tsx` (durations, PV toggle, cost presets with source year), `TariffStep.tsx` (with the baseline bill preview from the bill calculator on the load alone), `FinanceStep.tsx` (rate, horizon rule, basis shown as fixed labels in MVP-1), `LedgerReview.tsx` (key rows first, defaults visually distinct, CSV export), `RunStep.tsx` (budget sentence, stage list, abort), `Verdict.tsx` (chip, sentence, three KPIs vs baseline with basis and currency year, top drivers, maturity, main caveat, the NPV-by-construction note), `WhyHow.tsx` (waterfall, cumulative cash flow with payback marker, option table across durations and PV, typical week from the existing Dispatch chart component), `Robust.tsx` (tornado; run button charged), `ReportStep.tsx` (assemble, stale banner, download DOCX/HTML/XLSX). `decisionModel.ts`: pure predicates (section status, entry state, maturity label, verdict tone), unit-tested without rendering.
- `frontend/src/App.tsx`: `decision` in `FULL_SCREEN_TABS` and `PANEL_META`; the Expert view button opens the chosen option fork in the workbench.
- `frontend/src/layout/AppHeader.tsx`: when a decision-study run completes on the active base project, open the Verdict and toast the verdict class; queue toasts for study-owned jobs are suppressed (S11).
- `frontend/src/utils/decisionVocabulary.ts`: the spec §8.1 map (amended for StorageUnit: "Battery power (MW)" → `p_nom`, "Hours of storage (h)" → `max_hours`), the only source of novice labels.

**Acceptance (render tests, mandatory).** Intake opens on an incomplete study and the hub on a complete one; a ledger row edited in the UI shows `customised`; the verdict page renders three KPIs from a fixture payload with basis labels and renders "not established" (never `0`) for a null KPI; tornado bars sort by swing; the report step shows the stale banner; the Expert view link targets the option fork; Model Horizon, wizard and home tests unchanged.

**TDD evidence.** Red: `StepShell` rejects a non-horizon id; red: the verdict page renders `0` for a null KPI.

- [ ] Gate S8

## S9. QA driver, packaging, integration, findings note

**Files.**
- New `backend/tests/qa_decision_study.py` (discovered by `tests/run_qa_drivers.py`): over HTTP, create a study from a load CSV and the seed tariff (M0 creates the base project), seed the ledger, edit two rows, run, assert five options solved and charged on the study's context, the best option's case reconciles to `cost_breakdown.fom` and `upfront_cost_series`, findings carry a verdict, the tornado runs on two rows and leaves no throw-away forks, the report renders to DOCX and XLSX, `stale` flips after a ledger edit, abort mid-run leaves every pre-existing project's hash unchanged and removes unsolved forks, and delete cascades.
- Packaging (S5 of the review): `gui-requirements.txt` pins `python-docx==1.2.0`; `pypsa-gui.spec` `datas` gains `templates/decision_report.html.j2` and `study_library/`; `smoke/check_bundle.py::EXPECTED` lists them; `tests/test_packaging_requirements.py` passes.
- `docs/superpowers/findings/2026-09-28-guided-investment-study-mvp1.md`: status line, what changed, before/after counts (`gui-tests`, `gui-qa-drivers`), the golden reconciliation numbers, "Still open / deliberately not done".

**Acceptance.** Driver passes on the pinned PyPSA 1.1.2 and on the container's; failing set of the full suite unchanged versus master; `qa_asset_economics.py`, `qa_cost_decomp_overnight.py`, `qa_eh_reference_design.py` unchanged.

- [ ] Gate S9 (plan-level definition of done)

---

## Review deltas (v1 → v2)

| Finding | Resolution in v2 |
|---|---|
| [B1] quick screen via `time_aggregation_service`; demand charge under rolling/myopic | Both fidelities are 8760 h of one year (spec decision 14 amended); `_wrap_with_demand_charge` refuses myopic, rolling, SCLOPF, multi-period and non-flat snapshots, with tests (S3) |
| [B2] tariff reverted around the solve | Energy and export prices are permanent network data; only the demand charge is a config field; `demand_charge_eur` recomputed from `links_t.p0`; named term in `objective_decomposition._bridge` (S3) |
| [B3] upfront cost written to `capital_cost` | Pack writes `overnight_cost`, `lifetime`, `discount_rate`, `fom_cost`; `capital_cost` asserted derived (S4) |
| [B4] Store + Link pair underspecified | MVP-1 battery is a `StorageUnit` with enumerated durations 1/2/4 h (spec §8.1 amended); Store + Link deferred to MVP-2 with the reviewer's list (S4) |
| [B5] `marginal` unreachable with re-solves | Tornado re-dispatches at fixed sizes on throw-away forks; ledger rate and lifetimes written into the LP; horizon = battery lifetime; `npv_nonnegative_at_optimum_by_construction` note on the verdict page (S5, S6) |
| [B6] golden equation double-counts FOM and investment; wrong fixture shape | Asset-scoped OPEX, upfront-only CAPEX, no annuity in the cash flow; second golden fixture in the production shape; market revenue at duals excluded from cash flow (S5) |
| [B7] IDOR under `/api/studies/{id}`; 403; sidecar copied into forks | Routes under `/api/projects/{name}/studies/{study_id}` with `ProjectAccessDep`; 404; sidecar `studies/<id>.json` excluded from forks; prefixes added; route-inventory test (S1) |
| [B8] no mutation boundary; wrong context and frames | M0/M1/M2 boundary; explicit context under `copy_context()`; `decision_study` in `CHARGEABLE`, `STUDY_KEYS`, `ABORTABLE_STUDIES`; live-frame results with the fork's config; forks lifecycle (S4) |
| [S1] spec deviations | Four spec amendments recorded in the spec's §13 (decision 14, decision 19, §8.1, §9) |
| [S2] missing honesty notes; horizon rule | Notes listed in S5; horizon = battery lifetime; PV salvage reported |
| [S3] wrapper placement; naming; scale | Composed after capex budget, before ENS cap; no hyphenated names; scale test (S3) |
| [S4] no-op test vacuous | Through `run_simulation` with a mutation that turns it red (S3) |
| [S5] packaging | `python-docx` pinned in `gui-requirements.txt`; template and library in `pypsa-gui.spec` and `check_bundle.EXPECTED` (S9) |
| [S6] stored XSS | `autoescape=True`, CSP sandbox, content disposition, XSS test (S7) |
| [S7] user-timeseries tenancy | Study never touches `_user_ts`; save path pinned; auth-mode feature flag until OPEN-ITEMS 1 closes (global constraints, S4) |
| [S8] golden-matrix registration | `SURFACES`, `ROUTE_FILES`, sidecar walk extended (S5) |
| [S9] Excel NPV semantics | `=CF0 + NPV(rate, CF1:CFn)`; in-repo evaluator tested against Excel's documented example (S5) |
| [S10] "study" collides | `decision_study` ids, `decision` panel and tab, `CONTEXT.md` entry; existing `'study'` tab untouched (S1, S8) |
| [S11] fork lifecycle | Deterministic names, replace on re-run, cascade on delete, study-owned in lists, queue toasts suppressed (S4, S8) |
| [N1] `modelHorizonModel.ts` path | Corrected (`pages/modelHorizonModel.ts`) |
| [N2] fake solver reference | `tests/test_adequacy_abort.py` (S4) |
| [N3] S0 badges | Store `€/MWh/yr`, Line and Transformer `€/MVA/yr`, quick-add label (S0) |
| [N4] digit rule | Numeric-token regex with unit allowlist (S7) |
| [N5] SVG to PNG | PNG straight from matplotlib `Agg` (S7) |
| [N6] tornado inputs | Five drivers named; energy-price perturbation scales bands around the time-weighted mean; cost rows need no re-dispatch (S4, S6) |
| [N7] report helpers | Generic lift with a status mapping and a BESS-specific disclosure set; `build_study_report` pinned (S7) |
| [N8] `aria-label` | `navLabel` prop with the Model Horizon default (S8) |
| [N9] "tenth surface" | Wording replaced by "a new surface registered in the golden matrix" |
| [N10] currency year | `currency_year` on every ledger row and figure (S1, S2) |
| [N11] `eh_poc` fallback | Site bus not tagged; exact `['grid_import']` asserted (S4) |
| [N12] flat-network path | Explicit annual × horizon path in the expander (S5) |
| [N13] grid bus supply; export cycling | Zero-cost Generator with `p_min_pu=-1`; preflight warns when export price exceeds import price (S3, S4) |

## Review deltas (v2 re-gate, binding conditions)

| Condition | Where applied | Checked at gate |
|---|---|---|
| BC-1: `none` omits the assets instead of fixing them at 0; the tornado skips zero-size options; `validate_for_run` test | S4 options, S4 acceptance, S6 tornado | S4, S6 |
| BC-2: `p_nom_max` from a ledger row; `size_at_upper_bound` caveat; no-preflight-error test for every option | S4 pack, S4 acceptance | S4 |
| BC-3: study routes in neither prefix list; pattern exemption on the solver-blocking gate; `require_file_access` router dependency; PATCH-while-solving test | S1 routes, S1 acceptance | S1 |
| BC-4: `studies` in `_BUNDLE_DIRS`; forks skip it; sidecar test adapted; snapshot restore test | S1 store, S1 acceptance | S1 |
| BC-5: M0 chain named with `persist_user_ts=False` and `hydrate_or_adopt`; context-parameterised mesh check and claim; `ABORTABLE_STUDIES` route test extended | S4 mutation boundary | S4 |
| BC-6: unconditional auth-mode refusal until OPEN-ITEMS 1 closes; constraint wording narrowed to study-initiated saves | Global constraints, S1 acceptance | S1 |
| BC-7: reconcile to `Σ asset_economics.fom_cost_eur` per asset; storage market revenue = `discharge_revenue_eur − charge_cost_eur`; PV salvage on the annuity basis; tornado recomputes the baseline bill | S5 design and acceptance, S6 tornado | S5, S6 |
| N1-v2, N2-v2, N3-v2 | S4 pack and acceptance | S4 |

## Deferred (explicitly, to MVP-2/3)

Native PDF (no renderer in the environment; HTML print is the MVP-1 path), PPTX, share link, AI-drafted paragraphs with labels and review, post-tax and nominal bases, replacements and degradation rows, Store + Link battery with the ratio constraint and cycling coverage, price and weather scenario sets and the dotplot, ancillary and capacity revenue, resilience value from the sequential MC, multi-party perspective, the data-centre, hydrogen, co-located and off-grid templates, break-even bisection and the option map, representative-week quick screen (needs a billing-period-aware aggregation), scenario input diffs, ledger CSV import and the OpenEI import, the container-tenancy fix for `services/user_timeseries.py` (its own plan; required before the auth-mode flag is removed).

## Definition of done

MVP-1 is done when a QA driver takes a load file and a seed tariff to a DOCX report and an XLSX pro forma over HTTP with no canvas interaction, no pre-existing project's directory hash changes at any point, the pro forma reconciles to the site golden oracle, a project without a demand charge solves byte-identically through `run_simulation`, the existing economic surfaces and their tests are unchanged, `marginal` is demonstrably reachable, every phase gate reads `GO`, and the findings note records the counts.
