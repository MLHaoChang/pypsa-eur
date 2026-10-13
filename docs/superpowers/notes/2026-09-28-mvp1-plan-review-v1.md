<!-- Independent adversarial review of plan v1, produced 2026-09-28 by a delegated reviewer agent. Every finding was checked in source or the installed packages; the owner session reproduced B1, B4, B6, B7, B8, S3, S5, S6 and N1 before applying them in plan v2. -->

# Adversarial review: Guided investment study, MVP-1 plan

**Plan:** `docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1.md`
**Spec:** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md`
**Reviewed against:** the working tree on 2026-09-28 (PyPSA 1.1.2, linopy 0.8.0, pinned in `pixi.toml` and installed). Read-only review. Every finding below was checked in source, in the installed package, or with a one-line interpreter check. None is asserted from memory.

## Verdict: **NO-GO** for S1 to S9 as written. S0 is independent and may proceed (apply N3).

There are 8 blockers. Most need only a paragraph of plan text. Two need a design decision before any code is written: B5 (the verdict cannot reach `marginal`) and B8 (the mutation boundary and the context the runner uses). Revise, record the deltas under § Review deltas, then re-gate.

---

## Blockers

### [B1] `quick_screen` via `time_aggregation_service` does not exist for this network shape, and it breaks the demand charge

**Checked.**
- `services/time_aggregation_service.py::aggregate_period_snapshots(n, period, cfg)` is the limited-foresight helper for myopic runs. Its only caller is `services/solver/myopic.py`, and it only aggregates *future* periods. It indexes `n.snapshots.get_level_values(0) == period`, so it needs a multi-period MultiIndex. The plan's site network is one flat representative year, which this function cannot aggregate.
- `tsam` is deliberately not pinned in `gui-requirements.txt` (see the header note and `OPTIONAL_AT_RUNTIME` in `tests/test_packaging_requirements.py`). The desktop app therefore falls back *silently* to the full period.
- `_wrap_with_demand_charge` needs every snapshot mapped to a billing period. A representative week stands in for weeks from several months, so a monthly peak cannot be recovered from clustered weeks.
- **Rolling-horizon** (`optimize_with_rolling_horizon`) calls `extra_functionality` once per window. Each window would create its own `peak_import[p]`, so a month's demand charge is paid once per window.
- **Myopic** runs (`_run_myopic_foresight`) and **SCLOPF** also receive the same `extra_fn`.

**Why it matters.** The one fidelity the plan offers for the quick screen and for every tornado solve either fails, silently becomes a full solve on desktop, or produces a demand charge that means nothing.

**Change.**
- MVP-1 `quick_screen` is **8760 h of one year**. The site LP is a handful of components, so 8760 h is small. Record this as a spec §2 decision-14 amendment.
- `_wrap_with_demand_charge` **refuses** (typed error, and a `[TARIFF]` log line) when `cfg.mode` is myopic, rolling or SCLOPF, or when `multi_investment_periods` is true, or when the snapshots are not a flat `DatetimeIndex`.
- Add a test for each refusal.

### [B2] Tariff energy prices are written and then reverted around the solve, so every reporting surface loses the grid energy cost

**Checked.**
- S3 says `apply_tariff(...) -> revert` runs "inside the same revert discipline as periodized defaults".
- Periodized defaults can be reverted because every report re-enters `with_periodized_cost_defaults`. `compute_cost_breakdown` imports it for that reason. Nothing would re-apply a tariff at report time.
- After the revert, `n.statistics()` OPEX (and so `cost_breakdown.total`) has no import energy cost.
- `objective_decomposition::_bridge` would then put the whole energy bill *and* the demand charge into `residual_gap_eur`. S3's own acceptance ("`residual_gap_pct` stays within tolerance") cannot pass.
- The option table's `system_cost` (spec §4.6) would also leave out the demand charge. A BESS whose value is mostly peak shaving would then look *more* expensive than the baseline, which contradicts its own verdict.
- The option project opened in the Expert view would show grid imports at €0.

**Change.**
- The S4 pack writes energy-band and export prices **permanently** into the network (`links_t.marginal_cost` on `grid_import` and `grid_export`). They are then ordinary, visible network data, and a project with no tariff is still unchanged.
- `SolverConfig.tariff` carries only the demand-charge spec and the import link names.
- `demand_charge_eur` is recomputed *after the solve from `links_t.p0`* by `BillCalculator`. It is not read from `n.model`, which does not survive the netCDF round trip.
- `demand_charge_eur` is added as a named term in `objective_decomposition::_bridge` and to `OptionResult.system_cost`.

### [B3] The pack writes upfront €/kW and €/kWh into `capital_cost`, which is the annuity field

**Checked.**
- S4 says the Store takes "`capital_cost` from EUR/kWh" and the Links take "`capital_cost` from EUR/kW".
- The fixed cost the LP charges is `capital_cost` (annualised) + `fom_cost`. See `findings/2026-09-27-fom-missing-from-fixed-cost.md` and `periodized_costs.periodized_capital_costs`.
- The spec's §8.1 vocabulary map says "Upfront cost → `overnight_cost`. The guided flow never asks for an annuity". §8.2 even adds a preflight to catch this exact mistake.

**Why it matters.** As written, storage is overpriced about 8 to 12 times. The LP builds nothing, and the verdict is `not_recommended` for every study, without any warning. The S4 acceptance ("every cost on the built network equals its ledger row") would pin that mistake in place.

**Change.**
- The pack writes `overnight_cost` (converting kW to MW ×1000 and kWh to MWh ×1000), `lifetime`, `discount_rate` and `fom_cost`, and leaves `capital_cost` for the periodized fill.
- Acceptance becomes "`upfront_cost_series(n, 'Store')` equals the ledger row ×1000", and `capital_cost` is asserted to be derived.

### [B4] The Store + Link BESS pair is underspecified, and the existing surfaces show it inconsistently or not at all

**Checked.**
- **Power coupling.** S4 makes the charge and discharge Links separately `p_nom_extendable`, each with a `capital_cost`. That either counts the inverter CAPEX twice, or, if one Link is free, gives the LP unlimited power on the free side. Nothing in the backend ties the two (grep for `charger` or `discharger` in `services/`, `routers/` and `models/` finds nothing). PyPSA-Eur ties them with a ratio constraint in `extra_functionality`. A single Link with `p_min_pu=-1` and efficiency < 1 would create energy on reverse flow.
- **`services/compare/storage_cycling.py::_compute_storage_cycling_summary`** reads `n.storage_units` only. When that table is empty it returns `StorageCyclingComparison(available=True)`, which the docstring calls "zero cycling is the real, structurally-guaranteed answer". The guided battery would therefore show **0 cycles, marked available**, in Compare. That is not a blank; it is a wrong answer.
- **`cost_breakdown.storage_capex_expansion`** adds up StorageUnit + Store only, so the power CAPEX (on the Links) is left out of "storage" totals.
- **`services/compare/capacity.py`** puts the battery's MW in the Links table under the Link carrier, not under storage MW.
- **`asset_economics`** gives the Store row an `lcos_eur_per_mwh` that covers only energy (e_nom) CAPEX. The plan's pro forma `lcos` will include the power CAPEX, so the two surfaces will disagree about the same battery.
- Store rows carry `discharge_revenue_eur` and `charge_cost_eur`. There is **no `revenue_eur` key** for stores, although S5 reads `asset_economics.revenue_eur`.

**Why it matters.** The plan's rule is to reconcile with the existing surfaces. As written, the battery's economics are split across three rows, one of which prices at the internal battery bus, and one Compare tab states a false zero.

**Change.** Pick one of these and pin it in the plan and in spec §8.1:
- **(a)** Keep Store + Link. Then:
  - add a named `bess_power_ratio` constraint (discharge `p_nom` = charge `p_nom` × efficiency, the PyPSA-Eur convention), with the inverter CAPEX on the charger only and the discharger at 0;
  - give all three components one `carrier="battery"` convention and a shared `bess_group` column;
  - fix `storage_cycling` to cover Stores (energy capacity `e_nom_opt`) and return `available=False`, not zero, when it cannot;
  - define the BESS market revenue as Σ Link `revenue_eur` (both Links) + Store (`discharge_revenue_eur − charge_cost_eur`);
  - add one golden case with a Store + Link pair.
- **(b)** Size with a `StorageUnit` and enumerate the duration (1, 2 and 4 h) as a discrete choice, as spec decision 5 and the co-located template already do.

Either way, add a test that the guided battery appears in capacity, economics and cycling with consistent MW, MWh and CAPEX.

### [B5] With re-solves, the verdict can never be `marginal`: NPV ≥ 0 holds by construction of the LP

**Checked.** This is a proof, not a guess.
- The baseline (`none`: BESS fixed at 0) is a feasible point of the `bess` LP. So the optimum satisfies `objective_bess ≤ objective_none`.
- The bill uses the same energy, demand and export prices as the LP (B2), and the fixed charges are identical in both. So annual bill savings S equal the LP's operating-cost delta, and S ≥ annuity(r_lp, L) × capex + FOM.
- The NPV over L years at r_lp is `−capex + (S − FOM) × AF(r_lp, L) ≥ 0`, because `annuity = 1/AF`.
- The S6 tornado "re-solves the best option at `range_low` and `range_high`". Each re-solve re-sizes the battery, so each NPV is ≥ 0 again (the LP just builds less, or nothing). A sign flip cannot happen, so `marginal` is unreachable. The plan's own TDD line, "red: `marginal` never emitted", would stay red for good, or be made green only by basis mismatches.
- The only way the sign can change is if the ledger's discount rate or horizon differs from the LP's `discount_rate` and `lifetime`. Then the verdict measures a bookkeeping inconsistency, not the economics.

**Why it matters.** Spec decision 6 is defeated. The headline "NPV > 0, recommended" is the LP's own objective restated, and could be read as independent evidence. That is the same trap as the zero-profit reading note.

**Change.**
- **Size once, then evaluate.** The tornado *holds the recommended sizes fixed* (`p_nom` and `e_nom` fixed, extendability off) and re-dispatches at each bound. Spec §5 already allows "re-dispatch with fixed capacities, labelled". Record it as `method=redispatch_fixed_sizes`. NPV can then go negative and `marginal` becomes reachable.
- Pin that the ledger's discount rate and lifetime are written into the LP (`discount_rate` and `lifetime` on each asset, or on `cfg`), and that `horizon_years = BESS lifetime` in MVP-1 (see S2). Pro forma and LP are then on one basis.
- Add the honesty note `npv_nonnegative_at_optimum_by_construction`, and show it on the verdict page next to the NPV.

### [B6] The golden reconciliation equation in S5 counts FOM twice and books investment twice

**Checked.**
- `compute_cost_breakdown` returns `capex` = *annualised investment + FOM* (docstring and the 2026-09-26 FOM fix), with `fom` as the FOM share *already inside* `capex`.
- S5's acceptance, "`Σ years.opex_fixed` equals `cost_breakdown.fom + capex` … times years", is therefore annualised investment + 2 × FOM.
- It also books the annuity as a yearly cost *on top of* the upfront CAPEX that the first S5 bullet puts in the build year. That is the double-charging defect the S5 mutation exists to catch, written into the acceptance itself.
- `cost_breakdown` is system-wide, so its OPEX includes the grid energy bill priced on the import Link. Taking "variable OPEX from `compute_cost_breakdown`" and adding bill savings counts the energy twice.
- `tests/golden/fixture.py` is multi-period (`GOLDEN_PERIODS = (2030, 2035)`, 24 snapshots per period), uses a `StorageUnit` `bess`, and has no `grid_import` Link. `select_import_links` returns `[]` on it. The production shape (flat, 8760 h, Store + Link, tariff) is never reconciled.

**Change.**
- `opex_fixed[y] = Σ over the option's own assets of asset_economics.fom_cost_eur` (annual basis), and `Σ years.opex_fixed = cost_breakdown.fom[option assets] × horizon_years`.
- CAPEX comes only from `upfront_cost_series × size` in the build year. No annuity term appears anywhere in the cash flow.
- OPEX is **asset-scoped** (BESS and PV rows only), never system `cost_breakdown.opex`.
- Add a *second* golden fixture in the production shape (flat year, Store + Link or StorageUnit per B4, `grid_import`/`grid_export` Links, flat tariff) with an oracle NPV.

