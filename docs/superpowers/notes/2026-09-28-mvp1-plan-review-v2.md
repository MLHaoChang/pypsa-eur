<!-- Independent re-gate of plan v2, produced 2026-09-28 by the same delegated reviewer; the reviewer built a toy network in the v2 pack shape and solved it for 8760 h through run_simulation on PyPSA 1.1.2 / linopy 0.8.0 to check the B2, B5, B6 and StorageUnit claims. Verdict: GO WITH BINDING CONDITIONS (BC-1 to BC-7), recorded in plan v2 § Review deltas (v2 re-gate). -->

# Re-gate: Guided investment study, MVP-1 plan v2

**Plan:** `docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md`
**Spec amendments:** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md` §13
**Review v1:** `docs/superpowers/notes/2026-09-28-mvp1-plan-review-v1.md`

**Method.** Same protocol as v1: read-only, and every new v2 claim checked in source. I also solved a toy network built to the v2 pack shape (buses `grid`/`site`, a zero-cost grid Generator with `p_min_pu=-1`, `grid_import`/`grid_export` Links with a time-varying `marginal_cost`, an extendable `StorageUnit` priced through `overnight_cost`). It was 8760 h, run through `run_simulation` on PyPSA 1.1.2 / linopy 0.8.0 / HiGHS, with `compute_cost_breakdown`, `compute_objective_decomposition`, `compute_asset_economics` and `upfront_cost_series` read afterwards. The script is at `scratchpad/v2/toy.py`.

## Verdict: **GO WITH BINDING CONDITIONS**

All 8 v1 blockers are resolved in design. Where v2 makes a new claim, it holds in source, with the exceptions below.

The re-gate found **two plan-text defects that would stop the flow at its first solve**: S1-v2 (the `none` option) and S2-v2 (a missing size bound). It also found five more should-fixes, S3-v2 to S7-v2, that are wrong as worded. None needs a design change: each is a local amendment to one phase. They are binding conditions **BC-1 to BC-7**. Apply them to the plan text before S1 starts, and check each at the gate of the phase named.

---

## 1. Do the v1 blocker resolutions hold?

| v1 | Holds? | What I checked |
|---|---|---|
| **B1** | Yes | Spec §13 amends decision 14. The refusal list uses the real field names: `SolverConfig.solve_strategy ∈ {full, rolling, myopic}`, `sclopf`, `multi_investment_periods`. A flat 8760 h site LP is small: 131k rows before presolve, solved in about 14 s end to end in this container. |
| **B2** | **Yes, measured.** | With the prices as permanent `links_t.marginal_cost`, `n.statistics()` counts the import energy as Link OPEX. On the toy, `cost_breakdown.opex` was 31,604,934, all on `Link`, and the export credit was netted. `objective_decomposition` gave `gap_eur = -1.2e-06` and `residual_gap_pct = -1.1e-11`: the bridge closes exactly with no demand charge. With the demand charge on, the objective carries a term that `n.statistics` does not model, so v2's named `demand_charge_eur` term in `_bridge` is necessary and is enough. |
| **B3** | Yes | The static `capital_cost` column stays `0.0` and `overnight_cost` is honoured. `upfront_cost_series(n, "StorageUnit")` returned `650000.0` (= 150 €/kW × 1000 + 2 h × 250 €/kWh × 1000). The accessor `n.c["StorageUnit"].capital_cost` returned `71366.5` = 650,000 × annuity(7 %, 15). See N1 on how the acceptance is worded. |
| **B4** | Yes | `fill_periodized_cost_defaults` iterates `storage_units` in its FOM, direct-`capital_cost` and `discount_rate`/`lifetime` passes. `upfront_cost_series` is `n.c[comp_class].overnight_cost`, so it covers any class, including StorageUnit. `compare/storage_cycling`, `compare/capacity` (storage MW and MWh) and `cost_breakdown.storage_capex_expansion` all cover StorageUnit. |
| **B5** | Yes in principle; see S1-v2 and S6-v2 | With `p_nom_extendable=False` and `p_nom=p_nom_opt`, the re-dispatch LP has no sizing freedom. A perturbed input can therefore push savings below the fixed CAPEX + FOM, and the NPV can go negative. For the battery-only options no basis mismatch remains: the ledger rate and lifetime go into the LP, the horizon equals the battery lifetime, CAPEX is taken in year 0, the annuity is PyPSA's end-of-year one, and the bill uses the same prices as the LP. One mismatch remains for `bess_pv` (S6-v2). |
| **B6** | Yes | On a flat network, StorageUnit rows carry `fom_cost_eur` and `vom_cost_eur`: 250,000 (= 5,000 €/MW/yr × 50 MW × 1) and 0.0, with `by_period = []`. `cost_breakdown.fom = 250000.0` matches. |
| **B7** | **Mostly.** See S3-v2 and S4-v2. | Nesting works: `include_router(r, prefix="/api/projects/{name}/studies")` with a `Depends(require_project_access)` that takes `name` resolves `name` from the path. I checked this with FastAPI's TestClient. There is no collision with `projects.router`: its `/{name}` catch-alls are one segment, `/{name}/...` routes are static. Two claims do not hold: the plan's prefix-list wording (S3-v2), and the storage container for `studies/` (S4-v2). |
| **B8** | **Feasible.** See S5-v2. | M0 does not need the foreground context. The chain is: `project_registry.create_root(db, user, name)` → `project_registry.ensure_project_dir(row)` → `PyPSAService.build_context()` (a fresh, unbound, unregistered context with its own `mutation_lock`) → set `ctx.network` to the pack and `ctx.solver_state["solver_config"]` to the fork config → `routers/projects.py::_save_context(ctx, name, project_row=row, storage_dir=dir, persist_user_ts=False, db=db, user=user)`. Because the context is unbound, `_save_context`'s first-save claim calls `project_registry.bind_context` and `PyPSAService.rekey_context`. That registers the new context as resident under `org:uuid` but leaves the request's own slot untouched: `rekey_context` only moves `_request_slot` when `_request_ctx.get() is ctx`. However, v2 names "the queue's hydrate/save path" as the precedent. That path hydrates an *existing* project and never calls `create_root`. |

## 2. The StorageUnit choice

Verified as described in rows B3 and B4. **Two things the pack must add, found by running the pack shape through `run_simulation`:** see S1-v2 and S2-v2.

## 3. Remaining findings

### [S1-v2] (binding, BC-1, S4 and S6) A size fixed at 0 fails preflight, so the baseline never solves
**Checked.**
- `services/validation_service.py::_check_extendable_bounds` returns an **error** (`storageunit_p_nom_invalid`: "p_nom must be > 0 when p_nom_extendable=False") for any non-extendable asset with `p_nom = 0`. It applies the same rule to Generators, Links and Stores.
- I reproduced it with `validate_for_run` on a StorageUnit with `p_nom_extendable=False, p_nom=0`.
- The `none` option ("zero storage", following spec §5's "`p_nom` at existing or 0") would therefore abort with `validation_failed`, and so would every option after it.
- A tornado fork whose `p_nom_opt` is 0 fails the same way.

**Change.**
- The `none` option **omits** the StorageUnit (and PV) from the fork rather than fixing them at 0. Spec decision 4 already allows "or absent".
- The tornado skips an option whose `p_nom_opt ≤ ε`: its NPV is 0 at every bound and needs no solve.
- Add a runner test that `none` passes `validate_for_run`.

### [S2-v2] (binding, BC-2, S4) Extendable assets need a finite `p_nom_max`
**Checked.** The same `_check_extendable_bounds` rule returns an **error** `storageunit_p_nom_bounds` when `p_nom_max = inf`, the PyPSA default. It failed the first toy solve. The PV Generator has the same rule. The pack sets no bound, and whatever bound it picks becomes part of the answer. On the toy, the LP built exactly at `p_nom_max = 50`, where `explain_investment` reads `at_upper_bound` rather than an interior optimum.

**Change.**
- The pack writes `p_nom_max` from a ledger row, for example the connection limit × a stated multiple, with source and status.
- The verdict carries a `size_at_upper_bound` caveat when the bound binds, reusing the existing `_sizing` classification. An NPV at a bound is not an optimum.
- Pack test: `validate_for_run(n, cfg)` returns no errors for every option.

### [S3-v2] (binding, BC-3, S1) "Add the prefix to both prefix lists" cannot be done, and would be wrong
**Checked.**
- Both lists in `main.py` are matched with `path.startswith(p)`, so a templated `/api/projects/{name}/studies` cannot be added.
- `/api/projects/` is **already** in `_SOLVER_BLOCKING_PREFIXES`. As shipped, every study write therefore gets 409 `solver_in_flight` whenever the *active* project's worker is alive (`routers/simulation.py::_solver_in_flight` reads the active `_state`). That includes `POST .../run/abort`. Yet study writes never touch the resident network.
- `_FOREIGN_LOCK_GATE_PREFIXES` checks the **active** project's lock, which is the wrong project for a path-scoped route. The per-route authorisation audit left `/api/projects/` out of that list on purpose, and `tests/test_chat_tools_lock_gate_parity.py` pins it equal to `chat_tools._LOCK_GATE_PREFIXES`.

**Change.**
- Add the study routes to **neither** list.
- Keep the in-handler `_check_project_lock`/`_enforce_project_lock` against the **path** project, as v2 already says.
- Add a pattern exemption for `^/api/projects/[^/]+/studies(/|$)` to the solver-blocking gate. Today it has only exact and suffix forms, so extend it the way `_FOREIGN_LOCK_GATE_EXEMPT_PATTERNS` already does. Add a test that a study `PATCH` succeeds while the active project is solving.
- Also mount the router with `dependencies=_projects_router_guard` (`fs_permission.require_file_access`), like the worksheet and projects routers, because it reads and writes under the projects root.

### [S4-v2] (binding, BC-4, S1) `studies/` is a directory; `_BUNDLE_FILES` is a tuple of files
**Checked.**
- Every consumer of `_BUNDLE_FILES` does `src_file.read_bytes()` or the equivalent: `_create_scenario_db`, bundle export, snapshots in `routers/snapshots.py`, and the tmp-file probe. A directory entry there fails.
- Directories go in `_BUNDLE_DIRS` (today `("uploads",)`). `_copy_bundle_dirs` copies them in `create_scenario`, Save-As and snapshot create, and `export_bundle` walks them.
- `tests/test_bundle_sidecars.py` asserts that `SIDECAR_NAME ∈ _BUNDLE_FILES`.

**Change.**
- Put `studies` in `_BUNDLE_DIRS`.
- `services/study/forks.py` skips it (and `results_state.pkl`) explicitly. Do not reuse `_copy_bundle_dirs` unchanged.
- The sidecar test for `services/study/` asserts membership in `_BUNDLE_DIRS`.
- Add a snapshot restore test for a study directory.

### [S5-v2] (binding, BC-5, S4) Name M0's functions and make publishing and the mesh context-parameterised
**Checked.**
- M0 is feasible with the chain in row B8, but v2 names the wrong precedent.
- `routers/results.py::_publish_study` takes `PyPSAService.get_lock()` and writes `_state`, both of the **active** context.
- `_refuse_if_mesh_busy` → `_study_mesh_blocker` reads `study_state` and `_solver_in_flight()`, also the active context.
- If the base project is not the active one, the 409 check and the claim look at the wrong project.
- `tests/test_adequacy_study_swap_guard.py::test_abortable_studies_matches_the_routes_that_actually_exist` derives the abort routes from `routers/results.py` by regex (`@results_router.post("/(\w+)/abort")`). Adding `decision_study` to `ABORTABLE_STUDIES` while the abort route lives in `routers/studies.py` turns that test red.

**Change.**
- Name the M0 chain in the plan and pin `persist_user_ts=False`.
- Do the first-save claim inside `PyPSAService.hydrate_or_adopt(key)`, the documented lock order.
- Implement a context-parameterised claim: `running_study_key(base_ctx.solver_state)` and `_solver_in_flight_ctx(base_ctx)`, checked and published under `base_ctx.mutation_lock` → `base_ctx.solver_state_lock`, the same shape as `_publish_study`.
- Extend the route-equality test to scan `routers/studies.py` for the decision-study abort route.

### [S6-v2] (should-fix) Market revenue key; the `bess_pv` basis; the tornado baseline bill
**Checked.**
- **No `revenue_eur` on StorageUnit rows.** The keys are `discharge_revenue_eur`, `charge_cost_eur`, `vom_cost_eur`, `fixed_cost_eur`, `fom_cost_eur`, `net_profit_eur`, `lcos_eur_per_mwh`, `spread_eur_per_mwh`, and others. v2's "Already shipped" line and S5's `asset_economics.revenue_eur` are wrong. Market revenue at duals for the battery is `discharge_revenue_eur − charge_cost_eur`.
- **`bess_pv` basis.** PV lifetime (typically 25 years) exceeds the horizon (the battery lifetime). A straight-line salvage then differs from the present value of PV's remaining annuities, which is what the LP implicitly charged. So `npv_nonnegative_at_optimum_by_construction` is only exact for battery-only options, and the `bess_pv` NPV has a small basis gap.
- **Tornado baseline bill.** Energy-price and demand-charge perturbations change the **baseline** bill too.

**Change.**
- Use the correct key.
- Either value the salvage as the annuity present value (which keeps one basis) or label `salvage_straight_line` and scope the by-construction note to battery-only options.
- Pin that the tornado recomputes `bill_baseline` with `BillCalculator` at the perturbed tariff. No solve is needed, because the baseline has no flexible asset.

### [S7-v2] (binding, BC-6, S1; OPEN-ITEMS 1) The containment is sufficient only if the flag is a hard refusal
**Checked.**
- The study's own saves avoid `_user_ts`: M0 uses `persist_user_ts=False`, forks are copied on disk, and queue saves of non-foreground contexts pass `persist_user_ts=(ctx is PyPSAService._active)`, which is False.
- But the **Expert view**, which is one click away by design, activates the option fork, and the fork is then saved by the ordinary foreground `save_project` → `_save_context(..., persist_user_ts=True)`. That calls `_backup_network_ts_to_user_ts` and `_reapply_user_ts_to_network` against the process-global `_user_ts`.
- A queue solve of a fork that is resident *and* the process `_active` also saves with `persist_user_ts=True`.
- So v2's global constraint ("study projects are saved through a path that does not call `_reapply_user_ts_to_network`") is true for study-initiated saves only.
- In local, single-user mode this is the same exposure as any project and acceptable. In auth mode it is the exact cross-tenant path OPEN-ITEMS 1 describes, now carrying client meter data.

**Change.**
- In auth mode the study routes refuse **unconditionally** until OPEN-ITEMS 1 is closed. `PYPSAGUI_DECISION_STUDIES=1` may enable them only in local mode or in tests. Document that it is not an operator override.
- Narrow the global-constraint wording to "study-initiated saves".
- The refusal test must cover the flag set in auth mode.

### [S8-v2] (binding, BC-7, S5) State the reconciliation target by asset, not by class
**Checked.** `cost_breakdown.fom` breaks down per class (`by_component`) and per carrier (`by_carrier`), not per asset, so `cost_breakdown.fom[option assets]` has no direct reading.

**Change.** Reconcile to `Σ asset_economics.fom_cost_eur` over the option's assets, which is per-asset and verified equal to `cost_breakdown.fom` on the toy. Cross-check against `by_component["StorageUnit"].fom` plus the PV carrier row in `by_carrier`.

---

## Nits

- **[N1-v2]** "`capital_cost` is derived (absent before the fill)": the static column is `0.0` both before and after the fill (PyPSA ignores it for overnight-priced assets). Assert on the accessor instead: `n.c["StorageUnit"].capital_cost == overnight × annuity(r, L) × nyears`.
- **[N2-v2]** The zero-cost `grid_supply` Generator triggers the preflight warning `gen_zero_costs` ("Result will be indeterminate") on every study. Either suppress it for an asset tagged `eh_role=grid_supply`, or keep it out of the plain-language preflight list.
- **[N3-v2]** Set `cyclic_state_of_charge=True` on the StorageUnit, so a one-year extrapolation cannot bank a free starting charge or finish the year depleted.

## Contradictions, rebuilds, feasibility (question 3)

- **Pinned decisions.** No new contradiction beyond what §13 amends. The ENS-cap / reserve-margin ordering and Model Horizon's "no transactional wizard" are respected. Decision 4 ("fixed … or absent") supports BC-1.
- **Rebuilds.** None. The runner reuses `SolveQueue`, `campaign`, `study_state`, `_save_context`, `_live_result_df` and `upfront_cost_series`.
- **Feasibility in PyPSA 1.1.2 / linopy 0.8.0.** Every mechanism v2 relies on is present:
  - variable, constraint and objective term through `extra_functionality`;
  - time-varying Link `marginal_cost` counted in `statistics` OPEX;
  - StorageUnit `overnight_cost` annuitised;
  - `assign_duals` skips names without a component prefix;
  - a path-parameter router prefix.

## Binding conditions (record under § Review deltas; each checked at the named gate)

- **BC-1** (S4, S6): `none` omits the assets instead of fixing them at 0; the tornado skips options with zero size; add the `validate_for_run` test.
- **BC-2** (S4): `p_nom_max` comes from a ledger row; a `size_at_upper_bound` caveat is added; there is a no-preflight-error test for every option.
- **BC-3** (S1): study routes go in neither prefix list; a pattern exemption is added to the solver-blocking gate; `require_file_access` is added as a router dependency; add the test that a study PATCH succeeds while the active project is solving.
- **BC-4** (S1): `studies` goes in `_BUNDLE_DIRS`; forks skip it; the sidecar test is adapted; add the snapshot restore test.
- **BC-5** (S4): the M0 chain is named, with `persist_user_ts=False` and `hydrate_or_adopt`; the mesh check and claim are context-parameterised; the `ABORTABLE_STUDIES` route test is extended.
- **BC-6** (S1): the auth-mode refusal is unconditional until OPEN-ITEMS 1 closes; the constraint wording is narrowed.
- **BC-7** (S5): the reconciliation target is `Σ asset_economics.fom_cost_eur`; the market-revenue key is corrected; the `bess_pv` salvage basis and the tornado baseline bill are pinned (S6-v2).

S0 remains cleared to proceed.