### [B7] The `/api/studies/{id}` routes cannot use `ProjectAccessDep` as written; the plan as written has an IDOR

**Checked.**
- `routers/deps.py::require_project_access(name: str, …)` authorises a `{name}` **path** parameter. On a route with no `{name}` in the path, FastAPI treats `name` as a required **query** parameter.
- `/api/studies/{id}` would then authorise whatever project the caller names in `?name=` and act on whatever study `{id}` refers to.
- No study_id → project index exists. The plan stores a single fixed-name `study.json` per base project, so a lookup by id has to scan storage directories across tenants.
- `routers/projects.py::_create_scenario_db` copies every file in `_BUNDLE_FILES`. S1 adds `study.json` to that tuple, so **every option fork inherits a copy of the study sidecar**. There would be four studies claiming one id.
- `/api/studies/` is in neither `_FOREIGN_LOCK_GATE_PREFIXES` nor `_SOLVER_BLOCKING_PREFIXES` (`main.py`). The middleware's auth gate does cover all `/api/`, so the new prefix is not unauthenticated, but it gets none of the other gates. This is the "denies by omission" shape that OPEN-ITEMS 6 warns about.
- S1's acceptance expects "403 for a non-member". The house rule is **404** (the `require_project_access` docstring: a 403 is an existence oracle).

**Change.**
- Mount the routes as `/api/projects/{name}/studies/{study_id}/…` with `ProjectAccessDep`. `{study_id}` is looked up *inside* the authorised project's directory, and the sidecar is keyed `studies/<study_id>.json`.
- Every write calls `_check_project_lock(db, _lock_target(project), user)`, which is check-only. Run and abort use `_enforce_project_lock`.
- `_create_scenario_db`, or its lifted `services/study/forks.py`, **excludes** the study sidecar and `results_state.pkl` from fork copies.
- Change 403 to 404.
- Add a route-inventory test that every `routers/studies.py` handler declares `ProjectAccessDep`.

### [B8] S4 has no mutation boundary or runner context; "the base project is never mutated" is false as designed

**Checked.**
- **Forks copy disk, not memory.** `_create_scenario_db` copies `base_dir/_BUNDLE_FILES` from disk. So `build_site_network` has to be *saved into the base project* before any fork exists. That save is a mutation of the base project. If the base project is the user's active, resident context, it is a network swap under `refuse_if_study_running`.
- **Fork needs a request.** `_create_scenario_db(db, user, …)` needs a live DB session and a `User`. A worker thread has neither: it has to open `SessionLocal()` and re-fetch the user by id, because a request's `User` is detached.
- **No precedent.** `eh_study_runner.start_eh_study` does *not* fork and does not use `SolveQueue`. It mutates and reverts the **active** network under `PyPSAService.get_lock()`, inside `contextvars.copy_context()`. There is no existing pattern to copy.
- **Wrong context by default.** `campaign._context()` → `PyPSAService.get_active_context()` → `_request_ctx` ContextVar, falling back to the process foreground `_active`. Without `copy_context`, the runner's `check` and `record` calls charge **another session's** campaign.
- **Wrong frames.** `routers/results.py::_result_df` reads `_state["lopf_results"]` (the *foreground* context) before the live network. `compute_asset_economics(n, cfg, result_df=routers.results._result_df)` run on a fork would read the foreground project's dispatch. The precedent to use is `eh_report._live_result_df`, together with the fork's own config (from the job's `solver_config_json`, not `_state["solver_config"]`).
- **Campaign not wired.** `campaign.CHARGEABLE` has no study kind: `check()` raises `unknown_study` and `estimate_solves` has no branch. `campaign.start` raises `CampaignError` if the copilot already has a campaign open on that context. The module docstring also says the campaign "gates the AGENT … the panels are untouched".
- **Tornado overwrites options.** `SolveQueue._run_solve_job` *saves every successful solve* to the project (`_save_context`). If tornado variants re-solve the option forks, the Expert-view link then shows the last tornado variant, and S7's stale check (option network hash) fires on the study's own tornado.
- **Wrong context for abort.** `_abort_study` and `study_state` read the *active* context's `_state`. If the user switches project during a run, abort and mesh look at the wrong context.

**Change.** Add a "Mutation boundary" section to S4 that pins:
- **M0 (create).** A question-first study *creates a new base project* and saves the pack into it once through `_save_context` on a context that is not the foreground one. An existing user project is never overwritten. "Custom question on an existing project" runs no pack.
- **M1 (run).** Forks are named deterministically (`<study>-opt-<id>`), `scenario_type="scenario"` (only baseline/scenario/stress are valid), and replaced on re-run. Before enqueue, each fork gets an explicit `SolverConfig`: mode lopf, flat, no rolling or SCLOPF, tariff set. Nothing is inherited from the base. The enqueue passes `project_key`, `storage_dir` and `enqueued_by_user_id` from the fork's row.
- **M2 (tornado).** Variants solve on throw-away forks, which are deleted after their NPV is read. Option forks are never re-solved.
- **Context.** The runner takes the base context explicitly. The worker runs under `copy_context()` like `start_eh_study`. Campaign calls happen on that context. A `decision_study` entry goes into `CHARGEABLE` and `estimate_solves`, and a `decision_study` key into `STUDY_KEYS`, `STUDY_LABELS` and `ABORTABLE_STUDIES` (pinned by `test_adequacy_study_swap_guard`). If an agent campaign is already open, record against it; do not start a second one.
- **Results.** Results are read with a live-frame `result_df` and the fork's config.
- **Abort and delete.** Both delete the forks (or mark them), and S9's "restores nothing because nothing was mutated" becomes "no user project changed; study forks cleaned up".

---

## Should-fix

### [S1] Spec deviations without an amendment
- The plan drops native PDF. That contradicts pinned decision 19 and the MVP-1 row in §9 ("DOCX and PDF").
- It pulls forward the maturity badge, ledger CSV round-trip/import and library versions, which spec §9 lists under **MVP-2**.
- It redefines `quick_screen` (B1).

House rule: revised specs keep a revision history. Add a spec revision entry and owner sign-offs to §11, or defer the pulled-forward items.

### [S2] The honesty notes miss the assumptions most likely to inflate the headline

**Checked.**
- `replacements` and `degradation` are deferred ("`not_used_in_mvp1`").
- Battery lifetime is usually shorter than a plausible horizon, and S5 gives no rule tying the horizon to it.
- Perfect foresight on *monthly peaks* is the most generous assumption in the model: the LP knows each month's peak in advance.
- The PV profile comes from `profile_shapes._solar_cf_profile`, a synthetic curve with noise, not a location.
- `profile_shapes.py` has no sector load library (only `_double_peak_profile` and others). "Sector profile" is new work, not reuse.
- Revenue at duals now includes the peak-constraint duals.

**Change.**
- Set `horizon_years ≤ BESS lifetime` in MVP-1, with PV salvage handled.
- Add these honesty notes: `no_degradation`, `no_replacement_within_horizon`, `demand_charge_perfect_foresight` (the verdict's main caveat whenever demand charge is the top stream), `synthetic_pv_profile` and `synthetic_load_profile`, `duals_include_demand_charge`, and `npv_nonnegative_at_optimum_by_construction` (B5).

### [S3] Placement of the demand-charge wrapper relative to the objective scale
**Checked.** `run_simulation` composes, from inner to outer: curtailment → capex budget → ENS cap → reserve margin → **objective scale**. Each wrapper runs `user_fn` first. Anything composed before `_wrap_with_objective_scale` gets scaled; anything composed after it is left unscaled and is then mis-weighted by the scale factor.

A few smaller points:
- `_rescale_results_for_objective` does not rescale custom-constraint duals.
- `_objective_conditioning` (the auto-scale) does not see the demand-charge coefficient.
- PyPSA's `assign_duals` parses a hyphenated constraint name as `Component-attr`.

**Change.**
- Pin the position: immediately after `_wrap_with_capex_budget` and before ENS cap, reserve margin and objective scale.
- Name the variable and constraints without a hyphenated component prefix (`peak_import`, `peak_import_le`).
- Add a test that `user_objective_scale=10` leaves `p_nom_opt`/`e_nom_opt` and `demand_charge_eur` unchanged.

### [S4] The "tariff absent is no-op" test would pass vacuously
**Checked.** `tests/golden/fixture.py::solve_golden_network` calls `n.optimize` directly, not `run_simulation`, so it never builds the wrapper chain. A "byte-identical" pin on that fixture proves nothing about `_wrap_with_demand_charge`. House rule: a passing negative guard proves nothing.

**Change.** Run the no-op test through `run_simulation` with `cfg.tariff=None`. Also run a mutation (make the wrapper always active) and show that the test turns red.

### [S5] Desktop packaging
**Checked.**
- `python-docx` is **not** in `gui-requirements.txt`. The backend imports `docx` nowhere today, so the first unguarded import fails `tests/test_packaging_requirements.py` (or, if guarded, 500s in the frozen app).
- `pypsa-gui.spec` lists `templates/matpower.jinja2` as a *single file*, so `templates/decision_report.html.j2` and `study_library/*` would not be in the bundle.
- `smoke/check_bundle.py::EXPECTED` does not list them either.

**Change.** Pin `python-docx==1.2.0`. Add the template and the library directory to the spec's `datas` and to `EXPECTED`.

### [S6] Stored XSS in the HTML report
**Checked.**
- `routers/io.py` (the only Jinja2 precedent) uses a bare `Environment(...)`, which has autoescape off.
- `jinja2.select_autoescape(['html'])('decision_report.html.j2')` returns **False** (run in the environment), because the check matches on the file suffix.
- The study name, ledger labels and tariff names are user input rendered into an HTML page on the app's own origin.

**Change.**
- Use `Environment(autoescape=True)`.
- Serve `report.html` with `Content-Disposition` from `services/http_filenames.py::content_disposition` and a `Content-Security-Policy: sandbox` header.
- Add a test that a study named `<script>` renders escaped.

### [S7] User-timeseries tenancy (OPEN-ITEMS 1)
**Checked.** `routers/projects.py` foreground save calls `_reapply_user_ts_to_network` from the process-global `_user_ts`. Hydrate/load calls `_restore_user_ts`. The plan never says where an intake load upload lands.

**Change.**
- The pack writes `loads_t.p_set` directly and never touches `_user_ts`. The upload goes through `routers/uploads.py` (project attachments, `ProjectAccessDep` + lock) and is parsed by the pack.
- The study routes refuse in auth (multi-user) mode until item 1 is fixed or contained, and a test asserts the refusal.

### [S8] Golden-matrix registration
**Checked.** `tests/golden/coverage.py::SURFACES` and `tests/test_golden_coverage.py::ROUTE_FILES` cover only results, simulation, compare and asset_results. `tests/test_bundle_sidecars.py` walks only `services/adequacy/` for `SIDECAR_NAME`.

**Change.**
- Add `investment_case` (and `investment_case_xlsx`) to `SURFACES`.
- Add `routers/studies.py` to `ROUTE_FILES`.
- Extend the sidecar walk to `services/study/`.

Without these, the "tenth surface" is outside the matrix it claims to join.

### [S9] The XLSX NPV check can agree with itself and still disagree with Excel
**Checked.**
- Excel's `NPV()` discounts the *first* value one period, so `=NPV(r, CF0:CFn)` differs from a Python NPV where year 0 is not discounted.
- No formula evaluator is installed (`formulas`, `pycel` and `xlcalculator` are all absent), so the "pure-Python evaluation" would be hand-written by the same author.

**Change.**
- Pin the formula as `=CF0 + NPV(rate, CF1:CFn)`.
- The evaluator implements Excel's semantics, including a test that `=NPV(r, A1:A3)` with A1 in period 1 matches Excel's documented example.

### [S10] "Study" collides with three existing meanings
**Checked.**
- `NewProjectWizard` already has a `'study'` tab: the gridspine planning → dynamics study, with `NewProjectWizard.study.test.tsx` and `ProjectsHomePage.kind.test.tsx`.
- `STUDY_KEYS` in `services/project_context.py` means adequacy studies.
- `CONTEXT.md` has no entry for "study".
- `ProjectsHomePage.START_ACTIONS` maps cards 1:1 to wizard tabs, and the workbench relies on `initialTab='blank'`.

**Change.**
- Add a `CONTEXT.md` entry for **Decision study**, with the other meanings under "_Avoid_".
- Use distinct ids: `decision_study`, `/studies` under projects, SlidePanel `decision`.
- Add the question cards as a **new** tab, or as a landing only from ProjectsHomePage. Keep the `'blank'` default for the workbench, so the existing wizard and home tests stay unchanged.

### [S11] Fork lifecycle
Re-running a study hits `409 "Project … already exists"` from `_create_scenario_db`. Forks appear in the user's project list and scenario tree. `results_state.pkl` is copied into each fork. The queue panel and AppHeader toasts will announce each option solve.

**Change.** Pin naming and re-run replacement (see B8), cascade on study delete, show forks as study-owned in the list, and decide whether queue toasts are suppressed for study jobs.

---

## Nits

- **[N1] Wrong path.** `modelHorizonModel.ts` is at `frontend/src/pages/modelHorizonModel.ts`, not under `pages/modelHorizon/`.
- **[N2] Wrong reference.** `test_adequacy_campaign.py` has no fake solver. The fakes are in `tests/test_adequacy_abort.py`, `test_study_mesh_claim.py` and `test_solve_jobs_table.py`. Name the right one.
- **[N3] S0 unit badges.**
  - The Store capital badge is currently `€/MWh`, not `€/MWh/yr`, so "stays" is wrong: it must change too.
  - Line and Transformer capital badges (`€/MVA`) are annuities and need `/yr` as well.
  - The `PropertiesPanel` quick-add input `'Capital cost (€/MW)'` needs the same fix.
- **[N4] Digit rule too broad.** `validate_prose` as specified ("any digit outside `{{fact_id}}`") rejects `CO2`, `H2`, `N-1` and `24/7`. Match numeric tokens (`(?<![A-Za-z])\d`) and keep a unit allowlist.
- **[N5] SVG to PNG.** python-docx cannot embed SVG, and rasterising SVG needs cairosvg, which is absent. Save PNG straight from the same matplotlib figure (`Agg`). Also, `gui-requirements.txt` pins matplotlib 3.10.9 while pixi has 3.11.2.
- **[N6] Tornado inputs.** The tornado runs over four drivers, not five, because degradation is `not_used`. How the "energy price spread" is perturbed on a time-of-use tariff is undefined; pin it (scale around the time-weighted mean).
- **[N7] Report helpers.** `study_report._disclosures` always starts with an adequacy sentence ("a screening convolution, an LP proxy and a sampler"). `_not_established` filters `status == "no_data"`, not `not_established`. The lift needs a mapping and a disclosure set specific to the BESS report.
- **[N8] Nav label.** `StepShell` hard-codes `aria-label="Model horizon steps"`, which `ModelHorizon.render.test.tsx` queries five times. The generalisation must keep it as the Model Horizon default (`navLabel` prop).
- **[N9] Surface count.** `coverage.SURFACES` already has more than nine ids, so "tenth surface" is loose wording.
- **[N10] Price base year.** Ledger rows need a `currency_year` (technology-data v0.14.0 is in EUR of a stated base year). "Real" has no meaning without one.
- **[N11] Import link selection.** `select_import_links(n, overlay)` needs an `ImportOverlaySpec`. The pack must not tag the site bus `eh_poc`, because the poc fallback would then pick up the BESS Links. Assert exactly `['grid_import']`.
- **[N12] Flat network.** `period_utils.active_period_years` returns `None` on a flat network, and `asset_economics` emits `by_period` only when `is_multi`. The pro forma needs an explicit flat-network path.
- **[N13] Grid bus.** The pack never says what supplies `grid_import` and absorbs `grid_export` at the grid bus. Pin a zero-cost Generator with `p_min_pu=-1`, and validate that the export price stays below the import price in every hour, so import-to-export cycling cannot pay.

---

## What is solid

- **Shipped claims check out.** Every "already shipped" function exists with the behaviour described:
  - `_wrap_with_capex_budget` (constraint-only; it adds nothing to the objective, so the curtailment wrapper's `n.model.objective +=` is the precedent for an objective term);
  - `select_import_links` (role → poc → carrier);
  - `campaign.start/check/record/end`;
  - `start_eh_study`;
  - `create_scenario`/`_create_scenario_db`;
  - `study_report._disclosures/_not_established/_evidence_gaps`;
  - `upfront_cost_series`, `_annuity`, `_pv_factor_series`, `periodized_capital_costs`;
  - `tests/golden/{fixture,oracle}.py`;
  - `StepShell.tsx`;
  - `explain_investment` and its zero-profit note.
- **S3 is feasible in the pinned stack.** Adding a variable, a constraint and an objective term through `extra_functionality` works in PyPSA 1.1.2 / linopy 0.8.0 (`n.model.add_variables`/`add_constraints`, `Link-p` with dims `(snapshot, name)`). A new `SolverConfig` dataclass field survives the queue snapshot (`_solver_config_from_dict` filters to live fields) and the partial PUT (`update_solver_config` merges with `exclude_unset`).
- **S4's queue side is feasible.** `SolveQueue._run_solve_job` hydrates its own `ProjectContext` from `job.storage_dir` and solves off the foreground, so a runner can enqueue forks without a request, provided B8's context rules are followed. Contexts with a running study are protected from eviction (`_evict_if_over_cap`).
- **S0 targets are real and cheap.** All verified in source: the `€/MW` annuity badges, `$/MWh` in `CreationForm`, and the literal `{'max_solves'}` in `MarginLoopPanel`.
- **Libraries are present.** python-docx 1.2.0, openpyxl 3.1.5, Jinja2 3.1.6 and matplotlib 3.11.2 are installed, and `backend/templates/` exists.
- **The structure is in house style.** Phase gates, the TDD protocol, mutation checks, null-not-zero, `engine`/`basis` labels, per-step apply (consistent with Model Horizon's "no transactional wizard"), and "no second engine" are all in place. The non-goals in the spec match the FMEA, EH and trustworthy-numbers deferrals.
